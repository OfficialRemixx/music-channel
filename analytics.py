"""Section 5.9: weekly YouTube Analytics pull -> updates weights.json (4.9) and
reports progress against BOTH the 4,000h (pre-2027-02-01) and 8,000h bars
(4.7), with the caveat that YouTube Studio's own eligibility page is the
source of truth, not estimatedMinutesWatched. P0 stub -- wired in P5."""
from __future__ import annotations
from common.logging_setup import get_logger

log = get_logger(__name__)

HOURS_BAR_LEGACY = 4000
HOURS_BAR_2027 = 8000
SUBS_BAR = 1000
AVG_VIEW_MINUTES_ASSUMPTION = 40  # Section 4.7's own stated assumption, not a guarantee


def views_needed_for(hours_bar: int, avg_view_minutes: float = AVG_VIEW_MINUTES_ASSUMPTION) -> int:
    return round((hours_bar * 60) / avg_view_minutes)


def update_weights(state: dict, cfg: dict) -> dict:
    """Section 4.9 learning loop. Guardrails: no change until a prompt family
    has >=min_videos and >=min_views; exploration_floor keeps every weight
    above a floor (never collapses to one sound -- a policy risk per 4.2/R7)."""
    w = cfg.get("weights", {})
    min_videos, min_views, floor = w.get("min_videos", 5), w.get("min_views", 500), w.get("exploration_floor", 0.25)
    log.info("analytics.update_weights: P0 stub, guardrails min_videos=%s min_views=%s floor=%s",
              min_videos, min_views, floor)
    return state.get("weights", {})


def weekly_report(state: dict, cfg: dict | None = None) -> str:
    uploaded = len(state.get("uploaded_videos", []))
    lines = [
        f"Uploaded videos so far: {uploaded}",
        f"Views needed for legacy 4,000h bar (@{AVG_VIEW_MINUTES_ASSUMPTION}min avg): {views_needed_for(HOURS_BAR_LEGACY)}",
        f"Views needed for 2027-02-01 8,000h bar: {views_needed_for(HOURS_BAR_2027)}",
        f"Subscriber bar: {SUBS_BAR}",
        "(Real Analytics API numbers not wired yet -- P0 stub. Studio is the source of truth.)",
    ]
    return "\n".join(lines)
