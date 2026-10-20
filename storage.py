"""Storage layer (config.yaml's `storage:` key, currently `kaggle_dataset`).
`state.json` (small: prompts/jobs/track metadata/logs) still lives in this
git repo via commit_and_push() -- but the actual audio bytes do NOT. Audio
goes to a private Kaggle Dataset instead (see sync_tracks_to_kaggle_dataset
and resolve_track_path below), because git never shrinks: every .wav ever
committed stays in the repo's history forever. Confirmed live (2026-10-15):
.git hit 17MB after just 2 committed tracks (~8.3MB each) -- that does not
scale to hundreds of tracks/week.

Kaggle Datasets versioning is a full-snapshot replace, not an additive merge
(confirmed against the Kaggle CLI's own documented behavior) -- so every
sync must download whatever's already in the dataset, add the new file(s),
and re-push the whole folder as the next version, or previously-uploaded
tracks would silently disappear from the dataset.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from common.config import REPO_ROOT
from common.logging_setup import get_logger
from common.state import load_state, save_state

log = get_logger(__name__)

STORAGE_DIR = REPO_ROOT / "storage"
STATE_PATH = STORAGE_DIR / "state.json"
MIXES_DIR = STORAGE_DIR / "mixes"

KAGGLE_DATASET_TITLE = "Music Channel Generated Tracks"
KAGGLE_DATASET_SLUG_NAME = "music-channel-tracks"

_dataset_read_cache: Path | None = None  # memoized per-process download of the dataset's current files


def load() -> dict[str, Any]:
    return load_state(STATE_PATH)


def save(state: dict[str, Any]) -> None:
    save_state(STATE_PATH, state)


_RESOLVED_SLUG: str | None = None


def _dataset_slug() -> str:
    """The dataset's real `owner/name` ref.

    Asking Kaggle rather than assembling it from KAGGLE_USERNAME, because
    those two are not the same string. Kaggle lowercases and strips the
    owner slug: this account's handle is shown as "Official_Remixx" while
    the dataset actually lives at `officialremixx/music-channel-tracks`.
    `datasets create` normalises the id it is given, so creation always
    worked -- but every later `datasets download` asked for the
    un-normalised ref and got a 404. Three consecutive A1c runs built 24
    tracks each and saved none of them because of it, and before the
    guard in sync_tracks_to_kaggle_dataset existed, that same 404 is what
    sent the code down the dataset-replacing `create` path.

    Falls back to the env-derived ref if the lookup fails, so a Kaggle
    outage degrades to the old behaviour instead of breaking outright.
    """
    global _RESOLVED_SLUG
    if _RESOLVED_SLUG:
        return _RESOLVED_SLUG

    username = os.environ.get("KAGGLE_USERNAME")
    if not username:
        raise RuntimeError(
            "storage._dataset_slug: KAGGLE_USERNAME is not set -- needed to address "
            "'<username>/music-channel-tracks', same requirement as kaggle_acestep.py's kernel id."
        )
    fallback = f"{username}/{KAGGLE_DATASET_SLUG_NAME}"

    try:
        result = subprocess.run(
            ["kaggle", "datasets", "list", "--mine", "-s", KAGGLE_DATASET_SLUG_NAME, "--csv"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                ref = line.split(",", 1)[0].strip()
                if ref.endswith("/" + KAGGLE_DATASET_SLUG_NAME):
                    if ref != fallback:
                        log.info("storage._dataset_slug: using Kaggle's own ref %s "
                                 "(KAGGLE_USERNAME would have given %s)", ref, fallback)
                    _RESOLVED_SLUG = ref
                    return ref
        log.info("storage._dataset_slug: dataset not found in `datasets list --mine`, "
                 "falling back to %s (expected on the very first run)", fallback)
    except Exception:
        log.warning("storage._dataset_slug: lookup failed, falling back to %s",
                    fallback, exc_info=True)

    return fallback


def _download_existing_dataset(dest: Path, attempts: int = 3) -> bool:
    """Downloads every file currently in the tracks dataset into dest.

    Retries, because a transient failure here is DESTRUCTIVE: the caller
    uses the result to decide between "version" (keeps existing files)
    and "create" (replaces the dataset wholesale). See
    sync_tracks_to_kaggle_dataset for the incident this caused.

    Returns False both when the dataset genuinely doesn't exist yet and
    when the download failed -- the caller MUST distinguish those two
    using state["kaggle_dataset_created"], never treat False as "safe to
    create".
    """
    dest.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["kaggle", "datasets", "download", "-d", _dataset_slug(), "-p", str(dest), "--unzip"],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            return True
        log.info(
            "storage._download_existing_dataset: attempt %d/%d failed -- dataset may not exist "
            "yet, or this is transient (%s)",
            attempt, attempts, result.stderr.strip()[:300],
        )
        if attempt < attempts:
            time.sleep(5 * attempt)
    return False


def store_track_file(track: dict[str, Any]) -> dict[str, Any]:
    """Copies a QC-accepted track's audio into a fresh local staging dir.
    `stored["path"]` becomes just the filename (the Kaggle dataset is the
    source of truth for *where* it lives, not this repo) and
    `stored["_local_path"]` is the real local file the caller must pass to
    sync_tracks_to_kaggle_dataset() -- it is popped before anything is
    written to state.json. Returns a new dict; the caller's dict is left
    untouched."""
    source = track.get("normalized_path") or track.get("path")
    if not source or not Path(source).exists():
        raise FileNotFoundError(f"storage.store_track_file: missing source audio for {track!r}")

    staging = Path(tempfile.mkdtemp(prefix="track-upload-"))
    track_id = track.get("track_id") or Path(source).stem
    filename = f"{track_id}.wav"
    dest = staging / filename
    shutil.copyfile(source, dest)

    stored = dict(track)
    stored["path"] = filename
    stored["_local_path"] = str(dest)
    stored.pop("normalized_path", None)
    return stored


def sync_tracks_to_kaggle_dataset(new_files: list[Path], message: str, state: dict[str, Any]) -> bool:
    """Pushes `new_files` to the private Kaggle tracks dataset as a new
    version (or creates the dataset on the very first call). Always
    downloads whatever's already there first and includes it in the new
    version's folder -- see the module docstring for why that's required.
    Mutates state["kaggle_dataset_created"] on a successful first create."""
    if not new_files:
        return False

    staging = Path(tempfile.mkdtemp(prefix="kaggle-tracks-sync-"))

    # REAL DATA LOSS, 2026-10-20: this used to be
    #     existed = state.get("kaggle_dataset_created") and _download_existing_dataset(staging)
    # so ANY download failure (a transient 404 from DownloadDataset showed
    # up repeatedly in real job logs) silently set existed=False and sent
    # us down the `create` path -- and `kaggle datasets create` REPLACES
    # the dataset with just the folder's contents. Every previously stored
    # track's audio was destroyed, the command exited 0 so sync reported
    # success, and nothing rolled back. The result: track_pool held 68
    # entries while the dataset held 41 files, and B aborted the first
    # time the mix touched one of the 27 orphans.
    #
    # The two cases must stay separate: a dataset that has never been
    # created is the ONLY case where `create` is safe. If we believe it
    # exists, a failed download is a hard error -- the caller rolls back
    # and alerts -- never a reason to overwrite it.
    already_created = bool(state.get("kaggle_dataset_created"))
    if already_created:
        if not _download_existing_dataset(staging):
            log.error(
                "storage.sync_tracks_to_kaggle_dataset: dataset %s is marked created but could "
                "not be downloaded. Refusing to push, because creating it fresh would delete "
                "every track already stored there.",
                _dataset_slug(),
            )
            return False
        existed = True
    else:
        existed = False

    for f in new_files:
        shutil.copyfile(f, staging / f.name)

    # Real bug fixed 2026-10-19: `kaggle datasets version` needs a
    # dataset-metadata.json in the folder being versioned JUST LIKE `datasets
    # create` does -- it's not only a first-create thing. `kaggle datasets
    # download --unzip` does NOT include this file among the downloaded data
    # files, so _download_existing_dataset() above never restores it, and the
    # very first real sync after the create (7-track batch, 2026-10-15)
    # failed with "Metadata file not found: dataset-metadata.json" -- this
    # write used to be gated on `if not existed`, so it silently never ran on
    # the version path. That failure wasn't checked by the caller either, so
    # a2_collect.py kept going and committed track_pool entries whose audio
    # was never actually pushed to the dataset. Writing this unconditionally
    # fixes the version path; a2_collect.py's return-value check (see there)
    # fixes the silent-continue part.
    metadata = {
        "title": KAGGLE_DATASET_TITLE,
        "id": _dataset_slug(),
        "licenses": [{"name": "unknown"}],  # private dataset of our own AI-generated audio
    }
    (staging / "dataset-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if not existed:
        result = subprocess.run(
            ["kaggle", "datasets", "create", "-p", str(staging), "-q"],
            capture_output=True, text=True, timeout=600,
        )
    else:
        result = subprocess.run(
            ["kaggle", "datasets", "version", "-p", str(staging), "-m", message, "-q"],
            capture_output=True, text=True, timeout=600,
        )

    if result.returncode != 0:
        log.error("storage.sync_tracks_to_kaggle_dataset: push failed: %s", result.stderr.strip())
        return False

    state["kaggle_dataset_created"] = True
    return True


def resolve_track_path(filename: str) -> str:
    """`filename` is the bare name saved in state.json (store_track_file's
    rewritten `path`) -- audio lives in the Kaggle dataset, not this repo.
    Downloads the whole dataset once per process (cached) and returns the
    local path inside that download."""
    global _dataset_read_cache
    if _dataset_read_cache is None:
        _dataset_read_cache = Path(tempfile.mkdtemp(prefix="kaggle-tracks-read-"))
        _download_existing_dataset(_dataset_read_cache)
    return str(_dataset_read_cache / filename)


def commit_and_push(message: str) -> bool:
    """Commits whatever changed under storage/ and pushes, using the
    GITHUB_TOKEN GitHub Actions already injects into the checkout's git
    credentials when the workflow has `permissions: contents: write`.
    No-ops outside CI (GITHUB_ACTIONS unset) so local dev/tests never touch
    git state, and no-ops cleanly when there's nothing new to commit."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        log.info("storage.commit_and_push: not running in GitHub Actions, skipping git push")
        return False
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                        check=True, cwd=REPO_ROOT)
        subprocess.run(
            ["git", "config", "user.email",
             "41898282+github-actions[bot]@users.noreply.github.com"],
            check=True, cwd=REPO_ROOT,
        )
        subprocess.run(["git", "add", "storage"], check=True, cwd=REPO_ROOT)
        unchanged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        if unchanged.returncode == 0:
            log.info("storage.commit_and_push: nothing changed under storage/, skipping commit")
            return False
        subprocess.run(["git", "commit", "-m", message], check=True, cwd=REPO_ROOT)
    except subprocess.CalledProcessError:
        log.exception("storage.commit_and_push: git commit failed")
        return False

    # Push with rebase-and-retry. A plain push loses the entire run when
    # anything else pushed meanwhile: a real A1c run built 24 tracks,
    # synced their audio to Kaggle, committed -- and then the push was
    # rejected ("fetch first") because another commit had landed during
    # the 13 minutes it was working. The tracks were gone, while their
    # audio stayed in the dataset as unreferenced files.
    #
    # This is not a rare race. A2 pushes state every 30 minutes, A1/A1c/
    # B/C/D all push too, and B now runs unattended -- overlapping pushes
    # are the normal case, not the exception. Rebasing is safe here
    # because every one of these jobs only ever appends to its own files
    # under storage/.
    for attempt in range(1, 4):
        push = subprocess.run(["git", "push"], cwd=REPO_ROOT,
                              capture_output=True, text=True)
        if push.returncode == 0:
            return True
        log.info("storage.commit_and_push: push rejected (attempt %d/3), rebasing onto "
                 "origin and retrying: %s", attempt, push.stderr.strip()[:200])
        rebase = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        if rebase.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=REPO_ROOT,
                           capture_output=True, text=True)
            log.error("storage.commit_and_push: rebase failed, giving up: %s",
                      rebase.stderr.strip()[:300])
            return False
    log.error("storage.commit_and_push: still could not push after 3 attempts")
    return False
