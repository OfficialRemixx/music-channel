"""Third real CC source: Openverse (api.openverse.org), the
WordPress/Creative-Commons-run index of openly-licensed media.

EVERYTHING BELOW WAS VERIFIED AGAINST THE LIVE API ON 2026-10-19
(master-prompt rule 0.2), not just against docs -- the first
implementation trusted the docs and was wrong in three separate ways,
all of which a real A1c run then exposed:

1. "No API key needed, anonymous just gets a lower rate limit" was WRONG
   -- the live API returns 401 Unauthorized. Auth is mandatory: register
   an application once (POST /v1/auth_tokens/register/), then exchange
   client_id/client_secret for a 12-hour bearer token via
   POST /v1/auth_tokens/token/ (OAuth2 client_credentials). That token
   goes in an Authorization: Bearer header on every search.
2. Multi-word queries are ANDed, not OR'd. Passing the whole tag string
   ("lofi chill ambient instrumental") as one `q` returned 2 results;
   a single tag ("lofi") returned 240. So this queries ONE TAG AT A TIME
   and merges -- see _TAG splitting in fetch_candidates().
3. `category=music` silently excludes almost all Wikimedia content (it's
   simply not populated there) -- with it, source=wikimedia_audio
   returned 0 results; without it, 118. So no category filter is sent;
   junk is filtered client-side instead (see below).

WHY source=wikimedia_audio ONLY: the live /v1/audio/stats/ endpoint
shows Openverse's audio index is exactly three sources -- jamendo,
freesound and wikimedia_audio. We already query Jamendo and Freesound
*directly* (cc_library.py, freesound_source.py), so letting Openverse
return those too would just re-fetch tracks we already have and burn the
batch on near-duplicates. Wikimedia Commons (~3.98M items) is the one
pool the other two sources can't reach, so that's the only thing asked
for here. That makes this source genuinely additive rather than
redundant.

Client-side filtering (because the API can't express it):
  - `duration` is in MILLISECONDS here (a 228s track reports 228000),
    unlike Freesound's seconds. Short items are dropped.
  - Wikimedia audio is full of one-second dictionary pronunciation
    clips; those carry category == "pronunciation", so they're dropped.
  - license is re-checked from `license_url` with the shared
    cc_common.license_from_ccurl(), same as every other source.

Confirmed response fields (read off a live result, not assumed):
  id, title, creator, url, foreign_landing_url, license, license_version,
  license_url, provider, source, category, duration, filetype, filesize,
  attribution, audio_set, tags.
"""
from __future__ import annotations

import os

import requests

from common.logging_setup import get_logger

from .cc_common import (
    MAX_SOURCE_DURATION_S,
    MIN_SOURCE_DURATION_S,
    fetch_results_with_retry,
    has_forbidden_marker,
    license_from_ccurl,
    rotating_tags,
)

log = get_logger(__name__)

OPENVERSE_AUDIO_URL = "https://api.openverse.org/v1/audio/"
OPENVERSE_TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"

# Derived from qc.py's real floor (via cc_common), NOT hand-picked -- and
# converted to ms, because Openverse reports duration in MILLISECONDS
# while Freesound reports seconds. The first version hardcoded a 20s
# floor, which on Freesound let through tracks QC then rejected; same
# constant now drives all three sources.
MIN_DURATION_MS = int(MIN_SOURCE_DURATION_S * 1000)
MAX_DURATION_MS = int(MAX_SOURCE_DURATION_S * 1000)
SKIP_CATEGORIES = {"pronunciation"}  # Wikimedia's dictionary audio, never music


def _access_token() -> str | None:
    """Exchanges the registered app's client credentials for a bearer
    token (valid ~12h; every A1c run is a fresh process, so one fetch per
    run is fine). Returns None -- never raises -- if the credentials
    aren't set or the exchange fails, so this source just sits out."""
    client_id = os.environ.get("OPENVERSE_CLIENT_ID")
    client_secret = os.environ.get("OPENVERSE_CLIENT_SECRET")
    if not (client_id and client_secret):
        log.info("openverse_source: OPENVERSE_CLIENT_ID/SECRET not set, skipping this source")
        return None
    try:
        resp = requests.post(
            OPENVERSE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        log.exception("openverse_source: token exchange failed")
        return None


def _usable(item: dict) -> bool:
    """One place for every reason a live result gets rejected."""
    if (item.get("category") or "").lower() in SKIP_CATEGORIES:
        return False
    duration = item.get("duration")
    if duration is None or not (MIN_DURATION_MS <= duration <= MAX_DURATION_MS):
        return False
    if not item.get("url"):
        return False
    license_url = item.get("license_url")
    if license_from_ccurl(license_url) not in {"CC0", "CC-BY"} or has_forbidden_marker(license_url):
        return False
    return True


def fetch_candidates(cfg: dict, limit: int) -> list[dict]:
    """Returns candidates in the same shape as the other two sources.
    Never raises -- a failure here just means 0 tracks from Openverse
    this run, same pattern as freesound_source."""
    token = _access_token()
    if not token:
        return []

    tags = rotating_tags(cfg)
    headers = {"Authorization": f"Bearer {token}"}

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    scanned = 0
    for tag in tags:  # one tag per request -- see module docstring note 2
        if len(candidates) >= limit:
            break
        params = {
            "q": tag,
            "license": "cc0,by",
            "license_type": "commercial,modification",  # we remix, and the channel is monetized
            "source": "wikimedia_audio",  # the one pool Jamendo/Freesound can't reach
            "page_size": 50,
        }
        results = fetch_results_with_retry(OPENVERSE_AUDIO_URL, params, headers=headers)
        scanned += len(results)
        for item in results:
            if len(candidates) >= limit:
                break
            if not _usable(item):
                continue
            url = item["url"]
            if url in seen_urls:  # the same track can match several tags
                continue
            seen_urls.add(url)
            candidates.append({
                "title": item.get("title") or "Untitled track",
                "artist": item.get("creator") or "unknown artist",
                "download_url": url,
                "license": license_from_ccurl(item.get("license_url")),
                "license_url": item.get("license_url"),
                "source_url": item.get("foreign_landing_url") or url,
                "provider": "openverse/wikimedia",
            })

    log.info("openverse_source: %d/%d candidates passed filters across %d tag(s)",
             len(candidates), scanned, len(tags))
    return candidates
