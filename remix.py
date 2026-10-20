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

import subprocess
from pathlib import Path

from common.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_BOOST_DB = 6.0
DEFAULT_CUTOFF_HZ = 150

# Mashup defaults. The second track sits UNDER the first as a bed rather
# than competing with it -- two CC tracks at equal level just sound like
# two songs playing at once.
DEFAULT_BED_GAIN_DB = -7.0
DEFAULT_BPM_TOLERANCE = 8.0


def analyze_track(path: str) -> tuple[float | None, str | None]:
    """(bpm, key) for a candidate, reusing qc.py's own estimators so the
    mashup pairing agrees with what QC will later measure."""
    import qc

    loaded = qc._load_samples(path)
    if not loaded:
        return None, None
    samples, sr = loaded
    bpm = qc._estimate_bpm(samples, sr)
    key = qc._estimate_key(qc._chroma_vector(samples, sr))
    return bpm, key


def pair_for_mashup(
    analyzed: list[tuple[int, float | None, str | None]],
    bpm_tolerance: float = DEFAULT_BPM_TOLERANCE,
) -> list[tuple[int, int]]:
    """Pairs up indices of tracks that will actually sit together: same
    estimated key and BPM within tolerance.

    Layering two arbitrary tracks is how you get something unlistenable --
    clashing keys beat against each other, and mismatched tempos smear the
    groove. QC checks level and clipping, not dissonance, so a bad pair
    would sail through and end up in a published mix. Anything without a
    confident key/BPM reading is simply left unpaired and gets the plain
    bass-boost instead.
    """
    by_key: dict[str, list[tuple[int, float]]] = {}
    for idx, bpm, key in analyzed:
        if key and bpm:
            by_key.setdefault(key, []).append((idx, bpm))

    pairs: list[tuple[int, int]] = []
    for entries in by_key.values():
        entries.sort(key=lambda e: e[1])  # by bpm, so neighbours are closest
        used: set[int] = set()
        for i in range(len(entries) - 1):
            a_idx, a_bpm = entries[i]
            b_idx, b_bpm = entries[i + 1]
            if a_idx in used or b_idx in used:
                continue
            if abs(a_bpm - b_bpm) <= bpm_tolerance:
                pairs.append((a_idx, b_idx))
                used.update({a_idx, b_idx})
    return pairs


def mashup_tracks(
    lead_path: str,
    bed_path: str,
    out_path: str,
    bed_gain_db: float = DEFAULT_BED_GAIN_DB,
) -> str | None:
    """Layers `bed_path` under `lead_path` and writes the blend.

    Uses ffmpeg's amix directly rather than pydub: ffmpeg is already a
    hard dependency (visuals.py renders with it, and pydub shells out to
    it anyway), and amix handles the length/level maths in one pass.
    duration=shortest stops the longer track from running on alone after
    the other ends; normalize=0 keeps amix from quietly halving both
    inputs, and the dynaudnorm pass afterwards brings the blend back to a
    sane level without clipping -- qc.py rejects clipped audio.
    """
    filt = (
        f"[1:a]volume={bed_gain_db}dB[bed];"
        "[0:a][bed]amix=inputs=2:duration=shortest:normalize=0[mixed];"
        "[mixed]dynaudnorm=f=250:g=15,alimiter=limit=0.95[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", lead_path,
        "-i", bed_path,
        "-filter_complex", filt,
        "-map", "[out]",
        str(out_path),
    ]
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        log.error("remix.mashup_tracks: ffmpeg exited %s. Last stderr:\n%s",
                  exc.returncode, "\n".join(stderr.splitlines()[-10:]) or "(empty)")
        return None
    except (subprocess.TimeoutExpired, OSError):
        log.exception("remix.mashup_tracks: ffmpeg failed for %s + %s", lead_path, bed_path)
        return None
    return out_path


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
