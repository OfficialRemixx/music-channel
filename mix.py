"""Section 5.4: pick tracks for a long mix by energy arc + key compatibility,
crossfade, respect max_track_reuse (Section 4.4b), produce chapter timestamps
and a highlight clip per Short.

Track pool entries are the dicts qc.check_track-accepted tracks carry:
{path, score, bpm, key, loudness_dbfs, duration_s, ...}. Energy proxy is
loudness_dbfs (closer to 0 = more energetic, since qc.py already normalises
every track toward TARGET_LUFS, so relative loudness differences left after
normalization reflect the track's actual dynamic energy, not just gain).
Key compatibility uses the circle-of-fifths distance between consecutive
tracks' estimated keys so the crossfade doesn't clash harmonically.
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

from common.config import load_config
from common.logging_setup import get_logger

log = get_logger(__name__)

# Circle-of-fifths order, used to measure harmonic distance between two
# estimated keys (adjacent keys on the circle are the most compatible).
CIRCLE_OF_FIFTHS = ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]

HIGHLIGHT_CLIP_S = 30.0


def _key_root(key: str | None) -> str | None:
    if not key:
        return None
    return key.split(" ")[0]


def _key_distance(key_a: str | None, key_b: str | None) -> int:
    """0 = same root, 1 = adjacent on the circle of fifths (most compatible
    non-identical pairing), up to 6 = opposite/least compatible. Unknown
    keys are treated as maximally uncertain (mid-distance) rather than
    penalized or favored."""
    root_a, root_b = _key_root(key_a), _key_root(key_b)
    if not root_a or not root_b:
        return 3
    if root_a == root_b:
        return 0
    try:
        idx_a, idx_b = CIRCLE_OF_FIFTHS.index(root_a), CIRCLE_OF_FIFTHS.index(root_b)
    except ValueError:
        return 3
    diff = abs(idx_a - idx_b) % 12
    return min(diff, 12 - diff)


def _energy(track: dict) -> float:
    loudness = track.get("loudness_dbfs")
    if loudness is None:
        return 0.0
    # loudness_dbfs is negative; less negative (closer to 0) = more energetic.
    return loudness


def _order_by_energy_arc(tracks: list[dict], target_count: int) -> list[dict]:
    """Builds a gentle rise-peak-fall energy arc: start calmer, build toward
    the middle, wind back down -- rather than a flat shuffle or a sawtooth of
    random energy jumps, which makes a long ambient/focus mix feel aimless."""
    pool = sorted(tracks, key=_energy)  # calmest first
    if len(pool) > target_count:
        # Keep the spread: take an even stride through the sorted pool so we
        # don't just keep the N calmest tracks and discard all the energetic
        # ones (keep_ratio already filtered for quality upstream in qc.py --
        # this step is about arc shape, not re-filtering quality).
        stride = len(pool) / target_count
        pool = [pool[int(i * stride)] for i in range(target_count)]

    midpoint = len(pool) // 2
    rising = pool[:midpoint]
    falling = list(reversed(pool[midpoint:]))
    # Interleave so energy climbs toward the middle then eases back down,
    # instead of one block of rising followed by one abrupt block of falling.
    arc: list[dict] = []
    for i in range(max(len(rising), len(falling))):
        if i < len(rising):
            arc.append(rising[i])
        if i < len(falling):
            arc.append(falling[i])
    return arc


def _resolve_track_reuse(ordered: list[dict], max_reuse: int) -> list[dict]:
    """Enforces Section 4.4b's max_track_reuse by dropping a track's repeats
    beyond the allowed count rather than silently exceeding the cap."""
    if max_reuse <= 0:
        return ordered
    seen: dict[str, int] = {}
    kept = []
    for track in ordered:
        key = track.get("path") or id(track)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= max_reuse:
            kept.append(track)
    return kept


def _build_chapters(ordered: list[dict], crossfade_s: float) -> list[dict]:
    chapters = []
    cursor = 0.0
    for track in ordered:
        duration = track.get("duration_s") or track.get("details", {}).get("duration_s") or 0.0
        chapters.append({
            "title": track.get("prompt", "Untitled")[:100],
            "start_s": round(cursor, 1),
            "track_path": track.get("path"),
        })
        # Each track after the first overlaps the previous by crossfade_s.
        cursor += max(duration - crossfade_s, 0.0)
    return chapters


def _pick_highlight(ordered: list[dict], chapters: list[dict]) -> dict | None:
    if not ordered:
        return None
    # Favor the highest-scoring track for the Shorts highlight clip -- it's
    # the one most likely to hook a viewer into the full long-form mix.
    best_idx, best = 0, ordered[0]
    for i, track in enumerate(ordered):
        if track.get("score", 0.0) > best.get("score", 0.0):
            best_idx, best = i, track
    return {
        "track_path": best.get("path"),
        "start_s": chapters[best_idx]["start_s"] if best_idx < len(chapters) else 0.0,
        "duration_s": HIGHLIGHT_CLIP_S,
    }


def _render_audio(ordered: list[dict], crossfade_s: float, batch_id: str) -> str | None:
    """Concatenates the chosen tracks into one long mix with crossfades,
    using each track's QC-normalized file (normalized_path) when available
    so the whole mix is already at TARGET_LUFS. Returns None (fail-safe) if
    any track file is missing or decoding fails -- render_video already
    treats a missing audio_path as "nothing to render"."""
    try:
        from pydub import AudioSegment
    except ImportError:
        log.exception("mix._render_audio: pydub not installed")
        return None

    combined = None
    crossfade_ms = int(crossfade_s * 1000)
    for track in ordered:
        source = track.get("normalized_path") or track.get("path")
        if not source or not Path(source).exists():
            log.warning("mix._render_audio: missing audio file for track %r, aborting render",
                        track.get("path"))
            return None
        try:
            segment = AudioSegment.from_file(source)
        except Exception:
            log.exception("mix._render_audio: could not decode %s", source)
            return None
        if combined is None:
            combined = segment
        else:
            fade = min(crossfade_ms, len(combined), len(segment))
            combined = combined.append(segment, crossfade=max(fade, 0))

    if combined is None:
        return None

    out_dir = Path(tempfile.gettempdir()) / "music-channel-mixes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{batch_id}.wav"
    try:
        combined.export(out_path, format="wav")
    except Exception:
        log.exception("mix._render_audio: export failed")
        return None
    return str(out_path)


def build_mix(track_pool: list[dict], cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    lo, hi = cfg["mix"]["tracks"]
    if len(track_pool) < lo:
        log.info("mix.build_mix: pool has %d tracks, need >=%d -- nothing to build yet",
                  len(track_pool), lo)
        return {"tracks": [], "chapters": [], "highlight": None}

    crossfade_lo, crossfade_hi = cfg["mix"]["crossfade_s"]
    crossfade_s = random.uniform(crossfade_lo, crossfade_hi)
    max_reuse = cfg.get("music", {}).get("max_track_reuse", 1)

    target_count = min(hi, len(track_pool))
    ordered = _order_by_energy_arc(track_pool, target_count)
    ordered = _resolve_track_reuse(ordered, max_reuse)

    if len(ordered) < lo:
        log.info("mix.build_mix: after reuse-cap filtering only %d tracks left, need >=%d",
                  len(ordered), lo)
        return {"tracks": [], "chapters": [], "highlight": None}

    # Swap in a couple of adjacent tracks if it meaningfully improves key
    # compatibility, without reshuffling the whole energy arc.
    for i in range(len(ordered) - 2):
        if _key_distance(ordered[i].get("key"), ordered[i + 1].get("key")) > \
           _key_distance(ordered[i].get("key"), ordered[i + 2].get("key")):
            ordered[i + 1], ordered[i + 2] = ordered[i + 2], ordered[i + 1]

    chapters = _build_chapters(ordered, crossfade_s)
    highlight = _pick_highlight(ordered, chapters)
    batch_id = cfg.get("niche", {}).get("lane", "mix")
    audio_path = _render_audio(ordered, crossfade_s, batch_id)

    return {
        "tracks": ordered,
        "chapters": chapters,
        "highlight": highlight,
        "crossfade_s": round(crossfade_s, 1),
        "batch_id": batch_id,
        "audio_path": audio_path,
    }
