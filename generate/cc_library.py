"""Section 5.2 second source (added 2026-10-19, run equally alongside
kaggle_acestep -- see scripts/a1c_cc_ingest.py, a separate workflow from A1):
CC0/CC-BY/public-domain tracks only (never NC/SA/ND -- R2). Every track must
carry license + source URL + attribution text, stored alongside it, because
even properly-licensed free tracks can get a mistaken Content ID claim and
we need proof on hand.

Pulls from THREE independent real sources (per user request, 2026-10-19, to
not rely on a single source), each isolated in its own module so one
source's outage/missing key never blocks the others:
  - Jamendo (this file's _jamendo_candidates) -- verified against
    https://developer.jamendo.com/v3.0/tracks. Requires JAMENDO_CLIENT_ID.
  - Freesound (generate/freesound_source.py) -- verified against
    https://freesound.org/docs/api/. Requires FREESOUND_API_KEY.
  - Openverse (generate/openverse_source.py) -- verified against its public
    OpenAPI spec/client docs. No API key required; also surfaces Wikimedia
    Commons audio, a genuinely different pool from the other two.
All three independently re-check license via cc_common.license_from_ccurl()
rather than trusting any single API's request-time filter params.

This is a genuinely different kind of source from any "trending commercial
song" request -- these are independent CC-licensed artists, and
public-domain works, who have already, explicitly, permitted remixing and
reuse. Jamendo needs a free developer client_id (JAMENDO_CLIENT_ID
env/secret, sign up at https://devportal.jamendo.com) and Freesound needs a
free API key (FREESOUND_API_KEY, sign up at https://freesound.org/apiv2/apply)
-- Claude cannot create either account on the user's behalf (account
creation is a prohibited action), so a human has to do those one-time
signups. Openverse needs nothing and works as soon as cc_library.enabled=true.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import requests

from common.logging_setup import get_logger
from remix import bass_boost_track

from .base import BatchSpec, Job, MusicProvider
from .cc_common import has_forbidden_marker, license_from_ccurl
from .freesound_source import fetch_candidates as _freesound_candidates
from .openverse_source import fetch_candidates as _openverse_candidates

log = get_logger(__name__)

JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"
MAX_TRACK_DURATION_S = 170  # stay under qc.py's MAX_DURATION_S=180 after any trim


def _jamendo_candidates(cfg: dict, limit: int) -> list[dict]:
    client_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not client_id:
        log.info("cc_library: JAMENDO_CLIENT_ID not set, skipping this source")
        return []
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
    try:
        resp = requests.get(JAMENDO_TRACKS_URL, params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        log.exception("cc_library._jamendo_candidates: search request failed")
        return []

    candidates = []
    for item in results:
        license_url = item.get("license_ccurl")
        license_str = license_from_ccurl(license_url)
        if license_str not in {"CC0", "CC-BY"} or has_forbidden_marker(license_url):
            continue
        if not item.get("audiodownload_allowed", False) or not item.get("audiodownload"):
            continue
        candidates.append({
            "title": item.get("name"),
            "artist": item.get("artist_name"),
            "download_url": item["audiodownload"],
            "license": license_str,
            "license_url": license_url,
            "source_url": item.get("shareurl") or item.get("shorturl"),
            "provider": "jamendo",
        })
        if len(candidates) >= limit:
            break
    log.info("cc_library._jamendo_candidates: %d/%d candidates passed filters", len(candidates), len(results))
    return candidates


def _all_candidates(cfg: dict, limit: int) -> list[dict]:
    """Gathers candidates from every source, oversampling each so that
    even if one or two sources are unconfigured/failing, the batch can
    still reach `limit`. Each source function already never raises."""
    per_source_limit = max(limit, 8)
    sources = (_jamendo_candidates, _freesound_candidates, _openverse_candidates)
    all_candidates: list[dict] = []
    for source_fn in sources:
        try:
            found = source_fn(cfg, per_source_limit)
        except Exception:
            log.exception("cc_library._all_candidates: %s raised unexpectedly", source_fn.__module__)
            found = []
        all_candidates.extend(found)
    log.info(
        "cc_library._all_candidates: %d total candidates across %d sources",
        len(all_candidates), len(sources),
    )
    return all_candidates


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
        candidates = _all_candidates(self.cfg, batch_spec.tracks_per_batch)
        if not candidates:
            # Not a hard failure -- e.g. all three sources unconfigured/
            # down this run. a1c_cc_ingest.py already logs+alerts on an
            # empty track list, so a "complete" job with 0 tracks is the
            # right status here (not "failed").
            return Job(job_id=f"cc-{batch_spec.batch_id}", provider=self.name,
                        status="complete", meta={"tracks": []})

        staging = Path(tempfile.mkdtemp(prefix="cc-library-"))
        for cand in candidates:
            try:
                resp = requests.get(cand["download_url"], timeout=60)
                resp.raise_for_status()
            except Exception:
                log.warning("cc_library.submit: download failed for %s track %r",
                            cand.get("provider"), cand.get("title"))
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
                "prompt": f"{cand['artist']} - {cand['title']} (bass-boosted remix, via {cand['provider']})",
                "model": f"cc_library/{cand['provider']}+bass_boost",
                "license": cand["license"],
                "license_url": cand["license_url"],
                "source_url": cand["source_url"],
                "attribution": (
                    f"\"{cand['title']}\" by {cand['artist']}, {cand['license']} "
                    f"({cand['license_url']}), remixed (bass-boosted), via {cand['provider']}. "
                    f"Source: {cand['source_url']}"
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
