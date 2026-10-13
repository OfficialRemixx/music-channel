"""Section 5.11: Telegram notifications. Daily report + immediate alerts on
failure/claim/audit-lock/policy_watch changes. P0 stub -- real bot call wired
once TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID exist (Section 9 step 6)."""
from __future__ import annotations
import os
from common.logging_setup import get_logger

log = get_logger(__name__)


def send(message: str, cfg: dict | None = None, urgent: bool = False) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    prefix = "[URGENT] " if urgent else ""
    if not token or not chat_id or (cfg or {}).get("dry_run", True):
        log.info("notify.send (dry_run/no-creds, not actually sent): %s%s", prefix, message)
        return False
    raise NotImplementedError(
        "Wire python-telegram-bot's Bot.send_message here once creds exist. "
        "Read the current library docs before coding this (rule 0.2)."
    )


def alert_failure(stage: str, log_link: str, cfg: dict | None = None) -> bool:
    return send(f"FAILURE in {stage}. Log: {log_link}", cfg=cfg, urgent=True)
