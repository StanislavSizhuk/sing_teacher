"""Live status events the Go API relays over WebSocket (spec 8.3)."""

from __future__ import annotations

from typing import Protocol


class EventPublisher(Protocol):
    """Consumer-declared interface; the Redis pub/sub implementation lives
    in `queue/events.py`."""

    def publish_stage(self, analysis_id: str, name: str, index: int, total: int) -> None: ...

    def publish_done(self, analysis_id: str) -> None: ...

    def publish_failed(self, analysis_id: str, error_code: str, message: str) -> None: ...

    def publish_queued(self, analysis_id: str, position: int) -> None:
        """An analysis just transitioned into `queued` outside the usual
        `POST /analyses` request/response cycle -- specifically, woken from
        `waiting_for_reference` once its song's cold path reached `ready`
        (spec 10.3, FR-16). The HTTP-driven path still pushes this over the
        WS hub directly (`ws.Hub.BroadcastPositions`); this is the
        worker-initiated equivalent for `SongPrepJobHandler`."""
        ...
