"""Section 5.5: cover/background images (Pexels/Pixabay, license+credit stored),
Pillow style randomiser within the series bible, Pomodoro/now-playing + chapter
card layers, ffmpeg encode. P0 stub -- real image fetch + ffmpeg wiring is P2
work once a provider key exists (Section 9 step 6: Pexels API key)."""
from __future__ import annotations
from common.config import load_config
from common.logging_setup import get_logger

log = get_logger(__name__)


def fetch_cover_image(mood: str, cfg: dict | None = None) -> dict | None:
    """Returns {path, source, license, credit} or None. Stub until PEXELS_API_KEY
    exists and the current Pexels API docs have been read (rule 0.2)."""
    log.warning("visuals.fetch_cover_image: not wired yet (P0 stub), mood=%s", mood)
    return None


def render_video(mix: dict, cover: dict | None, cfg: dict | None = None) -> str | None:
    """ffmpeg encode per Section 5.5 (12-24fps, -preset veryfast, CRF~23,
    AAC 256kbps, +faststart). P0 stub -- filled in during P2 once mix.py and
    visuals fetch produce real inputs."""
    cfg = cfg or load_config()
    if not mix.get("tracks"):
        log.info("visuals.render_video: empty mix, nothing to render")
        return None
    raise NotImplementedError("ffmpeg encode pipeline: fill in during P2.")
