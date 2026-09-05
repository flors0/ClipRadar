from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"AQ\.[0-9A-Za-z_-]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z._-]{12,}"),
    re.compile(r"1//[0-9A-Za-z._-]{12,}"),
    re.compile(
        r"(?i)(?:access_token|refresh_token|client_secret)\s*[:=]\s*[\"']?[^\"',\s&}]{6,}"
    ),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in SECRET_PATTERNS:
            message = pattern.sub("[redacted]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(root: Path) -> None:
    log_path = root / "clipradar.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
