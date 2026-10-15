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

import random
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

# Combinatorial vocab for varied prompts -- all plain genre/mood/instrument
# words, nothing that could resemble a real artist/song/album name. Varying
# along these axes (genre, instrument set, mood, tempo band, key, texture) is
# what lets one A1 batch actually produce `tracks_per_batch` DIFFERENT
# tracks instead of the same 2 fixed prompts every time (see the
# tracks_per_batch comment in config.yaml for why that mattered).
_GENRES = ["lofi", "chill", "ambient", "jazz"]
_MOODS = ["focused", "calm", "relaxed", "dreamy", "peaceful", "warm", "nostalgic"]
_INSTRUMENT_SETS = [
    ["soft piano", "warm keys"],
    ["rhodes", "mellow bass"],
    ["acoustic guitar", "soft pads"],
    ["felt piano", "strings"],
    ["muted brass", "soft keys"],
    ["marimba", "warm bass"],
]
_TEXTURES = ["vinyl texture", "rain ambience", "tape hiss", "soft static"]
_KEYS = ["C major", "A minor", "G major", "D minor", "F major", "E minor", "D major", "B minor"]
_TEMPO_BANDS = [(60, 75), (65, 85), (70, 90), (80, 95)]


def _random_prompt_item(lane: str, trend_word: str | None, rng: random.Random) -> dict:
    """One combinatorially-varied prompt. trend_word (already filtered to
    GENERIC_WORDS by fetch_trend_signal, so still copyright-safe) is folded
    into the instrument/texture list when given -- never replaces genre, so
    a bad/empty trend signal degrades gracefully to a plain fixed prompt."""
    genre = rng.choice(_GENRES)
    mood = rng.choice(_MOODS)
    bpm_lo, bpm_hi = rng.choice(_TEMPO_BANDS)
    key = rng.choice(_KEYS)
    instruments = list(rng.choice(_INSTRUMENT_SETS)) + [rng.choice(_TEXTURES)]
    if trend_word and trend_word not in instruments and trend_word != genre:
        instruments.append(trend_word)
    return {
        "genre": genre, "mood": mood, "bpm": [bpm_lo, bpm_hi], "key": key,
        "instruments": instruments, "lane": lane,
    }


def fallback_prompt_matrix(
    cfg: dict, count: int | None = None, trend_words: list[str] | None = None,
) -> list[dict]:
    """Generates `count` varied prompts (default: cfg["music"]["tracks_per_batch"]),
    split between trend-influenced and fixed/evergreen per
    cfg["music"]["trend_share"]/"fixed_share" -- used whenever trend fetch is
    unavailable, disabled, or (for the fixed_share portion) always, by
    design. Deduplicates exact-identical prompt combos within one batch on a
    best-effort basis (the combinatorial space is large enough that a retry
    or two always finds a fresh one in practice)."""
    lane = cfg["niche"]["lane"]
    music_cfg = cfg.get("music", {})
    count = count if count is not None else music_cfg.get("tracks_per_batch", 2)
    trend_share = music_cfg.get("trend_share", 0.5)
    trend_words = trend_words or []

    rng = random.Random()
    n_trend = round(count * trend_share) if trend_words else 0
    seen: set[tuple] = set()
    matrix: list[dict] = []
    for i in range(count):
        word = rng.choice(trend_words) if i < n_trend else None
        for _attempt in range(5):
            item = _random_prompt_item(lane, word, rng)
            fingerprint = (
                item["genre"], item["mood"], tuple(item["instruments"]),
                item["key"], tuple(item["bpm"]),
            )
            if fingerprint not in seen:
                seen.add(fingerprint)
                break
        matrix.append(item)
    return matrix


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

    signal = fetch_trend_signal(cfg)
    if not signal:
        log.info("trend.build_prompt_matrix: empty/unavailable trend signal, using fallback matrix as-is")
        return fallback_prompt_matrix(cfg)

    # Trend words only ever mix into a fraction (trend_share) of the batch's
    # prompts' instrument/texture list -- never replace genre/mood outright,
    # keeping prompts grounded in our own hand-picked vocab rather than
    # letting the chart drive them.
    return fallback_prompt_matrix(cfg, trend_words=sorted(signal))


if __name__ == "__main__":
    import json
    print(json.dumps(build_prompt_matrix(), indent=2))
