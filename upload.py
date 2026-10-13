"""Section 5.7: resumable upload via videos.insert with the claim gate.
P0 stub -- real OAuth + Data API wiring happens in P3, only after Section 9
step 4 (Google Cloud project, OAuth consent screen set to "Published") is
done by the human, and the current youtube/v3 docs for videos.insert /
videos.list have been read (rule 0.2). Never guess field names here.

Critical fields per master prompt R3/5.7 (to wire exactly, not approximately):
  status.containsSyntheticMedia = true
  status.selfDeclaredMadeForKids = false
  status.privacyStatus = "private" (+ status.publishAt for the scheduled slot)
  categoryId -> read from the API's videoCategories.list, never hard-coded
After upload: videos.list the same id back to confirm containsSyntheticMedia
actually stuck (R3), and to read status.uploadStatus / status.rejectionReason /
contentDetails.licensedContent for the claim gate before ever flipping
privacyStatus to public.
"""
from __future__ import annotations
from dataclasses import dataclass
from common.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class UploadResult:
    ok: bool
    video_id: str | None
    reason: str


def upload_video(video_path: str, metadata: dict, cfg: dict) -> UploadResult:
    if cfg.get("dry_run", True):
        log.info("upload.upload_video: dry_run, not calling the API. metadata=%s", metadata.get("title"))
        return UploadResult(ok=True, video_id="dryrun-video-id", reason="dry_run")
    raise NotImplementedError(
        "Wire OAuth (google-auth-oauthlib) + youtube.videos().insert() here once "
        "Section 9 step 4 is complete. Read the current docs before coding this."
    )


def check_claim_gate(video_id: str, cfg: dict) -> UploadResult:
    """Section 5.7 claim gate: must run in a LATER workflow run (B3), never as
    a blocking wait inside the upload job. Cancels publishAt and alerts on any
    rejection/claim; detects the API-audit lock by comparing requested vs
    actual privacyStatus (Section 9 step 5)."""
    if cfg.get("dry_run", True):
        log.info("upload.check_claim_gate: dry_run, assuming clean for %s", video_id)
        return UploadResult(ok=True, video_id=video_id, reason="dry_run")
    raise NotImplementedError("Wire videos.list(status,contentDetails) here. Read docs first.")
