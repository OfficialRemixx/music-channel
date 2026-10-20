"""B entrypoint (.github/workflows/b-build.yml), runs after the workflow's
human approval Environment gate: builds a long mix from the QC-accepted track
pool, renders the video, generates metadata, and uploads it privately with a
scheduled publishAt at the next slot from config.yaml's publish_slots_utc.

Note (docs/RUNBOOK.md Section 9 step 5): until the YouTube API audit/quota
extension passes, uploaded videos stay private regardless of what's
requested here -- publishAt is honored only once that audit clears.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import storage
from common.config import is_enabled, load_config
from common.logging_setup import get_logger
from common.scheduling import next_publish_slot_utc
from mix import build_mix
from notify import alert_failure, send
from seo import generate_metadata
from upload import upload_video
from visuals import fetch_cover_image, render_video

log = get_logger(__name__)


def _mix_duration_min(mix: dict, cfg: dict) -> int:
    """Length of the mix we actually rendered, for the title.

    Measured from the rendered wav's own header rather than from the
    tracks' metadata. The old version summed t["details"]["duration_s"],
    but pool entries carry no "details" key, so the sum was always 0 and
    every title fell back to mix.minutes[0] -- the first real upload was
    45:14 and went out titled "60 min". A title that misstates the length
    is the kind of thing viewers bounce on.
    """
    audio_path = mix.get("audio_path")
    if audio_path:
        try:
            with wave.open(audio_path, "rb") as w:
                seconds = w.getnframes() / float(w.getframerate())
            if seconds > 0:
                return max(1, round(seconds / 60))
        except Exception:
            log.warning("b_build: could not read duration from %s, falling back",
                        audio_path, exc_info=True)
    chapters = mix.get("chapters") or []
    if chapters:
        return max(1, round(chapters[-1].get("start_s", 0) / 60)) or cfg["mix"]["minutes"][0]
    return cfg["mix"]["minutes"][0]


def main() -> int:
    cfg = load_config()
    if not is_enabled(cfg):
        log.info("b_build: pipeline_enabled=false, exiting")
        return 0

    state = storage.load()
    pool = [dict(t, path=storage.resolve_track_path(t["path"])) for t in state["track_pool"]]

    mix = build_mix(pool, cfg)
    if not mix["tracks"]:
        log.info("b_build: not enough QC-accepted tracks yet, nothing to build")
        return 0

    cover = fetch_cover_image(cfg["niche"]["lane"], cfg)
    video_path = render_video(mix, cover, cfg)
    if not video_path:
        alert_failure("B build", "render_video returned no output", cfg=cfg)
        return 1

    duration_min = _mix_duration_min(mix, cfg)
    metadata = generate_metadata(
        mood=cfg["niche"]["lane"],
        use_case="focus",
        series=cfg["niche"]["series"],
        vol=len(state["uploaded_videos"]) + 1,
        duration_min=duration_min,
        cfg=cfg,
        tracks=mix["tracks"],
    )
    metadata["publish_at"] = next_publish_slot_utc(cfg)

    result = upload_video(video_path, metadata, cfg)
    if not result.ok:
        alert_failure("B build", f"upload_video failed: {result.reason}", cfg=cfg)
        return 1

    used_paths = {t["path"] for t in mix["tracks"]}
    for t in state["track_pool"]:
        if storage.resolve_track_path(t["path"]) in used_paths:
            t["used_count"] = t.get("used_count", 0) + 1

    state["uploaded_videos"].append({
        "video_id": result.video_id,
        "title": metadata["title"],
        "status": "uploaded_private",
    })
    storage.save(state)
    storage.commit_and_push(f"B: build+upload '{metadata['title']}'")
    send(f"B build: uploaded '{metadata['title']}' (video_id={result.video_id}, private)", cfg=cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
