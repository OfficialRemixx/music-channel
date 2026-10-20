"""Section 5.6: Gemini-free-tier title/description/tags/localizations, with a
system prompt that enforces R2 (no artist/song/label names) and R4 (no "AI",
no invented bios, no "copyright free"/"no claims" promises). P0 stub -- real
Gemini call wired once GEMINI_API_KEY exists (Section 9 step 6) and the
current Gemini API docs are read (rule 0.2)."""
from __future__ import annotations

import re

from common.logging_setup import get_logger

log = get_logger(__name__)

FORBIDDEN_WORDS = {"ai", "ai-generated", "ai music", "copyright free", "no copyright",
                    "no claims", "copyright-free"}


def _attribution_lines(tracks: list[dict] | None) -> list[str]:
    """CC-BY legally requires attribution; CC0/public-domain don't, but
    listing them anyway is good practice AND doubles as on-the-record proof
    of license if a track ever gets a mistaken Content ID claim (see
    generate/cc_library.py's module docstring). Only tracks carrying an
    `attribution` string (added by cc_library-sourced tracks) are listed --
    AI-generated tracks have nothing to attribute."""
    if not tracks:
        return []
    lines = [t["attribution"] for t in tracks if t.get("attribution")]
    if not lines:
        return []
    return ["", "Music credits:", *lines]


def _template_fallback(
    mood: str, use_case: str, series: str, vol: int, duration_min: int,
    tracks: list[dict] | None = None,
) -> dict:
    """Deterministic, rule-following fallback used whenever Gemini is
    unavailable/rate-limited, and always in dry_run (no network call)."""
    # Lane names are snake_case identifiers ("deep_focus"), so .title()
    # put a literal underscore in the public title -- the first published
    # video went out as "Deep_Focus Lofi for Focus | 60 min | Deep Focus
    # Vol. 1": an underscore on screen, "Focus" twice, and "Deep Focus"
    # twice. With a video going out every day, that is the title on every
    # one of them, and a title that reads as broken costs clicks.
    mood_label = " ".join(mood.replace("_", " ").replace("-", " ").split()).title()
    use_label = " ".join(use_case.replace("_", " ").split()).title()

    headline = f"{mood_label} Lofi"
    if use_label.lower() not in mood_label.lower():
        headline += f" for {use_label}"
    # Drop the series name when the lane already says it, so we get
    # "... | Vol. 3" rather than "Deep Focus ... | Deep Focus Vol. 3".
    vol_label = f"Vol. {vol}" if series.lower() in mood_label.lower() else f"{series} Vol. {vol}"
    title = f"{headline} | {duration_min} min | {vol_label}"

    desc_lines = [
        # Not "original instrumentals" any more: since A1c, mixes also carry
        # CC-licensed tracks by real artists (credited below), and calling
        # those ours would be a false claim in every description.
        f"{use_label} companion mix -- calm {mood_label.lower()} instrumentals, no vocals.",
        "Timestamps below. New mixes every day.",
        *_attribution_lines(tracks),
    ]
    return {
        "title": title,
        "description": "\n".join(desc_lines),
        # dict.fromkeys dedupes while keeping order -- mood and use_case
        # are the same word whenever the lane is named after its use
        # ("sleep"/"sleep"), and YouTube ignores a repeated tag anyway.
        "tags": list(dict.fromkeys([
            mood_label.lower(), use_label.lower(), "lofi", "instrumental",
            "study music", "background music",
        ])),
    }


def validate_public_text(text: dict) -> list[str]:
    """R4 enforcement gate. Returns a list of violations (empty = passes).
    Whole-word/phrase matching (word-boundary regex), not plain substring --
    a substring check would false-positive on "ai" inside ordinary words
    like "available" or an artist name like "Air", which became a real risk
    once attribution text (real artist/track names, not written by us) got
    added to the description (see _attribution_lines, 2026-10-19)."""
    parts = [text.get("title", ""), text.get("description", ""), " ".join(text.get("tags", []))]
    blob = " ".join(parts).lower()
    return [w for w in FORBIDDEN_WORDS if re.search(rf"\b{re.escape(w)}\b", blob)]


def generate_metadata(mood: str, use_case: str, series: str, vol: int, duration_min: int,
                       cfg: dict | None = None, tracks: list[dict] | None = None) -> dict:
    meta = _template_fallback(mood, use_case, series, vol, duration_min, tracks=tracks)
    violations = validate_public_text(meta)
    if violations:
        log.error("seo.generate_metadata: template itself failed R4 gate: %s", violations)
        raise AssertionError(f"R4 violation in generated metadata: {violations}")
    return meta
