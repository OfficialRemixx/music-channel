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
from qc import check_track

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

    provider = KaggleAceStepProvider(cfg)
    still_pending = []

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
        for i, track in enumerate(tracks):
            track["track_id"] = f"{entry['batch_id']}-{i:03d}"
            result = check_track(track["path"])
            if not result.accepted:
                log.info("a2_collect: rejected %s (%s)", track["track_id"], result.reasons)
                continue
            track.update({
                "score": result.score,
                "bpm": result.bpm,
                "key": result.key,
                "loudness_dbfs": result.loudness_dbfs,
                "normalized_path": result.normalized_path,
                "used_count": 0,
            })
            state["track_pool"].append(storage.store_track_file(track))
            accepted += 1

        send(
            f"A2 collect: batch {entry['batch_id']} finished -- "
            f"{accepted}/{len(tracks)} tracks passed QC",
            cfg=cfg,
        )

    state["pending_generation_jobs"] = still_pending
    storage.save(state)
    storage.commit_and_push("A2: collect + QC results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
