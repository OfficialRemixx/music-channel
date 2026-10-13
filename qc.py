"""Section 5.3: quality gate. Rejects silence/clipping/bad length/vocals
(instrumental-only)/short-loop repetition/near-duplicates. Normalises loudness,
tags BPM/key, scores each track. Nothing failing QC may reach upload (R9).

Real audio analysis (pydub/numpy/loudness) is intentionally not wired yet --
this is the P0 scaffold. P1 fills in real checks once first tracks exist.
"""
from __future__ import annotations

from dataclasses import dataclass

from common.logging_setup import get_logger

log = get_logger(__name__)

TARGET_LUFS = -14.0
MAX_TRUE_PEAK_DBTP = -1.0


@dataclass
class QCResult:
    accepted: bool
    score: float
    reasons: list[str]
    bpm: float | None = None
    key: str | None = None


def check_track(path: str) -> QCResult:
    """P0 stub: always rejects with a clear reason so nothing silently passes
    QC before real checks exist (fails safe per R9)."""
    log.warning("qc.check_track: real checks not implemented yet (P0 stub) -- "
                "rejecting %s by default", path)
    return QCResult(accepted=False, score=0.0, reasons=["qc_not_implemented_yet"])
