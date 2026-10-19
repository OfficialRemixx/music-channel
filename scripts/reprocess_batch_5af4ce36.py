"""One-off recovery (2026-10-19): batch `deep_focus-5af4ce36` (26 tracks,
generated 2026-10-15) was already fetched and QC'd once, but two real bugs
corrupted that run:

1. qc.MAX_DEDUPE_SIMILARITY / config.yaml's dedupe_similarity_max were set to
   0.92 -- far too aggressive for a pure 12-bin chroma fingerprint, which
   mostly encodes key/instrumentation rather than melody/timbre. 18/26
   genuinely different tracks got wrongly rejected as "near-duplicates"
   (real cosine similarities logged: 0.920-0.984). Fixed: threshold raised
   to 0.995 (qc.py, config.yaml).
2. storage.sync_tracks_to_kaggle_dataset()'s `kaggle datasets version` path
   never wrote dataset-metadata.json (only the first-ever `datasets create`
   did), so the push of the 7 tracks that DID pass failed with "Metadata
   file not found: dataset-metadata.json" -- silently, because the caller
   didn't check the return value. Those 7 tracks were left in track_pool
   pointing at audio that was never actually uploaded anywhere. Fixed:
   storage.py always writes the metadata file now, and a2_collect.py rolls
   back track_pool entries on a sync failure instead of keeping them.

This script redoes that one batch correctly now that both bugs are fixed:
the Kaggle kernel's output is still live, so nothing was actually lost.
Safe to run only once -- it removes any existing `deep_focus-5af4ce36-*`
track_pool entries (added under the broken code) before reprocessing, so a
retry after a partial failure just starts clean rather than duplicating.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage
from common.config import load_config
from common.logging_setup import get_logger
from generate.base import Job
from generate.kaggle_acestep import KaggleAceStepProvider
from notify import alert_failure, send
from qc import check_track, fingerprint_similarity

log = get_logger(__name__)

BATCH_ID = "deep_focus-5af4ce36"
KERNEL_ID = "officialremixx/mc-gen-deep-focus-5af4ce36"


def main() -> int:
    cfg = load_config()
    dedupe_max = cfg.get("music", {}).get("dedupe_similarity_max", 0.995)
    pass_rate_alert_below = cfg.get("qc", {}).get("pass_rate_alert_below", 0.5)

    state = storage.load()

    before = len(state["track_pool"])
    state["track_pool"] = [
        t for t in state["track_pool"] if not t.get("track_id", "").startswith(BATCH_ID)
    ]
    log.info("reprocess: removed %d stale entries from the broken first pass",
              before - len(state["track_pool"]))

    provider = KaggleAceStepProvider(cfg)
    job = Job(job_id=KERNEL_ID, provider="kaggle_acestep", status="complete",
              meta={"slug": f"mc-gen-{BATCH_ID}", "batch_id": BATCH_ID})
    tracks = provider.fetch(job)
    log.info("reprocess: fetched %d tracks from the still-live kernel output", len(tracks))

    new_local_files: list[Path] = []
    newly_appended: list[dict] = []
    accepted = 0
    batch_fingerprints: list[list[float] | None] = [t.get("fingerprint") for t in state["track_pool"]]

    for i, track in enumerate(tracks):
        track["track_id"] = f"{BATCH_ID}-{i:03d}"
        result = check_track(track["path"])
        if not result.accepted:
            log.info("reprocess: rejected %s (%s)", track["track_id"], result.reasons)
            continue

        max_sim = max(
            (fingerprint_similarity(result.fingerprint, fp) for fp in batch_fingerprints),
            default=0.0,
        )
        if max_sim >= dedupe_max:
            log.info(
                "reprocess: rejected %s as a near-duplicate (cosine similarity %.3f >= %.3f)",
                track["track_id"], max_sim, dedupe_max,
            )
            continue

        track.update({
            "score": result.score,
            "bpm": result.bpm,
            "key": result.key,
            "loudness_dbfs": result.loudness_dbfs,
            "normalized_path": result.normalized_path,
            "fingerprint": result.fingerprint,
            "used_count": 0,
        })
        stored = storage.store_track_file(track)
        new_local_files.append(Path(stored.pop("_local_path")))
        state["track_pool"].append(stored)
        newly_appended.append(stored)
        batch_fingerprints.append(stored.get("fingerprint"))
        accepted += 1

    pass_rate = accepted / len(tracks) if tracks else 0.0
    log.info("reprocess: %d/%d tracks passed QC+dedupe (%.0f%%)", accepted, len(tracks), pass_rate * 100)

    if new_local_files:
        synced = storage.sync_tracks_to_kaggle_dataset(
            new_local_files, f"Reprocess {BATCH_ID}: +{len(new_local_files)} track(s)", state
        )
        if not synced:
            for stored in newly_appended:
                if stored in state["track_pool"]:
                    state["track_pool"].remove(stored)
            alert_failure(
                "Reprocess batch -- Kaggle Dataset sync failed again",
                f"{len(new_local_files)} accepted track(s) from the {BATCH_ID} reprocess "
                "could not be pushed to the Kaggle Dataset even with the fix. Needs manual investigation.",
                cfg=cfg,
            )
            return 1

    # Re-evaluate the circuit breaker against the CORRECTED pass rate, not
    # the broken 7/26=27% figure that originally tripped it.
    if tracks and pass_rate < pass_rate_alert_below:
        state["circuit_breaker_paused"] = True
        log.warning(
            "reprocess: corrected pass rate %.0f%% is still below threshold, leaving paused",
            pass_rate * 100,
        )
    else:
        state["circuit_breaker_paused"] = False
        log.info("reprocess: corrected pass rate clears the threshold, resuming A1 submissions")

    storage.save(state)
    storage.commit_and_push(f"Reprocess {BATCH_ID} with fixed dedupe threshold + storage sync")
    send(
        f"Reprocess {BATCH_ID}: corrected result -- {accepted}/{len(tracks)} tracks passed "
        f"({pass_rate:.0%}). circuit_breaker_paused={state['circuit_breaker_paused']}",
        cfg=cfg,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
