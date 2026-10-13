"""Section 5.10: weekly hash-check of the policy/docs pages this whole plan is
built on. If a hash changes, alert via Telegram so a human re-verifies before
the pipeline relies on stale assumptions. P0 stub -- network fetch wired in
P5 (keeps this file dependency-light: stdlib only, no requests needed to test
the hashing logic itself)."""
from __future__ import annotations

import hashlib

from common.logging_setup import get_logger

log = get_logger(__name__)

# URLs are placeholders for *which pages matter* (Section 3's "Re-check at"
# column) -- the exact current URLs must be confirmed against search results
# before this is wired for real, never guessed (rule 0.2).
WATCHED_PAGES = {
    "yt_monetization_policies": None,   # YouTube Help 1311392 (inauthentic content)
    "yt_ai_disclosure": None,           # YouTube Help 14328491
    "yt_ypp_eligibility": None,         # YPP eligibility page
    "yt_data_api_quota_cost": None,     # developers.google.com/youtube/v3/determine_quota_cost
    "yt_data_api_revision_history": None,
    "ace_step_releases": None,          # github.com/ace-step/ACE-Step-1.5/releases
    "kaggle_docs_quotas": None,
}


def hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_for_changes(previous_hashes: dict) -> dict:
    """Returns {page_key: {"changed": bool, "new_hash": str}}. P0 stub: no
    network call, every page reports changed=False so CI/tests are stable."""
    log.info("policy_watch.check_for_changes: P0 stub, no URLs configured yet")
    return {k: {"changed": False, "new_hash": previous_hashes.get(k)} for k in WATCHED_PAGES}
