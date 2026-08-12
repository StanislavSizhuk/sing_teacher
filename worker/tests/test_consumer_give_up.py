"""`Consumer._give_up` unit tests (spec 10.1) against a fake Redis client --
no real Redis needed, unlike test_consumer.py's integration suite. Covers
the give-up path itself: recording a job's terminal failure must never be
allowed to block removing its stream entry, or a single poison job_id
(never a valid UUID, however it got onto the stream) crash-loops the whole
worker process forever instead of just being given up on once.
"""

from __future__ import annotations

from collections.abc import Callable

from vocalcoach.queue.consumer import Consumer

STREAM_NAME = "analyses:run"
GROUP_NAME = "analyses:workers"


class _FakeRedisClient:
    """Only the three calls `_give_up` reaches: `fields_for`'s `xrange` and
    `_ack_and_remove`'s `xack`/`xdel`."""

    def __init__(self, fields: dict[str, str] | None) -> None:
        self._fields = fields
        self.xack_calls: list[tuple[str, str, str]] = []
        self.xdel_calls: list[tuple[str, str]] = []

    def xrange(self, _stream_name: str, start: str, _end: str) -> list[tuple[str, dict[str, str]]]:
        return [] if self._fields is None else [(start, self._fields)]

    def xack(self, stream_name: str, group_name: str, entry_id: str) -> None:
        self.xack_calls.append((stream_name, group_name, entry_id))

    def xdel(self, stream_name: str, entry_id: str) -> None:
        self.xdel_calls.append((stream_name, entry_id))


class _RaisingHandler:
    """Stands in for `AnalysisJobHandler`/`SongPrepJobHandler` when
    `mark_permanently_failed` itself fails -- e.g. `psycopg.errors.
    InvalidTextRepresentation` for a job_id that was never a real UUID, but
    any other DB error hits the same path."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(self, job_id: str, should_stop: Callable[[], bool]) -> bool:
        raise NotImplementedError

    def mark_permanently_failed(self, job_id: str) -> None:
        self.calls.append(job_id)
        raise RuntimeError("boom")


class _RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(self, job_id: str, should_stop: Callable[[], bool]) -> bool:
        raise NotImplementedError

    def mark_permanently_failed(self, job_id: str) -> None:
        self.calls.append(job_id)


def _make_consumer(client: _FakeRedisClient, handler: object) -> Consumer:
    return Consumer(
        client,  # type: ignore[arg-type]  # duck-typed fake, not a real redis.Redis
        handler,  # type: ignore[arg-type]  # duck-typed fake, not a real JobHandler
        STREAM_NAME,
        GROUP_NAME,
        reclaim_min_idle_seconds=900,
        max_claim_attempts=3,
        consumer_name="worker-1",
    )


def test_give_up_removes_entry_even_when_marking_permanently_failed_raises() -> None:
    client = _FakeRedisClient({"job_id": "job-resume"})
    handler = _RaisingHandler()
    consumer = _make_consumer(client, handler)

    consumer._give_up("42-0")  # must not raise -- a crash here takes down run_forever

    assert handler.calls == ["job-resume"]
    assert client.xack_calls == [(STREAM_NAME, GROUP_NAME, "42-0")]
    assert client.xdel_calls == [(STREAM_NAME, "42-0")]


def test_give_up_still_removes_entry_on_the_happy_path() -> None:
    client = _FakeRedisClient({"job_id": "job-1"})
    handler = _RecordingHandler()
    consumer = _make_consumer(client, handler)

    consumer._give_up("7-0")

    assert handler.calls == ["job-1"]
    assert client.xack_calls == [(STREAM_NAME, GROUP_NAME, "7-0")]
    assert client.xdel_calls == [(STREAM_NAME, "7-0")]


def test_give_up_with_no_job_id_field_still_removes_entry() -> None:
    client = _FakeRedisClient({})
    handler = _RecordingHandler()
    consumer = _make_consumer(client, handler)

    consumer._give_up("9-0")

    assert handler.calls == []
    assert client.xack_calls == [(STREAM_NAME, GROUP_NAME, "9-0")]
    assert client.xdel_calls == [(STREAM_NAME, "9-0")]
