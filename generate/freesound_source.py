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

from common.logging_setup import get_logger

from .cc_common import (
    MAX_SOURCE_DURATION_S,
    MIN_SOURCE_DURATION_S,
    fetch_results_with_retry,
    has_forbidden_marker,
    license_from_ccurl,
)

log = get_logger(__name__)

FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/"
# Derived from qc.py's real floor, NOT hand-picked. The first version
# used 20s here, which let 24-30s Freesound tracks through for QC (45s
# floor) to then reject -- a live run wasted a whole batch that way,
# 25 of 26 tracks rejected as duration_out_of_range. Verified live
# 2026-10-19 that the stricter floor still leaves plenty: 353 lofi /
# 5935 ambient / 378 chill / 1836 instrumental.
MIN_DURATION_S = MIN_SOURCE_DURATION_S
MAX_DURATION_S = MAX_SOURCE_DURATION_S

# Confirmed against the live API 2026-10-19: the duration range filter
# works, and it matters a lot -- Freesound is full of 3-second loops, so
# filtering server-side means a page isn't wasted on them. The license
# allow-list here uses the human-readable labels (that's what the filter
# syntax takes), while the RESPONSE's `license` field is a CC URL, which
# is what gets re-checked locally below.
_FILTER = (
    'category:Music '
    'license:("Attribution" OR "Creative Commons 0") '
    f"duration:[{int(MIN_DURATION_S)} TO {int(MAX_DURATION_S)}]"
)


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
    # One tag per request: confirmed on the live API 2026-10-19 that
    # Freesound ANDs a multi-word query -- the full tag string returned 2
    # results where a single tag ("lofi") returned 1589.
    tags = [t for t in lane_tags.split() if t]

    candidates: list[dict] = []
    seen_ids: set[object] = set()
    scanned = 0
    for tag in tags:
        if len(candidates) >= limit:
            break
        params = {
            "query": tag,
            "token": api_key,
            "filter": _FILTER,
            "fields": "id,name,username,license,duration,previews,num_downloads",
            # Most-downloaded first: this is "trending, but inside a free
            # library". Verified live 2026-10-20 -- the default ordering
            # returned tracks with ~400-800 downloads, downloads_desc
            # returns 4,800-5,900, so the same query yields the library's
            # genuinely popular material instead of obscure uploads.
            "sort": "downloads_desc",
            "page_size": 50,
        }
        results = fetch_results_with_retry(FREESOUND_SEARCH_URL, params)
        scanned += len(results)
        for item in results:
            if len(candidates) >= limit:
                break
            sound_id = item.get("id")
            if sound_id in seen_ids:  # the same sound can match several tags
                continue

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

            seen_ids.add(sound_id)
            username = item.get("username") or "unknown artist"
            candidates.append({
                "title": item.get("name") or f"freesound-{sound_id}",
                "artist": username,
                "download_url": preview_url,
                "license": license_str,
                "license_url": license_url,
                "source_url": f"https://freesound.org/people/{username}/sounds/{sound_id}/",
                "provider": "freesound",
                "popularity": item.get("num_downloads"),
                "popularity_unit": "downloads",
            })

    log.info("freesound_source: %d/%d candidates passed filters across %d tag(s)",
             len(candidates), scanned, len(tags))
    return candidates
