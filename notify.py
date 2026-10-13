"""Section 5.11: Telegram notifications. Daily report + immediate alerts on
failure/claim/audit-lock/policy_watch changes. Uses Telegram's HTTP Bot API
directly (sendMessage) rather than a client library, to keep the dependency
footprint small for CI runners."""
from __future__ import annotations
import os
import requests
from common.logging_setup import get_logger

log = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def send(message: str, cfg: dict | None = None, urgent: bool = False) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    prefix = "[URGENT] " if urgent else ""
    if not token or not chat_id or (cfg or {}).get("dry_run", True):
        log.info("notify.send (dry_run/no-creds, not actually sent): %s%s", prefix, message)
        return False
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": f"{prefix}{message}"[:4096]},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.error("notify.send: Telegram API returned not-ok: %s", data)
            return False
        return True
    except requests.RequestException:
        log.exception("notify.send: Telegram API call failed")
        return False


def alert_failure(stage: str, log_link: str, cfg: dict | None = None) -> bool:
    return send(f"FAILURE in {stage}. Log: {log_link}", cfg=cfg, urgent=True)
