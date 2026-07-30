"""Integration tests: a real Redis is required (spec 15.1) -- these are the
Streams reliability semantics (XACK, reclaim, give-up) that a mock would
just assert back at itself.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Generator
from typing import Any, cast

import pytest
import redis

from vocalcoach.constants import MAX_CLAIM_ATTEMPTS, PENDING_CLAIM_MIN_IDLE
from vocalcoach.queue.consumer import GROUP_NAME, STREAM_NAME, Consumer

pytestmark = pytest.mark.integration

# See the identical comment in vocalcoach/queue/consumer.py: redis-py types
# these calls for both its sync and async clients at once.
_ReadGroupReply = list[tuple[str, list[tuple[str, dict[str, str]]]]]


@pytest.fixture
def redis_client() -> Generator[redis.Redis, None, None]:
    client = redis.Redis(
        host=os.environ.get("TEST_REDIS_HOST", "localhost"),
        port=int(os.environ.get("TEST_REDIS_PORT", "6379")),
        password=os.environ.get("TEST_REDIS_PASSWORD", ""),
        decode_responses=True,
    )
    client.ping()
    client.flushall()
    with contextlib.suppress(redis.ResponseError):
        client.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    yield client
    client.flushall()
    client.close()


class RecordingHandler:
    def __init__(self, terminal: bool = True, raise_error: bool = False) -> None:
        self.handled: list[str] = []
        self.permanently_failed: list[str] = []
        self.terminal = terminal
        self.raise_error = raise_error

    def handle(self, analysis_id, should_stop):
        self.handled.append(analysis_id)
        if self.raise_error:
            raise RuntimeError("boom")
        return self.terminal

    def mark_permanently_failed(self, analysis_id: str) -> None:
        self.permanently_failed.append(analysis_id)


def _deliver_one(client: redis.Redis, consumer_name: str) -> tuple[str, dict[str, str]]:
    raw = cast(
        _ReadGroupReply,
        client.xreadgroup(GROUP_NAME, consumer_name, {STREAM_NAME: ">"}, count=1, block=2000),
    )
    assert raw, "expected a delivered entry"
    _stream, messages = raw[0]
    return messages[0]


def test_happy_path_acks(redis_client: redis.Redis) -> None:
    handler = RecordingHandler(terminal=True)
    consumer = Consumer(redis_client, handler, consumer_name="test-consumer")
    redis_client.xadd(STREAM_NAME, {"job_id": "job-1"})

    entry_id, fields = _deliver_one(redis_client, "test-consumer")
    consumer._process_entry(entry_id, fields)

    assert handler.handled == ["job-1"]
    assert redis_client.xpending_range(STREAM_NAME, GROUP_NAME, min="-", max="+", count=10) == []


def test_crashed_handler_leaves_job_pending(redis_client: redis.Redis) -> None:
    handler = RecordingHandler(raise_error=True)
    consumer = Consumer(redis_client, handler, consumer_name="test-consumer")
    redis_client.xadd(STREAM_NAME, {"job_id": "job-2"})

    entry_id, fields = _deliver_one(redis_client, "test-consumer")
    consumer._process_entry(entry_id, fields)

    assert handler.handled == ["job-2"]
    pending = cast(
        list[dict[str, Any]],
        redis_client.xpending_range(STREAM_NAME, GROUP_NAME, min="-", max="+", count=10),
    )
    assert len(pending) == 1 and pending[0]["message_id"] == entry_id


def test_reclaim_stuck_job_reprocesses_it(redis_client: redis.Redis) -> None:
    handler = RecordingHandler(terminal=True)
    consumer = Consumer(redis_client, handler, consumer_name="reclaimer")
    entry_id = cast(str, redis_client.xadd(STREAM_NAME, {"job_id": "job-3"}))
    redis_client.xreadgroup(GROUP_NAME, "dead-consumer", {STREAM_NAME: ">"}, count=1)
    redis_client.xclaim(
        STREAM_NAME,
        GROUP_NAME,
        "dead-consumer",
        0,
        [entry_id],
        idle=PENDING_CLAIM_MIN_IDLE * 1000 + 1000,
    )

    consumer._reclaim_stuck_jobs()

    assert handler.handled == ["job-3"]
    assert redis_client.xpending_range(STREAM_NAME, GROUP_NAME, min="-", max="+", count=10) == []


def test_read_next_recreates_a_missing_consumer_group(redis_client: redis.Redis) -> None:
    """A previous version let NOGROUP (the stream/group disappearing out
    from under a running worker) escape run_forever's loop uncaught,
    crashing the whole process instead of just this one read attempt."""
    consumer = Consumer(redis_client, RecordingHandler(), consumer_name="test-consumer")
    redis_client.delete(STREAM_NAME)

    entries = consumer._read_next()

    assert entries == []
    groups = cast(list[dict[str, Any]], redis_client.xinfo_groups(STREAM_NAME))
    assert any(group["name"] == GROUP_NAME for group in groups)


def test_reclaim_gives_up_after_max_claim_attempts(redis_client: redis.Redis) -> None:
    handler = RecordingHandler(terminal=True)
    consumer = Consumer(redis_client, handler, consumer_name="reclaimer2")
    entry_id = cast(str, redis_client.xadd(STREAM_NAME, {"job_id": "job-4"}))
    redis_client.xreadgroup(GROUP_NAME, "dead-consumer-2", {STREAM_NAME: ">"}, count=1)
    for _ in range(MAX_CLAIM_ATTEMPTS + 2):
        redis_client.xclaim(STREAM_NAME, GROUP_NAME, "dead-consumer-2", 0, [entry_id])
    redis_client.xclaim(
        STREAM_NAME,
        GROUP_NAME,
        "dead-consumer-2",
        0,
        [entry_id],
        idle=PENDING_CLAIM_MIN_IDLE * 1000 + 1000,
    )

    consumer._reclaim_stuck_jobs()

    assert handler.handled == []
    assert handler.permanently_failed == ["job-4"]
    assert redis_client.xpending_range(STREAM_NAME, GROUP_NAME, min="-", max="+", count=10) == []
