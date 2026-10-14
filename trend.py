"""Section 5.1: read ONLY generic genre/mood/BPM/instrument words from YouTube's
mostPopular music chart via videos.list (cheap), strip proper nouns, and emit a
prompt matrix for generate/. Falls back to config-driven fixed prompts.

Real API call (confirmed live per master-prompt rule 0.2, via
https://developers.google.com/youtube/v3/docs/videos/list):
  GET https://www.googleapis.com/youtube/v3/videos
    ?part=snippet&chart=mostPopular&regionCode=<cc>&videoCategoryId=<id>&maxResults=<n>
  chart accepts only "mostPopular"; regionCode/videoCategoryId are optional
  and only apply together with chart; 1 quota unit per call.
search.list is capped at 20 calls/week total (shared bucket) -- this module
must not call search.list at all; videos.list only.

We never pass any trend-derived proper noun (titles, channel names, artist
names) into the generation prompt -- only genre/mood/instrument/BPM words
drawn from GENERIC_WORDS, so this stays strictly non-infringing regardless of
what shows up in the chart.
"""
from __future__ import annotations

import re

from common.config import load_config
from common.logging_setup import get_logger

log = get_logger(__name__)

# Deliberately small and conservative -- names are stripped by a blocklist,
# not generated, so a false negative here is the safe failure direction.
PROPER_NOUN_BLOCKLIST_HINT = "capitalised multi-word phrases not in GENERIC_WORDS"

GENERIC_WORDS = {
    "lofi", "chill", "study", "focus", "piano", "ambient", "relax", "deep",
    "calm", "night", "rain", "coffee", "jazz", "beats", "sleep", "acoustic",
}

_WORD_RE = re.compile(r"[a-zA-Z]+")


def fallback_prompt_matrix(cfg: dict) -> list[dict]:
    """Used when trend fetch is unavailable, disabled, or returns nothing safe."""
    lane = cfg["niche"]["lane"]
    return [
        {"genre": "lofi", "mood": "focused", "bpm": [70, 90], "key": "C major",
         "instruments": ["soft piano", "warm keys", "vinyl texture"], "lane": lane},
        {"genre": "lofi", "mood": "calm", "bpm": [60, 75], "key": "A minor",
         "instruments": ["rhodes", "mellow bass", "rain ambience"], "lane": lane},
    ]


def _extract_generic_words(text: str) -> set[str]:
    """Lowercases, splits on word boundaries, and keeps only words already in
    our fixed GENERIC_WORDS allowlist -- never emits anything not already
    vetted, so this can't leak a proper noun or an unvetted word."""
    if not text:
        return set()
    found = set()
    for match in _WORD_RE.findall(text.lower()):
        if match in GENERIC_WORDS:
            found.add(match)
    return found


def _get_music_category_id(service, region_code: str) -> str:
    from upload import DEFAULT_CATEGORY_ID, _get_category_id
    return _get_category_id(service, region_code=region_code) or DEFAULT_CATEGORY_ID


def fetch_trend_signal(cfg: dict, region_code: str = "US", max_results: int = 25) -> set[str]:
    """Calls videos.list(chart=mostPopular) for the Music category and returns
    the set of GENERIC_WORDS found across those videos' titles/descriptions.
    Returns an empty set (never raises) on any API error so the caller can
    fall back cleanly -- a trend-signal outage must never break generation."""
    from common.youtube_client import get_service

    try:
        service = get_service()
        category_id = _get_music_category_id(service, region_code)
        resp = service.videos().list(
            part="snippet",
            chart="mostPopular",
            regionCode=region_code,
            videoCategoryId=category_id,
            maxResults=max_results,
        ).execute()
    except Exception:
        log.exception("trend.fetch_trend_signal: videos.list(mostPopular) failed, returning empty signal")
        return set()

    words: set[str] = set()
    items = resp.get("items", [])
    for item in items:
        snippet = item.get("snippet", {})
        words |= _extract_generic_words(snippet.get("title", ""))
        words |= _extract_generic_words(snippet.get("description", ""))
    log.info(
        "trend.fetch_trend_signal: found %d generic words from %d chart videos",
        len(words), len(items),
    )
    return words


def build_prompt_matrix(cfg: dict | None = None, dry_run: bool | None = None) -> list[dict]:
    cfg = cfg or load_config()
    dry_run = cfg.get("dry_run", True) if dry_run is None else dry_run
    if dry_run:
        log.info("trend.py: dry_run=True, using fallback_prompt_matrix only")
        return fallback_prompt_matrix(cfg)

    base = fallback_prompt_matrix(cfg)
    signal = fetch_trend_signal(cfg)
    if not signal:
        log.info("trend.build_prompt_matrix: empty/unavailable trend signal, using fallback matrix as-is")
        return base

    # Nudge (not replace) the fallback matrix's mood word with any trending
    # generic word not already used, keeping prompts grounded in our own
    # hand-picked base rather than letting the chart drive them outright.
    extra = sorted(signal)
    if extra:
        pick = extra[0]
        for item in base:
            if pick not in item["instruments"] and pick != item["genre"]:
                item["instruments"] = item["instruments"] + [pick]
    return base


if __name__ == "__main__":
    import json
    print(json.dumps(build_prompt_matrix(), indent=2))
