"""Shared YouTube Data API v3 client builder.

Auth: refresh-token based OAuth (google-auth), no interactive browser flow at
runtime -- YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN env vars (set as
repo secrets), obtained once via Google OAuth Playground against a "Web
application" OAuth client whose authorized redirect URI is
https://developers.google.com/oauthplayground.

Extracted out of upload.py so trend.py (read-only videos.list calls) and
upload.py (write calls) share one credentials/service path instead of two
independent OAuth implementations.
"""
from __future__ import annotations

import os

from common.logging_setup import get_logger

log = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_credentials():
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


def get_service():
    from googleapiclient.discovery import build

    creds = get_credentials()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)
