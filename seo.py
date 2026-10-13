"""Section 5.6: Gemini-free-tier title/description/tags/localizations, with a
system prompt that enforces R2 (no artist/song/label names) and R4 (no "AI",
no invented bios, no "copyright free"/"no claims" promises). P0 stub -- real
Gemini call wired once GEMINI_API_KEY exists (Section 9 step 6) and the
current Gemini API docs are read (rule 0.2)."""
from __future__ import annotations
from common.logging_setup import get_logger

log = get_logger(__name__)

FORBIDDEN_WORDS = {"ai", "ai-generated", "ai music", "copyright free", "no copyright",
                    "no claims", "copyright-free"}


def _template_fallback(mood: str, use_case: str, series: str, vol: int, duration_min: int) -> dict:
    """Deterministic, rule-following fallback used whenever Gemini is
    unavailable/rate-limited, and always in dry_run (no network call)."""
    title = f"{mood.title()} Lofi for {use_case.title()} | {duration_min} min | {series} Vol. {vol}"
    desc_lines = [
        f"{use_case.title()} companion mix -- {mood} original instrumentals, no vocals.",
        "Timestamps below. New volumes every week.",
    ]
    return {"title": title, "description": "\n".join(desc_lines), "tags": [mood, use_case, "lofi", "instrumental"]}


def validate_public_text(text: dict) -> list[str]:
    """R4 enforcement gate. Returns a list of violations (empty = passes)."""
    blob = " ".join([text.get("title", ""), text.get("description", ""), " ".join(text.get("tags", []))]).lower()
    return [w for w in FORBIDDEN_WORDS if w in blob]


def generate_metadata(mood: str, use_case: str, series: str, vol: int, duration_min: int,
                       cfg: dict | None = None) -> dict:
    meta = _template_fallback(mood, use_case, series, vol, duration_min)
    violations = validate_public_text(meta)
    if violations:
        log.error("seo.generate_metadata: template itself failed R4 gate: %s", violations)
        raise AssertionError(f"R4 violation in generated metadata: {violations}")
    return meta
