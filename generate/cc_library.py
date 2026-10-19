"""Section 5.2 second source (added 2026-10-19, run equally alongside
kaggle_acestep -- see scripts/a1c_cc_ingest.py, a separate workflow from A1):
CC0/CC-BY/public-domain tracks only (never NC/SA/ND -- R2). Every track must
carry license + source URL + attribution text, stored alongside it, because
even properly-licensed free tracks can get a mistaken Content ID claim and
we need proof on hand.

Real source (verified against https://developer.jamendo.com/v3.0/tracks,
2026-10-19 -- master-prompt rule 0.2): the Jamendo API. Independent artists
upload under Creative Commons licenses they choose themselves; `ccnc=0`,
`ccnd=0`, `ccsa=0` ask the API for only the licenses with no restrictions
beyond attribution (CC-BY/CC0), and `license_ccurl` on every result is
parsed and re-checked locally against ALLOWED_LICENSES as a second,
don't-trust-the-request-params check. `audiodownload_allowed` is checked per
track since an artist can disable downloads even on an otherwise-matching
license. `content_id_free` (when present) is logged for extra confidence but
is not relied on alone.

This is a genuinely different source from any "trending commercial song"
request -- these are independent CC-licensed artists and public-domain
works who have already, explicitly, permitted remixing and reuse. Requires
a free Jamendo developer client_id (JAMENDO_CLIENT_ID env/secret) -- sign up
at https://devportal.jamendo.com; Claude cannot create that account on the
user's behalf (account creation is a prohibited action), so a human has to
do that one-time signup.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from common.logging_setup import get_logger
from remix import bass_boost_track

from .base import BatchSpec, Job, MusicProvider

log = get_logger(__name__)

ALLOWED_LICENSES = {"CC0", "CC-BY", "public-domain"}
FORBIDDEN_LICENSE_MARKERS = ("NC", "SA", "ND")  # reject if any appear in a license string

JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"
MAX_TRACK_DURATION_S = 170  # stay under qc.py's MAX_DURATION_S=180 after any trim


def _license_from_ccurl(ccurl: str | None) -> str | None:
    """Parses Jamendo's license_ccurl (a creativecommons.org URL) into one of
    ALLOWED_LICENSES, or None if it's anything else (including any URL this
    doesn't recognize -- fails safe/rejects rather than guessing)."""
    if not ccurl:
        return None
    path = urlparse(ccurl).path.lower()
    if "publicdomain/zero" in path:
        return "CC0"
    if "/licenses/by/" in path:  # exactly "by", not "by-nc", "by-sa", "by-nd", etc.
        return "CC-BY"
    return None


def _candidate_tracks(cfg: dict, limit: int) -> list[dict]:
    client_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "cc_library: JAMENDO_CLIENT_ID is not set. Free signup at "
            "https://devportal.jamendo.com, then set it as a repo secret."
        )
    lane_tags = cfg.get("music", {}).get("cc_library", {}).get("tags", "lofi chill ambient instrumental")
    params = {
        "client_id": client_id,
        "format": "json",
        "limit": min(limit * 3, 200),  # over-fetch: some will fail license/download checks
        "fuzzytags": lane_tags,
        "vocalinstrumental": "instrumental",
        "ccnc": "0",  # exclude non-commercial-only
        "ccnd": "0",  # exclude no-derivatives-only (we remix, so this matters)
        "ccsa": "0",  # exclude share-alike-only
        "include": "musicinfo",
        "audioformat": "mp32",
        "order": "popularity_month",
    }
    resp = requests.get(JAMENDO_TRACKS_URL, params=params, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])

    candidates = []
    for item in results:
        license_str = _license_from_ccurl(item.get("license_ccurl"))
        if license_str not in ALLOWED_LICENSES:
            continue
        if any(marker in (item.get("license_ccurl") or "").upper() for marker in FORBIDDEN_LICENSE_MARKERS):
            continue  # belt-and-braces: reject if an excluded marker shows up anywhere in the URL
        if not item.get("audiodownload_allowed", False) or not item.get("audiodownload"):
            continue
        candidates.append({
            "jamendo_id": item.get("id"),
            "title": item.get("name"),
            "artist": item.get("artist_name"),
            "download_url": item["audiodownload"],
            "license": license_str,
            "license_url": item.get("license_ccurl"),
            "source_url": item.get("shareurl") or item.get("shorturl"),
            "content_id_free": item.get("content_id_free"),
        })
        if len(candidates) >= limit:
            break
    return candidates


class CCLibraryProvider(MusicProvider):
    name = "cc_library"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dry_run = cfg.get("dry_run", True)

    def submit(self, batch_spec: BatchSpec) -> Job:
        """No async GPU job here -- fetching + downloading + remixing a
        batch of CC tracks takes seconds, not hours, so (unlike
        kaggle_acestep's genuinely two-phase submit/poll) this does the real
        work eagerly and hands back an already-complete job."""
        if self.dry_run:
            return Job(job_id=f"cc-dryrun-{batch_spec.batch_id}", provider=self.name,
                        status="complete", meta={"dry_run": True, "tracks": []})

        tracks: list[dict] = []
        try:
            candidates = _candidate_tracks(self.cfg, batch_spec.tracks_per_batch)
        except Exception:
            log.exception("cc_library.submit: fetching candidates failed")
            return Job(job_id=f"cc-{batch_spec.batch_id}", provider=self.name,
                        status="failed", meta={})

        staging = Path(tempfile.mkdtemp(prefix="cc-library-"))
        for cand in candidates:
            try:
                resp = requests.get(cand["download_url"], timeout=60)
                resp.raise_for_status()
            except Exception:
                log.warning("cc_library.submit: download failed for jamendo id %s", cand["jamendo_id"])
                continue

            raw_path = staging / f"raw-{uuid.uuid4().hex[:8]}.mp3"
            raw_path.write_bytes(resp.content)

            # Trim to stay under qc.py's MAX_DURATION_S before remixing --
            # done here (not in remix.py) since it's about fitting the QC
            # contract, not part of the bass-boost transform itself.
            trimmed_path = staging / f"trimmed-{raw_path.stem}.wav"
            if not _trim_track(str(raw_path), str(trimmed_path), MAX_TRACK_DURATION_S):
                continue

            boost_db = self.cfg.get("music", {}).get("cc_library", {}).get("bass_boost_db", 6.0)
            boosted_path = staging / f"boosted-{raw_path.stem}.wav"
            if not bass_boost_track(str(trimmed_path), str(boosted_path), boost_db=boost_db):
                continue

            tracks.append({
                "path": str(boosted_path),
                "prompt": f"{cand['artist']} - {cand['title']} (bass-boosted remix)",
                "model": "cc_library/jamendo+bass_boost",
                "license": cand["license"],
                "license_url": cand["license_url"],
                "source_url": cand["source_url"],
                "attribution": (
                    f"\"{cand['title']}\" by {cand['artist']}, {cand['license']} "
                    f"({cand['license_url']}), remixed (bass-boosted). Source: {cand['source_url']}"
                ),
            })

        log.info("cc_library.submit: prepared %d/%d candidate tracks for batch %s",
                  len(tracks), len(candidates), batch_spec.batch_id)
        return Job(job_id=f"cc-{batch_spec.batch_id}", provider=self.name,
                    status="complete", meta={"tracks": tracks})

    def poll(self, job: Job) -> Job:
        return job  # submit() already did the real work; status is already terminal

    def fetch(self, job: Job) -> list[dict]:
        return job.meta.get("tracks", [])


def _trim_track(source_path: str, out_path: str, max_duration_s: float) -> bool:
    try:
        from pydub import AudioSegment
    except ImportError:
        log.exception("cc_library._trim_track: pydub not installed")
        return False
    try:
        audio = AudioSegment.from_file(source_path)
    except Exception:
        log.exception("cc_library._trim_track: could not decode %s", source_path)
        return False
    trimmed = audio[: int(max_duration_s * 1000)]
    try:
        trimmed.export(out_path, format="wav")
    except Exception:
        log.exception("cc_library._trim_track: export failed for %s", out_path)
        return False
    return True
