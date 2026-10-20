"""Shared helpers for every CC/public-domain track source (Jamendo,
Freesound, Openverse -- see cc_library.py, freesound_source.py,
openverse_source.py). Pulled out so all three sources apply the exact
same license allow-list, the same defensive URL-parsing, the same
duration window and the same retry behaviour, instead of three copies
that silently drift apart.

The duration window is derived from qc.py's REAL constants rather than
re-stated here. That mismatch was a real bug: the sources accepted
tracks >=20s while qc.py rejects anything under 45s, so a live A1c run
fetched, downloaded and bass-boosted 26 tracks only for QC to throw out
25 of them as duration_out_of_range (24-30s). Importing the constant
means the two can't disagree again.
"""
from __future__ import annotations

import time
from datetime import date
from urllib.parse import urlparse

import requests

from common.logging_setup import get_logger
from qc import MAX_DURATION_S as _QC_MAX_DURATION_S
from qc import MIN_DURATION_S as _QC_MIN_DURATION_S

log = get_logger(__name__)

# A few seconds above qc.py's floor: a source reports its own stored
# duration, which can differ slightly from what the decoder measures
# after download/trim/bass-boost, and landing just under the floor wastes
# the whole download.
_DURATION_MARGIN_S = 5.0
MIN_SOURCE_DURATION_S = _QC_MIN_DURATION_S + _DURATION_MARGIN_S

# cc_library.py trims every track to MAX_TRACK_DURATION_S (under qc.py's
# ceiling) before QC sees it, so there is no point fetching a much longer
# file: the extra minutes are downloaded and decoded only to be thrown
# away. This was set to 4x qc's ceiling (12-minute tracks) and a live run
# spent >14 minutes on download+decode+bass-boost and got killed by the
# workflow timeout. 1.5x leaves room to pick a track without paying for
# audio that gets cut.
MAX_SOURCE_DURATION_S = _QC_MAX_DURATION_S * 1.5

ALLOWED_LICENSES = {"CC0", "CC-BY", "public-domain"}
FORBIDDEN_LICENSE_MARKERS = ("NC", "SA", "ND")  # reject if any appear in a license string


def rotating_tags(cfg: dict, today: date | None = None) -> list[str]:
    """The tag set for this run, rotated by date.

    Every source is asked for its most popular tracks, which means an
    identical query returns identical tracks -- fine weekly, useless
    daily: the second run onward would fetch the same tracks and dedupe
    would reject all of them. Rotating which tags we ask about keeps each
    run at the TOP of a different popularity ranking, which is where the
    genuinely popular material is (deep paging just reaches obscure
    tracks -- measured on Jamendo: 400k-900k listens at offset 0, ~500 at
    offset 78).

    Falls back to the flat `tags` string when no tag_pool is configured.
    """
    cc = cfg.get("music", {}).get("cc_library", {})
    pool = cc.get("tag_pool") or []
    if not pool:
        return [t for t in (cc.get("tags") or "").split() if t]

    per_run = max(1, int(cc.get("tags_per_run", 4)))
    day = (today or date.today()).toordinal()
    start = (day * per_run) % len(pool)
    # Wrap around so the window is always per_run long.
    return [pool[(start + i) % len(pool)] for i in range(min(per_run, len(pool)))]


def license_from_ccurl(ccurl: str | None) -> str | None:
    """Parses a creativecommons.org license URL into one of
    ALLOWED_LICENSES, or None if it's anything else (including any URL
    this doesn't recognize -- fails safe/rejects rather than guessing).
    Jamendo, Freesound and Openverse all surface a CC URL for a track's
    license (in different fields), so this one parser covers all three.
    """
    if not ccurl:
        return None
    path = urlparse(ccurl).path.lower()
    if "publicdomain/zero" in path:
        return "CC0"
    if "/licenses/by/" in path:  # exactly "by", not "by-nc", "by-sa", "by-nd", etc.
        return "CC-BY"
    return None


def fetch_results_with_retry(
    url: str,
    params: dict,
    results_key: str = "results",
    headers: dict | None = None,
    attempts: int = 4,
    backoff_s: float = 1.5,
) -> list[dict]:
    """GETs `url` and returns response[results_key], retrying when the
    request fails OR when it succeeds with an EMPTY result list.

    Retrying on an empty-but-successful response looks odd, so: Jamendo
    intermittently answers an identical, valid request with HTTP 200,
    headers.status "success", no warning -- and zero results. Measured
    live on 2026-10-19, six identical requests returned
    [78, 0, 0, 78, 78, 78], i.e. it fails that way roughly a third of
    the time. A real A1c run hit exactly this and logged a mystifying
    "0/0 candidates", contributing nothing for the whole run. Freesound
    and Openverse haven't shown this, but the retry is harmless there
    and keeps one code path.

    Never raises: returns [] once attempts are exhausted, so a single
    bad source can't take down a whole A1c run.
    """
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            results = resp.json().get(results_key) or []
        except Exception:
            log.warning("fetch_results_with_retry: request failed (attempt %d/%d) for %s",
                        attempt, attempts, url, exc_info=True)
            results = []
        if results:
            if attempt > 1:
                log.info("fetch_results_with_retry: got %d results on attempt %d for %s",
                         len(results), attempt, url)
            return results
        if attempt < attempts:
            time.sleep(backoff_s * attempt)
    log.warning("fetch_results_with_retry: still empty after %d attempts for %s", attempts, url)
    return []


def has_forbidden_marker(license_url: str | None) -> bool:
    """Belt-and-braces check: reject if an excluded marker (NC/SA/ND)
    shows up anywhere in the raw license URL string, even if
    license_from_ccurl() didn't already reject it for some other reason."""
    return any(marker in (license_url or "").upper() for marker in FORBIDDEN_LICENSE_MARKERS)
