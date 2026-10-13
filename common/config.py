"""Central config loader. Everything in the pipeline reads config through here,
never hard-coded niche/schedule/provider/threshold values (R-rule in master prompt)."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Environment can force dry_run on even if config says off (safety net for CI).
    if os.environ.get("FORCE_DRY_RUN", "").lower() in ("1", "true", "yes"):
        cfg["dry_run"] = True
    return cfg


def is_dry_run(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("dry_run", True))


def is_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """Checks the kill switch (R8). Call this at the top of every workflow's
    entrypoint before doing anything with side effects."""
    cfg = cfg or load_config()
    return bool(cfg.get("pipeline_enabled", False))
