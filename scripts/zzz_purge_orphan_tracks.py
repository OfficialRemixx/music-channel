"""ONE-OFF REPAIR (2026-10-20) -- delete this script and its workflow once run.

Cleans up after the destructive-sync bug fixed in storage.py the same day:
a failed dataset download silently sent sync_tracks_to_kaggle_dataset down
the `create` path, which REPLACES the Kaggle dataset, wiping tracks that
were already stored. state.json kept the track_pool entries, so the pool
ended up with 68 entries pointing at only 41 real files, and b_build.py
aborted the moment a mix picked an orphan ("missing audio file for track").

This downloads whatever the dataset really holds right now and drops every
track_pool entry with no matching file. It does NOT invent or re-fetch
anything -- A1c will top the pool back up on its normal schedule.

Deliberately does not touch the dataset itself: read-only there.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage
from common.config import load_config
from common.logging_setup import get_logger
from notify import send

log = get_logger(__name__)


def main() -> int:
    state = storage.load()
    pool = state["track_pool"]

    staging = Path(tempfile.mkdtemp(prefix="orphan-check-"))
    if not storage._download_existing_dataset(staging):
        log.error("zzz_purge: could not download the dataset -- refusing to purge anything, "
                  "since every entry would look like an orphan and the whole pool would be wiped")
        return 1

    present = {p.name for p in staging.iterdir() if p.is_file()}
    log.info("zzz_purge: dataset holds %d file(s)", len(present))

    kept, orphans = [], []
    for track in pool:
        name = track["path"].rsplit("/", 1)[-1]
        (kept if name in present else orphans).append(track)

    if not orphans:
        log.info("zzz_purge: no orphans -- pool of %d is fully backed by real audio", len(pool))
        return 0

    by_batch: dict[str, int] = {}
    for track in orphans:
        batch = track["path"].rsplit("/", 1)[-1].rsplit("-", 1)[0]
        by_batch[batch] = by_batch.get(batch, 0) + 1
    log.info("zzz_purge: dropping %d orphan(s) of %d: %s", len(orphans), len(pool), by_batch)

    state["track_pool"] = kept
    storage.save(state)
    storage.commit_and_push(
        f"Repair: drop {len(orphans)} track_pool entries whose audio was lost to the sync bug"
    )
    send(
        f"Repair: dropped {len(orphans)} orphaned track_pool entries "
        f"({by_batch}); pool is now {len(kept)} tracks, all backed by real audio.",
        cfg=load_config(),  # notify.send treats cfg=None as dry_run and wouldn't actually send
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
