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
  - Openverse (generate/openverse_source.py) -- verified against the LIVE
    API 2026-10-19 after a real run disproved the docs. It DOES require
    auth (OPENVERSE_CLIENT_ID/OPENVERSE_CLIENT_SECRET), and it's pointed
    at Wikimedia Commons only, since its other two upstreams are Jamendo
    and Freesound, which we already query directly here.
All three independently re-check license via cc_common.license_from_ccurl()
rather than trusting any single API's request-time filter params.

This is a genuinely different kind of source from any "trending commercial
song" request -- these are independent CC-licensed artists, and
public-domain works, who have already, explicitly, permitted remixing and
reuse. Jamendo needs a free developer client_id (JAMENDO_CLIENT_ID
env/secret, sign up at https://devportal.jamendo.com) and Freesound needs a
free API key (FREESOUND_API_KEY, sign up at https://freesound.org/apiv2/apply).
Openverse needs an app registered once via its own API, which yields
OPENVERSE_CLIENT_ID/OPENVERSE_CLIENT_SECRET. Any subset of these can be
missing: that source logs it and sits out, the others keep working.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import requests

from common.logging_setup import get_logger
from remix import analyze_track, bass_boost_track, mashup_tracks, pair_for_mashup

from .base import BatchSpec, Job, MusicProvider
from .cc_common import (
    MAX_SOURCE_DURATION_S,
    MIN_SOURCE_DURATION_S,
    fetch_results_with_retry,
    has_forbidden_marker,
    license_from_ccurl,
)
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
        # `stats` carries the real popularity numbers (rate_listened_total,
        # rate_downloads_total) so we can record how popular a track we
        # actually picked. Verified live 2026-10-20: ordering by
        # popularity_month returns tracks with hundreds of thousands of
        # listens (e.g. 888,901 / 430,509) instead of obscure uploads.
        "include": "musicinfo stats",
        "audioformat": "mp32",
        "order": "popularity_month",
        # Verified live 2026-10-19: without this, Jamendo happily returns
        # 36s tracks, which qc.py (45s floor) then rejects after we've
        # already downloaded and bass-boosted them. With it, the shortest
        # result was 52s.
        "durationbetween": f"{int(MIN_SOURCE_DURATION_S)}_{int(MAX_SOURCE_DURATION_S)}",
    }
    # Retries on an empty-but-successful response -- Jamendo does that to
    # identical requests about a third of the time. See
    # cc_common.fetch_results_with_retry's docstring for the measurements.
    results = fetch_results_with_retry(JAMENDO_TRACKS_URL, params)
    if not results:
        log.warning("cc_library._jamendo_candidates: no results after retries")
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
            "popularity": (item.get("stats") or {}).get("rate_listened_total"),
            "popularity_unit": "listens",
        })
        if len(candidates) >= limit:
            break
    log.info("cc_library._jamendo_candidates: %d/%d candidates passed filters", len(candidates), len(results))
    return candidates


def _all_candidates(cfg: dict, limit: int) -> list[dict]:
    """Gathers candidates from every source, oversampling each so that
    even if one or two sources are unconfigured/failing, the batch can
    still reach `limit`. Each source function already never raises.

    Returns AT MOST `limit` candidates, round-robin across the sources
    that returned anything. Both parts matter:

    - The cap: each source is asked for a full batch's worth so that one
      working source can carry the run alone, but every candidate past
      the cap is a wasted download+decode+bass-boost. Without it, two
      working sources produced 52 candidates for a 26-track batch and
      the job ran past the workflow's timeout and was cancelled mid-run.
    - Round-robin: truncating a concatenated list would fill the whole
      batch from whichever source happens to be first (Jamendo), so the
      other sources would only ever contribute when Jamendo came up
      short -- which defeats the point of having several sources.
    """
    per_source_limit = max(limit, 8)
    sources = (_jamendo_candidates, _freesound_candidates, _openverse_candidates)
    per_source: list[list[dict]] = []
    for source_fn in sources:
        try:
            found = source_fn(cfg, per_source_limit)
        except Exception:
            log.exception("cc_library._all_candidates: %s raised unexpectedly", source_fn.__module__)
            found = []
        per_source.append(found)

    interleaved: list[dict] = []
    for i in range(max((len(f) for f in per_source), default=0)):
        for found in per_source:
            if i < len(found):
                interleaved.append(found[i])
    selected = interleaved[:limit]

    log.info(
        "cc_library._all_candidates: %d found across %d sources, taking %d for this batch (%s)",
        len(interleaved), len(sources), len(selected),
        ", ".join(f"{fn.__module__.rsplit('.', 1)[-1]}={len(found)}"
                  for fn, found in zip(sources, per_source, strict=True)),
    )
    return selected


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

        cc_cfg = self.cfg.get("music", {}).get("cc_library", {})
        boost_db = cc_cfg.get("bass_boost_db", 6.0)
        mashup_share = float(cc_cfg.get("mashup_share", 0.0) or 0.0)

        staging = Path(tempfile.mkdtemp(prefix="cc-library-"))

        # Phase 1: fetch and trim everything first. Mashups need two tracks
        # in hand (and their key/BPM) before anything can be rendered, so
        # the download pass can't also be the remix pass any more.
        prepared: list[tuple[dict, Path]] = []
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
            # contract, not part of the transform itself.
            trimmed_path = staging / f"trimmed-{raw_path.stem}.wav"
            if not _trim_track(str(raw_path), str(trimmed_path), MAX_TRACK_DURATION_S):
                continue
            prepared.append((cand, trimmed_path))

        # Phase 2: pick which ones get blended. Only same-key / close-BPM
        # pairs are eligible (see remix.pair_for_mashup) -- everything
        # else, and everything over the share, gets the plain bass-boost.
        mashed: set[int] = set()
        pairs: list[tuple[int, int]] = []
        if mashup_share > 0 and len(prepared) >= 2:
            analyzed = [(i, *analyze_track(str(p))) for i, (_, p) in enumerate(prepared)]
            wanted = int(len(prepared) * mashup_share) // 2
            pairs = pair_for_mashup(analyzed)[:wanted]
            mashed = {i for pair in pairs for i in pair}
            log.info("cc_library.submit: %d mashup pair(s) from %d prepared track(s)",
                     len(pairs), len(prepared))

        def _entry(cand: dict, path: Path, kind: str, extra: dict | None = None) -> dict:
            attribution = (
                f"\"{cand['title']}\" by {cand['artist']}, {cand['license']} "
                f"({cand['license_url']}), remixed ({kind}), via {cand['provider']}. "
                f"Source: {cand['source_url']}"
            )
            if extra:
                attribution += (
                    f" Blended with \"{extra['title']}\" by {extra['artist']}, "
                    f"{extra['license']} ({extra['license_url']}), via {extra['provider']}. "
                    f"Source: {extra['source_url']}"
                )
            return {
                "path": str(path),
                "prompt": f"{cand['artist']} - {cand['title']} ({kind}, via {cand['provider']})",
                "model": f"cc_library/{cand['provider']}+{kind.replace(' ', '_')}",
                "license": cand["license"],
                "license_url": cand["license_url"],
                "source_url": cand["source_url"],
                "popularity": cand.get("popularity"),
                "popularity_unit": cand.get("popularity_unit"),
                "attribution": attribution,
            }

        for lead_i, bed_i in pairs:
            lead_cand, lead_path = prepared[lead_i]
            bed_cand, bed_path = prepared[bed_i]
            out_path = staging / f"mashup-{lead_path.stem}.wav"
            if not mashup_tracks(str(lead_path), str(bed_path), str(out_path)):
                mashed.discard(lead_i)  # fall back to bass-boosting them
                mashed.discard(bed_i)
                continue
            # BOTH sources are credited: a blend is a derivative of two
            # licensed works, and CC-BY attribution isn't optional.
            tracks.append(_entry(lead_cand, out_path, "mashup", extra=bed_cand))

        for i, (cand, trimmed_path) in enumerate(prepared):
            if i in mashed:
                continue
            boosted_path = staging / f"boosted-{trimmed_path.stem}.wav"
            if not bass_boost_track(str(trimmed_path), str(boosted_path), boost_db=boost_db):
                continue
            tracks.append(_entry(cand, boosted_path, "bass-boosted"))

        log.info("cc_library.submit: prepared %d/%d candidate tracks for batch %s "
                 "(%d mashup, %d bass-boosted)",
                 len(tracks), len(candidates), batch_spec.batch_id,
                 len(pairs), len(tracks) - len(pairs))
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
