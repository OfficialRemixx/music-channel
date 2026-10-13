"""Section 5.2: MusicProvider interface. Every backend (kaggle_acestep,
cc_library, modal_acestep, lyria) implements this so upstream code never
cares which one is active -- that's a config.yaml switch only."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BatchSpec:
    prompts: list[dict]
    tracks_per_batch: int
    batch_id: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    job_id: str
    provider: str
    status: str  # "pending" | "running" | "complete" | "failed"
    meta: dict[str, Any] = field(default_factory=dict)


class MusicProvider(ABC):
    name: str

    @abstractmethod
    def submit(self, batch_spec: BatchSpec) -> Job:
        """Kick off generation. Must return immediately (two-phase, Section 5.2
        kaggle_acestep note: never hold a runner while waiting)."""

    @abstractmethod
    def poll(self, job: Job) -> Job:
        """Cheap status check. Called from the frequent, short A2 workflow."""

    @abstractmethod
    def fetch(self, job: Job) -> list[dict]:
        """Once complete: return [{path, prompt, seed, model, commit, license}]."""
