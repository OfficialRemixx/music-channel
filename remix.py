"""Audio transform applied to cc_library tracks before they go through QC --
this is the "remix" step: a bass-boost (the brief literally asked for
"bass boosted"), which is legally trivial because the source audio is
already CC0/CC-BY (derivatives explicitly allowed by those licenses -- see
generate/cc_library.py's ALLOWED_LICENSES). This stays simple/explainable
DSP like qc.py, not a black-box effect: boost a low-passed copy of the
track and overlay it back onto the original, then normalize peak so the
boost doesn't push the track into qc.py's clipping rejection.
"""
from __future__ import annotations

from pathlib import Path

from common.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_BOOST_DB = 6.0
DEFAULT_CUTOFF_HZ = 150


def bass_boost_track(
    source_path: str,
    out_path: str,
    boost_db: float = DEFAULT_BOOST_DB,
    cutoff_hz: int = DEFAULT_CUTOFF_HZ,
) -> str | None:
    """Low-passes a copy of the track below cutoff_hz, boosts it by boost_db,
    and overlays it back onto the original (classic bass-boost technique).
    Peak-normalizes afterward so the boost doesn't clip -- qc.py would reject
    a clipped track anyway, but failing early here gives a clearer log line.
    Returns out_path, or None on any decode/processing failure (fail-safe,
    matching mix.py's _render_audio pattern)."""
    try:
        from pydub import AudioSegment, effects
    except ImportError:
        log.exception("remix.bass_boost_track: pydub not installed")
        return None

    try:
        original = AudioSegment.from_file(source_path)
    except Exception:
        log.exception("remix.bass_boost_track: could not decode %s", source_path)
        return None

    bass = original.low_pass_filter(cutoff_hz).apply_gain(boost_db)
    boosted = original.overlay(bass)
    boosted = effects.normalize(boosted, headroom=1.0)  # keep peak safely under 0 dBFS

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        boosted.export(out_path, format="wav")
    except Exception:
        log.exception("remix.bass_boost_track: export failed for %s", out_path)
        return None
    return out_path
