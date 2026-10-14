"""B3 entrypoint (.github/workflows/b3-claim-gate.yml): checks every recently
uploaded video for a rejection/claim/audit-lock before it would ever go
public, and alerts if one shows up. Runs on its own cheap schedule, separate
from the upload step (Section 5.7 -- never a blocking wait inside B build)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import storage
from common.config import is_enabled, load_config
from common.logging_setup import get_logger
from notify import alert_failure
from upload import check_claim_gate

log = get_logger(__name__)

# Only re-check videos we haven't already cleared or already flagged --
# avoids re-alerting on the same video run after run.
PENDING_STATUSES = {"uploaded_private"}


def main() -> int:
    cfg = load_config()
    if not is_enabled(cfg):
        log.info("b3_claim_gate: pipeline_enabled=false, exiting")
        return 0

    state = storage.load()
    changed = False

    for video in state["uploaded_videos"]:
        if video.get("status") not in PENDING_STATUSES or not video.get("video_id"):
            continue

        result = check_claim_gate(video["video_id"], cfg)
        changed = True
        if result.ok:
            video["status"] = "claim_gate_clear"
            log.info("b3_claim_gate: %s clear", video["video_id"])
        else:
            video["status"] = f"claim_gate_failed:{result.reason}"
            alert_failure(
                "B3 claim-gate",
                f"video {video['video_id']} ('{video.get('title')}') failed: {result.reason}",
                cfg=cfg,
            )

    if changed:
        storage.save(state)
        storage.commit_and_push("B3: claim-gate results")
    else:
        log.info("b3_claim_gate: nothing pending to check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
