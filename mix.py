"""Section 5.4: pick tracks for a long mix by energy arc + key compatibility,
crossfade, respect max_track_reuse (Section 4.4b), produce chapter timestamps
and a highlight clip per Short. P0 stub -- wired once qc.py produces real
scored tracks."""
from __future__ import annotations

from common.config import load_config
from common.logging_setup import get_logger

log = get_logger(__name__)


def build_mix(track_pool: list[dict], cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    lo, hi = cfg["mix"]["tracks"]
    if len(track_pool) < lo:
        log.info("mix.build_mix: pool has %d tracks, need >=%d -- nothing to build yet",
                  len(track_pool), lo)
        return {"tracks": [], "chapters": [], "highlight": None}
    raise NotImplementedError("Energy-arc ordering + crossfade plan: fill in during P2.")
