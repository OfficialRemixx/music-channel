"""Section 5.2 fallback provider: CC0/CC-BY/public-domain tracks only (never
NC/SA/ND -- R2). Every track must carry license + source URL + attribution
text, stored alongside it, because even properly-licensed free tracks can get
a mistaken Content ID claim and we need proof on hand.

TODO: pick and document the actual source(s) before P1 -- master-prompt rule
0.2 forbids guessing a repo/endpoint, so no source is hard-coded here yet.
"""
from __future__ import annotations
from .base import MusicProvider, BatchSpec, Job
from common.logging_setup import get_logger

log = get_logger(__name__)

ALLOWED_LICENSES = {"CC0", "CC-BY", "public-domain"}
FORBIDDEN_LICENSE_MARKERS = ("NC", "SA", "ND")  # reject if any appear in a license string


class CCLibraryProvider(MusicProvider):
    name = "cc_library"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def submit(self, batch_spec: BatchSpec) -> Job:
        return Job(job_id=f"cc-{batch_spec.batch_id}", provider=self.name, status="pending")

    def poll(self, job: Job) -> Job:
        job.status = "complete"
        return job

    def fetch(self, job: Job) -> list[dict]:
        log.warning("cc_library.fetch: no source configured yet -- returning empty. "
                    "See module docstring before relying on this provider.")
        return []
