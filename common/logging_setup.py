"""Structured logging with automatic secret masking (R5 / Section 7:
'Log masking; never echo tokens'). Import get_logger(__name__) everywhere."""
from __future__ import annotations

import logging
import os
import re
import sys

_SECRET_ENV_NAMES = (
    "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "KAGGLE_KEY", "GDRIVE_REFRESH_TOKEN",
    "HF_TOKEN", "PEXELS_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN",
    "MODAL_TOKEN_SECRET",
)


class SecretMaskingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets = [v for name in _SECRET_ENV_NAMES if (v := os.environ.get(name))]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self._secrets:
            if s and s in msg:
                msg = msg.replace(s, "***REDACTED***")
        # Generic bearer/token pattern catch-all
        msg = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{10,}", r"\1***REDACTED***", msg)
        record.msg = msg
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        handler.addFilter(SecretMaskingFilter())
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    return logger
