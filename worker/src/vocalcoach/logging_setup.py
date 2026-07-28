"""Structured JSON logging (spec 17.2): one log line per record, with the
same `ts`/`level`/`msg` field names the Go API uses, so `docker compose
logs` reads consistently across both processes.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Never logged, even if a caller passes them in `extra` (spec 11.5, 17.2):
# passwords, tokens, verification codes, full emails, raw audio.
_RESERVED_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
