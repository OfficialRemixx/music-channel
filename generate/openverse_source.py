"""Third real CC source, added 2026-10-19 alongside Jamendo and
Freesound: Openverse (api.openverse.org), the WordPress/Creative-Commons
-run search index that aggregates CC-licensed audio across many
upstream providers (Jamendo, Freesound, Wikimedia Commons audio, and
others) behind one API with one consistent license filter -- this is
the "another, genuinely different source" leg: its Wikimedia Commons
coverage is real public-domain/CC material that isn't on Jamendo or
Freesound at all.

Verified against the official OpenAPI spec and client docs
(master-prompt rule 0.2, 2026-10-19 -- see
https://jentic.com/apis/api.openverse.org/openverse and
https://apis.io/apis/openverse/openverse-audio-api/):
  - Base URL: https://api.openverse.org/v1 . Search: GET /v1/audio/ .
  - No API key is required for basic anonymous search (lower rate
    limit than an authenticated app, which isn't needed at this
    pipeline's batch size of one search request per run).
  - `license=cc0,by` restricts to exactly CC0/CC-BY (never by-sa, which
    isn't excludable by a documented negation operator, so this is an
    allow-list same as the other two sources). `license_type=
    commercial,modification` is a belt-and-braces second filter asking
    the API itself to only return results that explicitly permit both
    commercial use and derivatives (we remix tracks and upload to a
    monetized channel -- both apply).
  - Confirmed response fields: `license`, `license_url`, `attribution`,
    `url` (the audio file to download), `waveform_url`. `title` and
    `creator` are standard Openverse fields but weren't directly
    confirmed on the pages this was checked against, so they're read
    defensively with a fallback rather than assumed to always be
    present -- if a real run shows them under different names, fix
    here, don't guess further.
"""
from __future__ import annotations

import requests

from common.logging_setup import get_logger

from .cc_common import has_forbidden_marker, license_from_ccurl

log = get_logger(__name__)

OPENVERSE_AUDIO_URL = "https://api.openverse.org/v1/audio/"


def fetch_candidates(cfg: dict, limit: int) -> list[dict]:
    """Returns candidates in the same shape as the other two sources.
    Never raises -- a transient failure here just means this source
    contributes 0 tracks to this run, same pattern as freesound_source.
    """
    lane_tags = cfg.get("music", {}).get("cc_library", {}).get("tags", "lofi chill ambient instrumental")
    params = {
        "q": lane_tags,
        "license": "cc0,by",
        "license_type": "commercial,modification",
        "category": "music",
        "page_size": min(limit * 3, 150),
    }
    try:
        resp = requests.get(OPENVERSE_AUDIO_URL, params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        log.exception("openverse_source: search request failed")
        return []

    candidates = []
    for item in results:
        download_url = item.get("url")
        license_url = item.get("license_url")
        license_str = license_from_ccurl(license_url)
        if not download_url or license_str not in {"CC0", "CC-BY"} or has_forbidden_marker(license_url):
            continue

        # title/creator field names aren't independently confirmed (see
        # module docstring) -- fall back rather than crash on a miss.
        title = item.get("title") or item.get("name") or "Untitled track"
        creator = item.get("creator") or item.get("creator_name") or "unknown artist"
        source_url = item.get("foreign_landing_url") or item.get("url")

        candidates.append({
            "title": title,
            "artist": creator,
            "download_url": download_url,
            "license": license_str,
            "license_url": license_url,
            "source_url": source_url,
            "provider": "openverse",
        })
        if len(candidates) >= limit:
            break

    log.info("openverse_source: %d/%d candidates passed filters", len(candidates), len(results))
    return candidates
