"""ONE-OFF READ-ONLY DIAGNOSTIC (2026-10-20) -- delete after use.

B uploaded a real video and then failed its own post-upload check with
"containsSyntheticMedia did not persist". The docs don't say that field
is write-only, so rather than guess, ask the live API what it actually
returns for that video, with the channel owner's own credentials.

Prints, and changes NOTHING:
  - which channel these credentials belong to (also answers "where do
    uploads actually go?")
  - the full status block the API returns for the uploaded video
  - whether containsSyntheticMedia is present/true/absent
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.youtube_client import get_service

VIDEO_ID = os.environ.get("INSPECT_VIDEO_ID", "ulnT75sM5Lc")


def main() -> int:
    service = get_service()

    ch = service.channels().list(part="snippet,contentDetails", mine=True).execute()
    print("=== CHANNEL (these credentials upload here) ===")
    for item in ch.get("items", []):
        print("  id    :", item["id"])
        print("  title :", item["snippet"]["title"])
        print("  url   : https://www.youtube.com/channel/" + item["id"])

    # Don't rely on a video id transcribed from a log screenshot --
    # YouTube ids mix l/I/1 and are case sensitive. Ask the channel what
    # it actually holds.
    print("\n=== RECENT UPLOADS ON THIS CHANNEL ===")
    uploads_playlist = None
    for item in ch.get("items", []):
        uploads_playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]
    found_ids = []
    if uploads_playlist:
        pl = service.playlistItems().list(
            part="snippet,contentDetails", playlistId=uploads_playlist, maxResults=10
        ).execute()
        for entry in pl.get("items", []):
            vid = entry["contentDetails"]["videoId"]
            found_ids.append(vid)
            print(f"  {vid}  {entry['snippet']['title'][:70]}")
    if not found_ids:
        print("  (no uploads found on this channel at all)")

    target = VIDEO_ID if VIDEO_ID in found_ids else (found_ids[0] if found_ids else VIDEO_ID)
    print(f"\n=== VIDEO {target} ===")
    resp = service.videos().list(part="status,snippet,contentDetails", id=target).execute()
    items = resp.get("items", [])
    if not items:
        print("  NOT FOUND (wrong id, or not visible to these credentials)")
        return 0
    VIDEO_ID_RESOLVED = target

    video = items[0]
    print("  title       :", video["snippet"]["title"])
    print("  published   :", video["snippet"].get("publishedAt"))
    print("  url         : https://www.youtube.com/watch?v=" + VIDEO_ID_RESOLVED)
    print("\n  full status block as returned by the API:")
    print(json.dumps(video["status"], indent=4))

    status = video["status"]
    print("\n  containsSyntheticMedia present in response? ",
          "containsSyntheticMedia" in status)
    print("  value                                      : ",
          status.get("containsSyntheticMedia"))
    print("  privacyStatus / uploadStatus               : ",
          status.get("privacyStatus"), "/", status.get("uploadStatus"))
    print("  rejectionReason                            : ",
          status.get("rejectionReason"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
