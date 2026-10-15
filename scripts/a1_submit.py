"""A1 entrypoint (.github/workflows/a1-generate-submit.yml): build this run's
prompt matrix and submit exactly one Kaggle generation batch, then persist the
pending job to storage/state.json so a2_collect.py can poll it later. Never
blocks on the GPU job -- submit() returns as soon as the kernel is pushed."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

import storage
from common.config import is_enabled, load_config
from common.logging_setup import get_logger
from generate.base import BatchSpec
from generate.kaggle_acestep import KaggleAceStepProvider, prompts_from_matrix
from notify import alert_failure
from trend import build_prompt_matrix

log = get_logger(__name__)


def main() -> int:
    cfg = load_config()
    if not is_enabled(cfg):
        log.info("a1_submit: pipeline_enabled=false, exiting")
        return 0

    state = storage.load()
    if state.get("circuit_breaker_paused"):
        log.info(
            "a1_submit: circuit_breaker_paused=true (a2_collect saw a batch's QC "
            "pass-rate fall below qc.pass_rate_alert_below), not submitting a new "
            "batch -- clear storage/state.json's circuit_breaker_paused flag to resume"
        )
        return 0

    if state["pending_generation_jobs"]:
        log.info("a1_submit: %d job(s) already pending, not submitting a new one",
                  len(state["pending_generation_jobs"]))
        return 0

    matrix = build_prompt_matrix(cfg)
    prompts = prompts_from_matrix(matrix)
    batch_id = f"{cfg['niche']['lane']}-{uuid.uuid4().hex[:8]}"

    provider = KaggleAceStepProvider(cfg)
    try:
        job = provider.submit(
            BatchSpec(prompts=prompts, tracks_per_batch=len(prompts), batch_id=batch_id)
        )
    except Exception as exc:
        log.exception("a1_submit: submit failed")
        alert_failure("A1 generate-submit", str(exc), cfg=cfg)
        return 1

    state["pending_generation_jobs"].append({
        "job_id": job.job_id,
        "provider": job.provider,
        "status": job.status,
        "meta": job.meta,
        "batch_id": batch_id,
    })
    storage.save(state)
    storage.commit_and_push(f"A1: submit batch {batch_id} ({len(prompts)} prompts)")
    log.info("a1_submit: submitted %s (%d prompts)", job.job_id, len(prompts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
