"""`Scheduler` unit tests (spec 10.2): the one-ML-slot-with-priority rule
between analyses:run and songs:prep, against fake streams -- the Redis
Streams mechanics themselves (XREADGROUP/XCLAIM/XPENDING) are covered by
test_consumer.py against real Redis; this only exercises which stream/
entry the scheduler picks each tick.
"""

from __future__ import annotations

from typing import Any

from vocalcoach.queue.scheduler import Scheduler


class FakeStream:
    """Stands in for `queue.consumer.Consumer`: `pending` holds entries
    already "delivered" (as `claim_new_entries` would do), `undelivered`
    holds ones a `read_next` call would pick up next.
    """

    def __init__(self, undelivered: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.undelivered = undelivered or []
        self.pending: list[tuple[str, dict[str, str]]] = []
        self.processed: list[tuple[str, dict[str, str]]] = []
        self.ensure_group_called = False
        self.reclaim_called = False
        self.claim_calls = 0

    def ensure_group(self) -> None:
        self.ensure_group_called = True

    def reclaim_stuck_jobs(self) -> None:
        self.reclaim_called = True

    def read_next(self, *, block_ms: int | None = None):
        if not self.undelivered:
            return []
        entry_id, fields = self.undelivered.pop(0)
        return [("stream", [(entry_id, fields)])]

    def claim_new_entries(self) -> None:
        self.claim_calls += 1
        self.pending.extend(self.undelivered)
        self.undelivered = []

    def pending_entries(self) -> list[dict[str, Any]]:
        return [{"message_id": entry_id} for entry_id, _fields in self.pending]

    def fields_for(self, entry_id: str) -> dict[str, str] | None:
        for pending_id, fields in self.pending:
            if pending_id == entry_id:
                return fields
        return None

    def process_entry(self, entry_id: str, fields: dict[str, str], should_stop) -> None:
        self.processed.append((entry_id, fields))
        self.pending = [(pid, f) for pid, f in self.pending if pid != entry_id]


class FakeWaitingSongs:
    def __init__(self, song_id: str | None = None) -> None:
        self.song_id = song_id

    def oldest_waiting_song_id(self) -> str | None:
        return self.song_id


def test_tick_prefers_analyses_run_when_both_have_work() -> None:
    analyses = FakeStream(undelivered=[("1-0", {"job_id": "a1"})])
    songs_prep = FakeStream(undelivered=[("1-0", {"job_id": "s1"})])
    scheduler = Scheduler(analyses, songs_prep, FakeWaitingSongs())

    handled = scheduler._tick()

    assert handled is True
    assert analyses.processed == [("1-0", {"job_id": "a1"})]
    assert songs_prep.processed == []
    assert songs_prep.claim_calls == 0  # never even looked at songs:prep


def test_tick_processes_songs_prep_fifo_when_analyses_run_empty() -> None:
    analyses = FakeStream()
    songs_prep = FakeStream(undelivered=[("1-0", {"job_id": "s1"}), ("2-0", {"job_id": "s2"})])
    scheduler = Scheduler(analyses, songs_prep, FakeWaitingSongs())

    handled = scheduler._tick()

    assert handled is True
    # Oldest (first-delivered) entry wins, in FIFO order.
    assert songs_prep.processed == [("1-0", {"job_id": "s1"})]


def test_tick_priority_song_jumps_the_songs_prep_line() -> None:
    analyses = FakeStream()
    songs_prep = FakeStream(
        undelivered=[
            ("1-0", {"job_id": "s1"}),
            ("2-0", {"job_id": "s2"}),
            ("3-0", {"job_id": "s3"}),
        ]
    )
    scheduler = Scheduler(analyses, songs_prep, FakeWaitingSongs(song_id="s3"))

    handled = scheduler._tick()

    assert handled is True
    # s3 was third in submission order but an analysis is waiting on it --
    # it must be the one actually processed, not s1.
    assert songs_prep.processed == [("3-0", {"job_id": "s3"})]
    # The other two entries were claimed (delivered to this consumer's PEL)
    # but not processed -- available for a later tick.
    assert {entry_id for entry_id, _ in songs_prep.pending} == {"1-0", "2-0"}


def test_tick_priority_song_not_found_falls_back_to_fifo() -> None:
    """Edge case (spec 10.2): the priority target isn't (yet, or anymore)
    actually on songs:prep -- a race with enqueue, or it's already being
    processed. Must not spin; falls through to plain FIFO instead."""
    analyses = FakeStream()
    songs_prep = FakeStream(undelivered=[("1-0", {"job_id": "s1"})])
    scheduler = Scheduler(analyses, songs_prep, FakeWaitingSongs(song_id="s-not-on-stream"))

    handled = scheduler._tick()

    assert handled is True
    assert songs_prep.processed == [("1-0", {"job_id": "s1"})]


def test_tick_returns_false_when_both_streams_are_empty() -> None:
    scheduler = Scheduler(FakeStream(), FakeStream(), FakeWaitingSongs())

    assert scheduler._tick() is False


def test_run_forever_ensures_groups_and_reclaims_before_looping() -> None:
    analyses = FakeStream()
    songs_prep = FakeStream()
    scheduler = Scheduler(analyses, songs_prep, FakeWaitingSongs())
    scheduler._stopping = True  # stop the loop immediately after setup

    scheduler.run_forever()

    assert analyses.ensure_group_called is True
    assert songs_prep.ensure_group_called is True
    assert analyses.reclaim_called is True
    assert songs_prep.reclaim_called is True
