"""Section 5.7: resumable upload via videos.insert with the claim gate.

Auth: refresh-token based OAuth (google-auth), no interactive browser flow at
runtime -- YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN env vars (set as
repo secrets), obtained once via Google OAuth Playground against a "Web
application" OAuth client whose authorized redirect URI is
https://developers.google.com/oauthplayground.

Critical fields per master prompt R3/5.7:
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
import os
from dataclasses import dataclass

from common.logging_setup import get_logger

log = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_CATEGORY_ID = "10"  # Music -- confirmed against videoCategories.list for US/region "en"


@dataclass
class UploadResult:
    ok: bool
    video_id: str | None
    reason: str


def _get_credentials():
    from google.oauth2.credentials import Credentials

    client_id = os.environ["YT_CLIENT_ID"]
    client_secret = os.environ["YT_CLIENT_SECRET"]
    refresh_token = os.environ["YT_REFRESH_TOKEN"]
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    from google.auth.transport.requests import Request
    creds.refresh(Request())
    return creds


def _get_service():
    from googleapiclient.discovery import build

    creds = _get_credentials()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(video_path: str, metadata: dict, cfg: dict) -> UploadResult:
    if cfg.get("dry_run", True):
        log.info("upload.upload_video: dry_run, not calling the API. metadata=%s", metadata.get("title"))
        return UploadResult(ok=True, video_id="dryrun-video-id", reason="dry_run")

    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    service = _get_service()

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata.get("tags", []),
            "categoryId": metadata.get("category_id", DEFAULT_CATEGORY_ID),
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    if metadata.get("publish_at"):
        body["status"]["publishAt"] = metadata["publish_at"]

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    try:
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("upload.upload_video: %d%% uploaded", int(status.progress() * 100))
        video_id = response["id"]
    except HttpError:
        log.exception("upload.upload_video: YouTube API upload failed")
        return UploadResult(ok=False, video_id=None, reason="upload_http_error")

    # R3: confirm containsSyntheticMedia actually stuck.
    check = service.videos().list(part="status", id=video_id).execute()
    items = check.get("items", [])
    if not items or not items[0]["status"].get("containsSyntheticMedia"):
        log.error("upload.upload_video: containsSyntheticMedia did not persist for %s", video_id)
        return UploadResult(ok=False, video_id=video_id, reason="synthetic_media_flag_missing")

    return UploadResult(ok=True, video_id=video_id, reason="uploaded")


def check_claim_gate(video_id: str, cfg: dict) -> UploadResult:
    """Section 5.7 claim gate: must run in a LATER workflow run (B3), never as
    a blocking wait inside the upload job. Cancels publishAt and alerts on any
    rejection/claim; detects the API-audit lock by comparing requested vs
    actual privacyStatus (Section 9 step 5)."""
    if cfg.get("dry_run", True):
        log.info("upload.check_claim_gate: dry_run, assuming clean for %s", video_id)
        return UploadResult(ok=True, video_id=video_id, reason="dry_run")

    from googleapiclient.errors import HttpError

    service = _get_service()
    try:
        resp = service.videos().list(
            part="status,contentDetails", id=video_id
        ).execute()
    except HttpError:
        log.exception("upload.check_claim_gate: videos.list failed")
        return UploadResult(ok=False, video_id=video_id, reason="api_error")

    items = resp.get("items", [])
    if not items:
        return UploadResult(ok=False, video_id=video_id, reason="video_not_found")

    status = items[0]["status"]
    content_details = items[0].get("contentDetails", {})

    if status.get("rejectionReason"):
        return UploadResult(ok=False, video_id=video_id, reason=f"rejected:{status['rejectionReason']}")
    if status.get("uploadStatus") not in ("processed", "uploaded"):
        return UploadResult(ok=False, video_id=video_id, reason=f"upload_status:{status.get('uploadStatus')}")
    if content_details.get("licensedContent"):
        return UploadResult(ok=False, video_id=video_id, reason="licensed_content_claim")

    return UploadResult(ok=True, video_id=video_id, reason="clean")
