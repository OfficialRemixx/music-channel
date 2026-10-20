"""Section 5.5: cover/background images (Pixabay, license+credit stored),
Pillow style randomiser within the series bible, Pomodoro/now-playing + chapter
card layers, ffmpeg encode.

Image provider: Pixabay (https://pixabay.com/api/docs/) -- Pexels paused new
API key issuance at setup time, so Pixabay is the active provider. Pixabay
content is served under the Pixabay Content License (free for commercial use,
no attribution required, but we store the credit anyway for the series bible).
"""
from __future__ import annotations

import os
import random
import subprocess
import tempfile
from pathlib import Path

import requests

from common.config import load_config
from common.logging_setup import get_logger

log = get_logger(__name__)

PIXABAY_API_BASE = "https://pixabay.com/api/"

# Mood -> Pixabay search query. Keep generic/abstract -- no proper nouns,
# no real places or people (Section 5.3/R4 public-text rules apply to
# imagery too: nothing that could misrepresent the content as licensed
# footage of a real location/artist).
MOOD_QUERY = {
    "deep_focus": "cozy study lamp night rain window",
    "lofi": "warm desk lamp bokeh aesthetic",
    "ambient": "soft clouds gradient abstract",
    "calm": "slow water ripple minimal",
}


def fetch_cover_image(mood: str, cfg: dict | None = None) -> dict | None:
    """Returns {path, source, license, credit} or None on failure/no key."""
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        log.warning("visuals.fetch_cover_image: PIXABAY_API_KEY not set, skipping")
        return None

    cfg = cfg or load_config()
    query = MOOD_QUERY.get(mood, MOOD_QUERY["deep_focus"])
    try:
        resp = requests.get(
            PIXABAY_API_BASE,
            params={
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "orientation": "horizontal",
                "min_width": 1920,
                "min_height": 1080,
                "safesearch": "true",
                "per_page": 20,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        log.exception("visuals.fetch_cover_image: Pixabay request failed")
        return None

    hits = data.get("hits") or []
    if not hits:
        log.warning("visuals.fetch_cover_image: no results for mood=%s query=%r", mood, query)
        return None

    hit = random.choice(hits)
    image_url = hit.get("largeImageURL") or hit.get("webformatURL")
    if not image_url:
        return None

    out_dir = Path(tempfile.gettempdir()) / "music-channel-covers"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"pixabay-{hit['id']}.jpg"

    try:
        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()
        out_path.write_bytes(img_resp.content)
    except requests.RequestException:
        log.exception("visuals.fetch_cover_image: image download failed")
        return None

    return {
        "path": str(out_path),
        "source": "pixabay",
        "license": "Pixabay Content License",
        "credit": f"Image by {hit.get('user', 'Pixabay user')} on Pixabay (id {hit['id']})",
    }


def render_video(mix: dict, cover: dict | None, cfg: dict | None = None) -> str | None:
    """ffmpeg encode per Section 5.5 (12-24fps, -preset veryfast, CRF~23,
    AAC 256kbps, +faststart). Static cover image + the mixed audio track --
    Pillow style-randomised overlay layers (Pomodoro/now-playing/chapter
    cards) are a P2.5 refinement on top of this base encode, not required
    for a working video."""
    cfg = cfg or load_config()
    audio_path = mix.get("audio_path")
    if not mix.get("tracks") or not audio_path:
        log.info("visuals.render_video: empty mix or no audio_path, nothing to render")
        return None

    if cfg.get("dry_run", True):
        log.info("visuals.render_video: dry_run, not invoking ffmpeg")
        return "dryrun-video.mp4"

    if not cover or not cover.get("path"):
        log.error("visuals.render_video: no cover image available, cannot render")
        return None

    out_dir = Path(tempfile.gettempdir()) / "music-channel-renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{mix.get('batch_id', 'render')}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", cover["path"],
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-r", "16",
        "-preset", "veryfast", "-crf", "23",
        # Fit the cover into a standard 1080p 16:9 frame, letterboxing
        # rather than stretching, and force square pixels.
        #
        # This is not cosmetic: libx264 + yuv420p needs EVEN width and
        # height, and stock photos routinely have odd ones. A real B run
        # died here on a 'pixabay-2596809.jpg' cover -- ffmpeg exited
        # non-zero with "Error while filtering: Generic error in an
        # external library" and wrote no packets. Reproduced locally with
        # a 1279x853 image (fails) and confirmed this filter encodes the
        # same image fine (1920x1080 h264). Padding to a fixed size also
        # means every upload has consistent, YouTube-friendly dimensions
        # instead of whatever the cover happened to be.
        "-vf", ("scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"),
        "-c:a", "aac", "-b:a", "256k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        # ffmpeg's own stderr is the only thing that says WHY. It used to
        # be captured and then thrown away, so a failure logged nothing
        # but "encode failed" and a traceback -- useless for diagnosis.
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        log.error(
            "visuals.render_video: ffmpeg exited %s. Last stderr:\n%s",
            exc.returncode, "\n".join(stderr.splitlines()[-15:]) or "(empty)",
        )
        return None
    except subprocess.TimeoutExpired:
        log.exception("visuals.render_video: ffmpeg timed out")
        return None

    return str(out_path)
