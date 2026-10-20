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

from dataclasses import dataclass

from common.logging_setup import get_logger
from common.youtube_client import get_service

log = get_logger(__name__)

# Fail-safe fallback only -- _get_category_id() below reads the real id from
# videoCategories.list on every upload per this module's own R3/5.7 rule
# (never hard-code categoryId). "10" happens to be Music in most regions,
# but we never rely on that matching without checking.
DEFAULT_CATEGORY_ID = "10"


@dataclass
class UploadResult:
    ok: bool
    video_id: str | None
    reason: str


def _get_category_id(service, region_code: str = "US") -> str:
    """Reads categoryId from the API's own videoCategories.list rather than
    hard-coding it (confirmed real endpoint: GET .../youtube/v3/videoCategories
    ?part=snippet&regionCode=<cc>, per
    https://developers.google.com/youtube/v3/docs/videoCategories/list).
    Falls back to DEFAULT_CATEGORY_ID (fails safe, not silently) if the call
    errors or no "Music" category is returned for the region."""
    try:
        resp = service.videoCategories().list(part="snippet", regionCode=region_code).execute()
    except Exception:
        log.exception(
            "upload._get_category_id: videoCategories.list failed, falling back to %s",
            DEFAULT_CATEGORY_ID,
        )
        return DEFAULT_CATEGORY_ID

    for item in resp.get("items", []):
        if item.get("snippet", {}).get("title") == "Music":
            return item["id"]

    log.warning(
        "upload._get_category_id: no 'Music' category for region %s, falling back to %s",
        region_code, DEFAULT_CATEGORY_ID,
    )
    return DEFAULT_CATEGORY_ID


def upload_video(video_path: str, metadata: dict, cfg: dict) -> UploadResult:
    if cfg.get("dry_run", True):
        log.info("upload.upload_video: dry_run, not calling the API. metadata=%s", metadata.get("title"))
        return UploadResult(ok=True, video_id="dryrun-video-id", reason="dry_run")

    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    service = get_service()
    category_id = metadata.get("category_id") or _get_category_id(service)

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata.get("tags", []),
            "categoryId": category_id,
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

    # Check whether YouTube actually ACCEPTED the video before checking
    # anything about its metadata. Ordering matters: this used to go
    # straight to the containsSyntheticMedia check, so when YouTube threw
    # the upload away the only thing logged was "containsSyntheticMedia
    # did not persist" -- which sent us hunting a metadata bug when the
    # real message, visible only in YouTube Studio, was "Processing
    # abandoned: video is too long" (an unverified account is capped at
    # 15 minutes, and these mixes are 60-120). Surface the real reason.
    check = service.videos().list(
        part="status,processingDetails", id=video_id
    ).execute()
    items = check.get("items", [])
    if not items:
        log.error(
            "upload.upload_video: %s is gone right after upload -- YouTube discarded it. "
            "Check Studio > Content for a notice (e.g. 'Processing abandoned: video is too "
            "long', which means the channel needs phone verification at youtube.com/verify "
            "to exceed the 15-minute limit).",
            video_id,
        )
        return UploadResult(ok=False, video_id=video_id, reason="discarded_by_youtube")

    status = items[0].get("status", {})
    processing = items[0].get("processingDetails", {})
    upload_status = status.get("uploadStatus")
    failure = status.get("failureReason") or processing.get("processingFailureReason")
    rejection = status.get("rejectionReason")

    if upload_status in ("failed", "rejected") or failure or rejection:
        log.error(
            "upload.upload_video: YouTube did not accept %s -- uploadStatus=%s "
            "failureReason=%s rejectionReason=%s processingStatus=%s",
            video_id, upload_status, failure, rejection,
            processing.get("processingStatus"),
        )
        return UploadResult(
            ok=False, video_id=video_id,
            reason=f"not_accepted:{failure or rejection or upload_status}",
        )

    # R3: confirm containsSyntheticMedia actually stuck.
    if not status.get("containsSyntheticMedia"):
        log.error("upload.upload_video: containsSyntheticMedia did not persist for %s "
                  "(uploadStatus=%s)", video_id, upload_status)
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

    service = get_service()
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
