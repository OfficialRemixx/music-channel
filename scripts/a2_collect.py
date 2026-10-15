"""A2 entrypoint (.github/workflows/a2-collect.yml): cheap, frequent poll of
every pending Kaggle job. Exits in seconds when nothing is pending. On
completion: fetches the generated tracks, runs each through qc.check_track,
stores the accepted ones into storage/, and updates state.json."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import storage
from common.config import is_enabled, load_config
from common.logging_setup import get_logger
from generate.base import Job
from generate.kaggle_acestep import KaggleAceStepProvider
from notify import alert_failure, send
from qc import check_track, fingerprint_similarity

log = get_logger(__name__)


def main() -> int:
    cfg = load_config()
    if not is_enabled(cfg):
        log.info("a2_collect: pipeline_enabled=false, exiting")
        return 0

    state = storage.load()
    if not state["pending_generation_jobs"]:
        log.info("a2_collect: nothing pending")
        return 0

    dedupe_max = cfg.get("music", {}).get("dedupe_similarity_max", 0.92)
    pass_rate_alert_below = cfg.get("qc", {}).get("pass_rate_alert_below", 0.5)

    provider = KaggleAceStepProvider(cfg)
    still_pending = []
    new_local_files: list[Path] = []  # staged audio from store_track_file(), synced to Kaggle once at the end

    for entry in state["pending_generation_jobs"]:
        job = Job(job_id=entry["job_id"], provider=entry["provider"],
                  status=entry["status"], meta=entry["meta"])
        try:
            job = provider.poll(job)
        except Exception:
            log.exception("a2_collect: poll failed for %s", job.job_id)
            still_pending.append(entry)
            continue

        if job.status == "failed":
            alert_failure("A2 collect", f"Kaggle job {job.job_id} failed", cfg=cfg)
            continue  # drop it -- don't keep retrying a dead job forever
        if job.status != "complete":
            entry["status"] = job.status
            still_pending.append(entry)
            continue

        try:
            tracks = provider.fetch(job)
        except Exception:
            log.exception("a2_collect: fetch failed for %s", job.job_id)
            alert_failure("A2 collect", f"fetch failed for {job.job_id}", cfg=cfg)
            continue

        accepted = 0
        batch_fingerprints: list[list[float] | None] = [
            t.get("fingerprint") for t in state["track_pool"]
        ]  # cross-track dedupe checks against the whole existing pool PLUS
           # every track already accepted earlier in this same batch/run
        for i, track in enumerate(tracks):
            track["track_id"] = f"{entry['batch_id']}-{i:03d}"
            result = check_track(track["path"])
            if not result.accepted:
                log.info("a2_collect: rejected %s (%s)", track["track_id"], result.reasons)
                continue

            max_sim = max(
                (fingerprint_similarity(result.fingerprint, fp) for fp in batch_fingerprints),
                default=0.0,
            )
            if max_sim >= dedupe_max:
                log.info(
                    "a2_collect: rejected %s as a near-duplicate (cosine similarity %.3f >= %.3f)",
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
            batch_fingerprints.append(stored.get("fingerprint"))
            accepted += 1

        pass_rate = accepted / len(tracks) if tracks else 0.0
        if tracks and pass_rate < pass_rate_alert_below:
            state["circuit_breaker_paused"] = True
            alert_failure(
                "A2 collect -- circuit breaker tripped",
                f"Batch {entry['batch_id']} passed only {accepted}/{len(tracks)} tracks "
                f"({pass_rate:.0%}, below qc.pass_rate_alert_below={pass_rate_alert_below:.0%}). "
                "New A1 submissions are paused to avoid wasting GPU quota on a broken "
                "generation pipeline. To resume once the cause is fixed, set "
                "circuit_breaker_paused to false in storage/state.json.",
                cfg=cfg,
            )

        send(
            f"A2 collect: batch {entry['batch_id']} finished -- "
            f"{accepted}/{len(tracks)} tracks passed QC",
            cfg=cfg,
        )

    if new_local_files:
        storage.sync_tracks_to_kaggle_dataset(
            new_local_files, f"A2: +{len(new_local_files)} track(s)", state
        )

    state["pending_generation_jobs"] = still_pending
    storage.save(state)
    storage.commit_and_push("A2: collect + QC results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
