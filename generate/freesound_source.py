"""Second real CC source, added 2026-10-19 alongside Jamendo (per user
request to bring in more than one source): Freesound.org.

Verified against https://freesound.org/docs/api/overview.html and
https://freesound.org/docs/api/resources_apiv2.html (master-prompt rule
0.2, 2026-10-19):
  - Base URL: https://freesound.org/apiv2 . Search: GET /apiv2/search/
    (the old /apiv1/search/text path now redirects here), auth via a
    `token` query param -- a free API key from
    https://freesound.org/apiv2/apply (NOT OAuth2; OAuth2 is only needed
    for original-quality downloads, uploads, and a few other endpoints
    this doesn't use).
  - License filter is a human-readable label, not a URL:
    filter=license:("Attribution" OR "Creative Commons 0") -- the docs
    don't give a documented way to explicitly exclude NC/ND/SA values,
    so this allow-list (rather than a negation) is how those get
    excluded. category:Music narrows away sound-effect/field-recording
    results.
  - Response `license` field is itself a creativecommons.org URL (not
    the human label used for filtering), so it's re-checked locally with
    the same cc_common.license_from_ccurl() used for Jamendo -- same
    don't-trust-the-request-params philosophy.
  - Download: uses `previews.preview-hq-mp3` (128kbps-ish, no OAuth2
    required) rather than the `download` field (original quality,
    OAuth2-gated) -- plenty good for a background lofi mix.
  - `duration` (seconds) is filtered client-side for a sane music-track
    length, since the docs don't confirm a reliable pack/loop-vs-track
    filter.

Freesound's attribution URL pattern (https://freesound.org/people/
<username>/sounds/<id>/) is a long-stable, documented convention, not
guessed per-request.
"""
from __future__ import annotations

import os

import requests

from common.logging_setup import get_logger

from .cc_common import has_forbidden_marker, license_from_ccurl

log = get_logger(__name__)

FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/"
MIN_DURATION_S = 20   # below this it's almost certainly a short SFX, not usable background music
MAX_DURATION_S = 400  # sane upper bound before the per-track trim step in cc_library.py


def fetch_candidates(cfg: dict, limit: int) -> list[dict]:
    """Returns a list of candidate dicts in the same shape as
    generate/cc_library.py's Jamendo candidates (title, artist,
    download_url, license, license_url, source_url, provider).
    Returns [] (never raises) if FREESOUND_API_KEY isn't set, so a
    missing key for this one source doesn't take down the whole A1c run.
    """
    api_key = os.environ.get("FREESOUND_API_KEY")
    if not api_key:
        log.info("freesound_source: FREESOUND_API_KEY not set, skipping this source")
        return []

    lane_tags = cfg.get("music", {}).get("cc_library", {}).get("tags", "lofi chill ambient instrumental")
    params = {
        "query": lane_tags,
        "token": api_key,
        "filter": 'category:Music license:("Attribution" OR "Creative Commons 0")',
        "fields": "id,name,username,license,duration,previews,type",
        "page_size": min(limit * 3, 150),  # 150 = Freesound's documented max page_size
    }
    try:
        resp = requests.get(FREESOUND_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        log.exception("freesound_source: search request failed")
        return []

    candidates = []
    for item in results:
        duration = item.get("duration")
        if duration is None or not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
            continue

        license_url = item.get("license")  # Freesound's response field is itself a CC URL
        license_str = license_from_ccurl(license_url)
        if license_str not in {"CC0", "CC-BY"} or has_forbidden_marker(license_url):
            continue

        preview_url = (item.get("previews") or {}).get("preview-hq-mp3")
        if not preview_url:
            continue

        sound_id = item.get("id")
        username = item.get("username") or "unknown artist"
        candidates.append({
            "title": item.get("name") or f"freesound-{sound_id}",
            "artist": username,
            "download_url": preview_url,
            "license": license_str,
            "license_url": license_url,
            "source_url": f"https://freesound.org/people/{username}/sounds/{sound_id}/",
            "provider": "freesound",
        })
        if len(candidates) >= limit:
            break

    log.info("freesound_source: %d/%d candidates passed filters", len(candidates), len(results))
    return candidates
