"""Publishes live status events over Redis Pub/Sub so the Go API can relay
them to WebSocket clients without polling Postgres (spec 8.3, ADR-0010).
"""

from __future__ import annotations

import json

import redis

#: Must match the Go API's subscriber channel name exactly (ADR-0010).
CHANNEL_NAME = "analyses:events"


class RedisEventPublisher:
    """`EventPublisher` backed by a Redis Pub/Sub channel.

    Publishing is best-effort: a dropped event only costs a WebSocket
    client one intermediate update, since `GET /analyses/{id}` (spec 8.3's
    mandated REST fallback) always reflects the true, Postgres-persisted
    state on the next poll.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def publish_stage(self, analysis_id: str, name: str, index: int, total: int) -> None:
        self._publish(
            {
                "analysis_id": analysis_id,
                "type": "stage",
                "name": name,
                "index": index,
                "total": total,
            }
        )

    def publish_done(self, analysis_id: str) -> None:
        self._publish({"analysis_id": analysis_id, "type": "done"})

    def publish_failed(self, analysis_id: str, error_code: str, message: str) -> None:
        self._publish(
            {
                "analysis_id": analysis_id,
                "type": "failed",
                "error_code": error_code,
                "message": message,
            }
        )

    def _publish(self, payload: dict[str, object]) -> None:
        self._client.publish(CHANNEL_NAME, json.dumps(payload))
