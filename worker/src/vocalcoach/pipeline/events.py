"""Live status events the Go API relays over WebSocket (spec 8.3)."""

from __future__ import annotations

from typing import Protocol


class EventPublisher(Protocol):
    """Consumer-declared interface; the Redis pub/sub implementation lives
    in `queue/events.py`."""

    def publish_stage(self, analysis_id: str, name: str, index: int, total: int) -> None: ...

    def publish_done(self, analysis_id: str) -> None: ...

    def publish_failed(self, analysis_id: str, error_code: str, message: str) -> None: ...
