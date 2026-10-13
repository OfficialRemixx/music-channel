"""Section 5.1: read ONLY generic genre/mood/BPM/instrument words from YouTube's
mostPopular music chart via videos.list (cheap), strip proper nouns, and emit a
prompt matrix for generate/. Falls back to config-driven fixed prompts.

TODO before going live (per master-prompt rule 0.2): read the current
youtube-v3 videos.list docs and pin the exact params (chart=mostPopular,
videoCategoryId, regionCode) before wiring the real API call. Do not guess.
search.list is capped at 20 calls/week total (shared bucket) -- this module
must not call search.list at all; videos.list only.
"""
from __future__ import annotations

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


def fallback_prompt_matrix(cfg: dict) -> list[dict]:
    """Used when trend fetch is unavailable, disabled, or returns nothing safe.
    This is also what P0/dry-run uses, since no YouTube API wiring exists yet."""
    lane = cfg["niche"]["lane"]
    return [
        {"genre": "lofi", "mood": "focused", "bpm": [70, 90], "key": "C major",
         "instruments": ["soft piano", "warm keys", "vinyl texture"], "lane": lane},
        {"genre": "lofi", "mood": "calm", "bpm": [60, 75], "key": "A minor",
         "instruments": ["rhodes", "mellow bass", "rain ambience"], "lane": lane},
    ]


def build_prompt_matrix(cfg: dict | None = None, dry_run: bool | None = None) -> list[dict]:
    cfg = cfg or load_config()
    dry_run = cfg.get("dry_run", True) if dry_run is None else dry_run
    if dry_run:
        log.info("trend.py: dry_run=True, using fallback_prompt_matrix only")
        return fallback_prompt_matrix(cfg)
    raise NotImplementedError(
        "Wire videos.list(chart=mostPopular) here once the YouTube Data API "
        "project + OAuth exist (Section 9 step 4). Read the current docs first."
    )


if __name__ == "__main__":
    import json
    print(json.dumps(build_prompt_matrix(), indent=2))
