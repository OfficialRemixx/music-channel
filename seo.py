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
    title = f"{mood.title()} Lofi for {use_case.title()} | {duration_min} min | {series} Vol. {vol}"
    desc_lines = [
        f"{use_case.title()} companion mix -- {mood} original instrumentals, no vocals.",
        "Timestamps below. New volumes every week.",
        *_attribution_lines(tracks),
    ]
    return {
        "title": title,
        "description": "\n".join(desc_lines),
        "tags": [mood, use_case, "lofi", "instrumental"],
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
