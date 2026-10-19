"""A1c entrypoint (.github/workflows/a1c-cc-ingest.yml): a second,
independent track source alongside A1/A2's AI generation -- real
CC0/CC-BY-licensed tracks from Jamendo, bass-boosted (see remix.py), then
run through the SAME qc.py gate (silence/clipping/loudness/duration/
near-duplicate) as every other track before joining track_pool. Unlike
kaggle_acestep this isn't a two-phase GPU job: CCLibraryProvider.submit()
already does the real fetch+download+remix work and returns a complete
job, so this script does fetch->QC->dedupe->store->sync in one run, the
same shape as a2_collect.py's per-job loop body.

Runs on its own schedule, independent of the AI-generation circuit breaker
(music.circuit_breaker_paused) -- that breaker exists to stop wasting
Kaggle GPU quota, which doesn't apply here, so a problem with the AI
generation side should never block this source or vice versa.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

import storage
from common.config import is_enabled, load_config
from common.logging_setup import get_logger
from generate.base import BatchSpec
from generate.cc_library import CCLibraryProvider
from notify import alert_failure, send
from qc import check_track, fingerprint_similarity

log = get_logger(__name__)


def main() -> int:
    cfg = load_config()
    if not is_enabled(cfg):
        log.info("a1c_cc_ingest: pipeline_enabled=false, exiting")
        return 0

    cc_cfg = cfg.get("music", {}).get("cc_library", {})
    if not cc_cfg.get("enabled", False):
        log.info("a1c_cc_ingest: music.cc_library.enabled=false, exiting")
        return 0

    count = cc_cfg.get("tracks_per_batch", 26)
    dedupe_max = cfg.get("music", {}).get("dedupe_similarity_max", 0.995)
    pass_rate_alert_below = cfg.get("qc", {}).get("pass_rate_alert_below", 0.5)

    state = storage.load()
    batch_id = f"cc-{cfg['niche']['lane']}-{uuid.uuid4().hex[:8]}"

    provider = CCLibraryProvider(cfg)
    try:
        job = provider.submit(BatchSpec(prompts=[], tracks_per_batch=count, batch_id=batch_id))
    except Exception as exc:
        log.exception("a1c_cc_ingest: submit failed")
        alert_failure("A1c cc-library ingest", str(exc), cfg=cfg)
        return 1

    if job.status == "failed":
        alert_failure("A1c cc-library ingest", f"fetching candidates failed for {batch_id}", cfg=cfg)
        return 1

    tracks = provider.fetch(job)
    if not tracks:
        log.info("a1c_cc_ingest: no usable tracks returned for %s "
                  "(check JAMENDO_CLIENT_ID and music.cc_library_tags)", batch_id)
        return 0

    new_local_files: list[Path] = []
    newly_appended: list[dict] = []
    accepted = 0
    batch_fingerprints: list[list[float] | None] = [t.get("fingerprint") for t in state["track_pool"]]

    for i, track in enumerate(tracks):
        track["track_id"] = f"{batch_id}-{i:03d}"
        result = check_track(track["path"])
        if not result.accepted:
            log.info("a1c_cc_ingest: rejected %s (%s)", track["track_id"], result.reasons)
            continue

        max_sim = max(
            (fingerprint_similarity(result.fingerprint, fp) for fp in batch_fingerprints),
            default=0.0,
        )
        if max_sim >= dedupe_max:
            log.info(
                "a1c_cc_ingest: rejected %s as a near-duplicate (cosine similarity %.3f >= %.3f)",
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
    log.info("a1c_cc_ingest: %d/%d tracks passed QC+dedupe (%.0f%%)", accepted, len(tracks), pass_rate * 100)
    if tracks and pass_rate < pass_rate_alert_below:
        # Logged + alerted, but does NOT set the shared circuit_breaker_paused
        # flag (see module docstring) -- this source has no GPU quota to protect.
        alert_failure(
            "A1c cc-library ingest -- low pass rate",
            f"Batch {batch_id} passed only {accepted}/{len(tracks)} tracks "
            f"({pass_rate:.0%}, below qc.pass_rate_alert_below={pass_rate_alert_below:.0%}).",
            cfg=cfg,
        )

    if new_local_files:
        synced = storage.sync_tracks_to_kaggle_dataset(
            new_local_files, f"A1c: +{len(new_local_files)} cc_library track(s)", state
        )
        if not synced:
            for stored in newly_appended:
                if stored in state["track_pool"]:
                    state["track_pool"].remove(stored)
            alert_failure(
                "A1c cc-library ingest -- Kaggle Dataset sync failed",
                f"{len(new_local_files)} accepted cc_library track(s) could not be pushed "
                "to the Kaggle Dataset, so they were NOT added to track_pool.",
                cfg=cfg,
            )

    storage.save(state)
    storage.commit_and_push(f"A1c: cc_library ingest batch {batch_id} (+{accepted} tracks)")
    send(
        f"A1c cc-library ingest: batch {batch_id} finished -- {accepted}/{len(tracks)} tracks passed QC",
        cfg=cfg,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
