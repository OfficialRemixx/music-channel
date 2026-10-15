"""One-off migration (2026-10-15): the first 2 tracks in storage/state.json
were generated before storage.py moved audio off git onto a private Kaggle
Dataset -- their `path` still points at a file physically committed in this
repo (storage/tracks/*.wav) instead of a bare filename resolved through
storage.resolve_track_path(). This script uploads those existing files to
the Kaggle dataset, rewrites their `path` entries to the new bare-filename
scheme, and removes the physical .wav files from the git tree.

Safe to run only once -- it no-ops (skips) any track_pool entry whose path
is already a bare filename (no "/"), so a re-run after a partial failure is
harmless. Run via a one-off workflow_dispatch so it has the same
KAGGLE_API_TOKEN/KAGGLE_USERNAME secrets scripts/a2_collect.py uses in CI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage
from common.config import REPO_ROOT
from common.logging_setup import get_logger

log = get_logger(__name__)


def main() -> int:
    state = storage.load()
    to_migrate = [
        t for t in state["track_pool"]
        if "/" in t.get("path", "")  # old git-relative paths only; new ones are bare filenames
    ]
    if not to_migrate:
        log.info("migrate_tracks_to_kaggle: nothing to migrate, all paths already bare filenames")
        return 0

    local_files = []
    old_repo_paths = []
    for track in to_migrate:
        old_path = REPO_ROOT / track["path"]
        if not old_path.exists():
            log.error("migrate_tracks_to_kaggle: missing file %s for track %s, skipping",
                       old_path, track.get("track_id"))
            continue
        local_files.append(old_path)
        old_repo_paths.append(track["path"])
        track["path"] = old_path.name  # rewrite to the new bare-filename scheme

    if not local_files:
        log.error("migrate_tracks_to_kaggle: no files found on disk to migrate")
        return 1

    ok = storage.sync_tracks_to_kaggle_dataset(
        local_files, "Migrate pre-existing tracks from git to Kaggle Dataset", state
    )
    if not ok:
        log.error("migrate_tracks_to_kaggle: Kaggle dataset push failed, aborting (state.json not changed)")
        return 1

    storage.save(state)

    # Remove the now-migrated physical .wav files from the git tree -- git
    # never shrinks, so leaving them committed defeats the whole migration.
    subprocess.run(["git", "rm", "-q", *old_repo_paths], check=True, cwd=REPO_ROOT)
    storage.commit_and_push(
        f"Migrate {len(local_files)} pre-existing track(s) to Kaggle Dataset storage"
    )
    log.info("migrate_tracks_to_kaggle: migrated %d track(s)", len(local_files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
