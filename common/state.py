"""Idempotent run-state. Every workflow must be safe to re-run after a crash
without double-processing or double-uploading (Section 5.0 / 5.7)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 1,
    "pending_generation_jobs": [],   # [{kernel_slug, submitted_at, batch_id}]
    "track_pool": [],                # [{track_id, path, score, license, used_count}]
    "uploaded_videos": [],           # [{video_id, published_at, status}]
    "weights": {},                   # prompt-family -> weight, see Section 4.9
    "quota_usage": {},               # bucket -> units used today
    "last_run": {},                  # workflow_name -> iso timestamp
}


def load_state(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(p)  # atomic on POSIX, avoids a half-written state.json on crash
