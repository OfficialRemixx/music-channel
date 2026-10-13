"""Section 5.2 default provider: two-phase Kaggle + ACE-Step 1.5.

TODO before P1 (per master-prompt rule 0.2 -- do not guess):
1. Create a Kaggle account + API token (Section 9 step 3).
2. Read github.com/ace-step/ACE-Step-1.5's current README and pin a commit SHA
   for the notebook to install from. Record the license text at that commit
   (currently MIT on the HF cards, but master-prompt says re-check).
3. Read `kaggle kernels push`/`status`/`output` CLI docs for the exact metadata
   fields a kernel-metadata.json needs (this file does not exist yet).
This module is intentionally a stub until those are done -- it will refuse to
run for real (raises) but is fully wired for dry_run so CI/tests pass.
"""
from __future__ import annotations

from common.logging_setup import get_logger

from .base import BatchSpec, Job, MusicProvider

log = get_logger(__name__)


class KaggleAceStepProvider(MusicProvider):
    name = "kaggle_acestep"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dry_run = cfg.get("dry_run", True)

    def submit(self, batch_spec: BatchSpec) -> Job:
        if self.dry_run:
            log.info("kaggle_acestep.submit: dry_run, faking a pending job for %s",
                      batch_spec.batch_id)
            return Job(job_id=f"dryrun-{batch_spec.batch_id}", provider=self.name,
                       status="pending", meta={"dry_run": True})
        raise NotImplementedError(
            "Phase A1: push a Kaggle notebook via `kaggle kernels push` once "
            "kernel-metadata.json + the pinned ACE-Step commit exist."
        )

    def poll(self, job: Job) -> Job:
        if job.meta.get("dry_run"):
            job.status = "complete"
            return job
        raise NotImplementedError("Phase A2: `kaggle kernels status <slug>`.")

    def fetch(self, job: Job) -> list[dict]:
        if job.meta.get("dry_run"):
            return []
        raise NotImplementedError("Phase A2: `kaggle kernels output <slug>`.")
