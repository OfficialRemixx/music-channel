"""Storage layer (config.yaml's `storage:` key). Default implementation:
commits `state.json` + generated track/mix files into this repo's own
`storage/` folder, so A1/A2/B (each a separate, stateless GitHub Actions run)
can hand data to each other without a second Google Drive OAuth consent.
Swapping to `storage: gdrive`/`storage: hf_dataset`/`storage: kaggle_dataset`
later means adding a sibling module behind this same load/save/store_track_file
interface -- nothing upstream (a1/a2/b scripts) needs to change.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common.config import REPO_ROOT
from common.logging_setup import get_logger
from common.state import load_state, save_state

log = get_logger(__name__)

STORAGE_DIR = REPO_ROOT / "storage"
STATE_PATH = STORAGE_DIR / "state.json"
TRACKS_DIR = STORAGE_DIR / "tracks"
MIXES_DIR = STORAGE_DIR / "mixes"


def load() -> dict[str, Any]:
    return load_state(STATE_PATH)


def save(state: dict[str, Any]) -> None:
    save_state(STATE_PATH, state)


def store_track_file(track: dict[str, Any]) -> dict[str, Any]:
    """Copies a QC-accepted track's audio into storage/tracks/ and rewrites
    its path to be repo-relative, so the next (fresh-checkout) workflow run
    can find it via resolve_track_path(). Returns a new dict -- the caller's
    track dict is left untouched."""
    source = track.get("normalized_path") or track.get("path")
    if not source or not Path(source).exists():
        raise FileNotFoundError(f"storage.store_track_file: missing source audio for {track!r}")

    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    track_id = track.get("track_id") or Path(source).stem
    dest = TRACKS_DIR / f"{track_id}.wav"
    shutil.copyfile(source, dest)

    stored = dict(track)
    stored["path"] = str(dest.relative_to(REPO_ROOT))
    stored.pop("normalized_path", None)
    return stored


def resolve_track_path(repo_relative_path: str) -> str:
    """Inverse of store_track_file's path rewrite -- turns the repo-relative
    path saved in state.json back into an absolute path for this checkout."""
    return str(REPO_ROOT / repo_relative_path)


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
        subprocess.run(["git", "push"], check=True, cwd=REPO_ROOT)
        return True
    except subprocess.CalledProcessError:
        log.exception("storage.commit_and_push: git commit/push failed")
        return False
