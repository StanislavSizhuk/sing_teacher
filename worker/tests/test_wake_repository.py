"""T11 (spec 10.3, FR-16/17): the Postgres queries `SongPrepJobHandler`
uses to wake or fail every analysis waiting on a song's cold path.
A real, migrated Postgres is required (spec 15.1); see test_repositories.py.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any, cast

import psycopg
import pytest

from vocalcoach.repositories.postgres import PostgresAnalysisRepository

pytestmark = pytest.mark.integration


def _fetchone(cur: Any) -> tuple[Any, ...]:
    row = cur.fetchone()
    assert row is not None, "expected a row -- an empty RETURNING means the INSERT failed"
    return cast("tuple[Any, ...]", row)


@pytest.fixture
def conn() -> Generator[psycopg.Connection, None, None]:
    connection = psycopg.connect(
        host=os.environ.get("TEST_POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("TEST_POSTGRES_PORT", "5432")),
        dbname=os.environ.get("TEST_POSTGRES_DB", "vocalcoach"),
        user=os.environ.get("TEST_POSTGRES_USER", "vocalcoach"),
        password=os.environ.get("TEST_POSTGRES_PASSWORD", ""),
    )
    yield connection
    # Safety net: this file is the only place any integration test creates
    # a waiting_for_reference row, and test_oldest_waiting_song_id_* below
    # assumes the table holds none of them on entry -- a test above that
    # deliberately leaves one non-terminal (to prove exclusivity) must
    # still not leak it into a later test if it forgets its own cleanup.
    with connection.cursor() as cur:
        cur.execute("DELETE FROM analyses WHERE status = 'waiting_for_reference'")
    connection.commit()
    connection.close()


def _make_user(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash, display_name, email_verified) "
            "VALUES (gen_random_uuid(), %s, 'x', 'Test', true) RETURNING id",
            (f"{uuid.uuid4()}@example.com",),
        )
        (user_id,) = _fetchone(cur)
    conn.commit()
    return str(user_id)


def _make_song(conn: psycopg.Connection, *, prep_status: str = "pending") -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO songs (id, source_type, content_hash, title, duration_sec, prep_status) "
            "VALUES (gen_random_uuid(), 'upload', %s, 'Test Song', 180, %s) RETURNING id",
            (str(uuid.uuid4()), prep_status),
        )
        (song_id,) = _fetchone(cur)
    conn.commit()
    return str(song_id)


def _make_analysis(conn: psycopg.Connection, *, user_id: str, song_id: str, status: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analyses (id, user_id, song_id, status) "
            "VALUES (gen_random_uuid(), %s, %s, %s) RETURNING id",
            (user_id, song_id, status),
        )
        (analysis_id,) = _fetchone(cur)
    conn.commit()
    return str(analysis_id)


def _status_of(conn: psycopg.Connection, analysis_id: str) -> tuple[str, int | None, str | None]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, queue_position, error_code FROM analyses WHERE id = %s", (analysis_id,)
        )
        status, position, error_code = _fetchone(cur)
    return status, position, error_code


def test_wake_waiting_for_reference_promotes_only_this_songs_waiters(
    conn: psycopg.Connection,
) -> None:
    user_id = _make_user(conn)
    song_id = _make_song(conn)
    other_song_id = _make_song(conn)
    repo = PostgresAnalysisRepository(conn)

    waiting_1 = _make_analysis(
        conn, user_id=user_id, song_id=song_id, status="waiting_for_reference"
    )
    waiting_2 = _make_analysis(
        conn, user_id=user_id, song_id=song_id, status="waiting_for_reference"
    )
    other_songs_waiter = _make_analysis(
        conn, user_id=user_id, song_id=other_song_id, status="waiting_for_reference"
    )

    newly_queued, positions = repo.wake_waiting_for_reference(song_id)

    assert set(newly_queued) == {waiting_1, waiting_2}
    assert waiting_1 in positions
    assert waiting_2 in positions
    assert other_songs_waiter not in positions

    status_1, position_1, _ = _status_of(conn, waiting_1)
    status_2, position_2, _ = _status_of(conn, waiting_2)
    status_other, _, _ = _status_of(conn, other_songs_waiter)
    assert status_1 == "queued"
    assert status_2 == "queued"
    assert position_1 is not None
    assert position_2 is not None
    # FIFO by original submission order (queue_seq), not redrawn on wake --
    # relative, not absolute, since other integration tests in this same
    # run share this database and may leave their own queued rows behind.
    assert position_1 < position_2
    assert status_other == "waiting_for_reference"  # untouched -- a different song

    # Cleanup: this row is deliberately left waiting to prove exclusivity
    # above -- resolve it so it doesn't leak into later tests in this file
    # (the only place any integration test creates such a row).
    with conn.cursor() as cur:
        cur.execute("DELETE FROM analyses WHERE id = %s", (other_songs_waiter,))
    conn.commit()


def test_wake_waiting_for_reference_reshuffles_already_queued_positions(
    conn: psycopg.Connection,
) -> None:
    """A waiting analysis keeps its original queue_seq (submission order),
    so if it was submitted before an analysis already queued against a
    different, already-ready song, waking it must push that already-queued
    row's position back -- not just assign the waiter a position at the end.
    """
    user_id = _make_user(conn)
    waiting_song = _make_song(conn)
    ready_song = _make_song(conn, prep_status="ready")
    repo = PostgresAnalysisRepository(conn)

    # Submitted first, against the not-yet-ready song.
    waiter = _make_analysis(
        conn, user_id=user_id, song_id=waiting_song, status="waiting_for_reference"
    )
    # Submitted second, against an already-ready song -- queued immediately.
    already_queued = _make_analysis(conn, user_id=user_id, song_id=ready_song, status="queued")
    with conn.cursor() as cur:
        cur.execute("UPDATE analyses SET queue_position = 1 WHERE id = %s", (already_queued,))
    conn.commit()

    newly_queued, positions = repo.wake_waiting_for_reference(waiting_song)

    assert newly_queued == [waiter]
    assert already_queued in positions  # pushed back, and reported as changed
    # Earlier queue_seq -> jumps ahead, regardless of what absolute numbers
    # other integration tests running in this same session left behind.
    assert positions[waiter] < positions[already_queued]


def test_fail_waiting_for_reference_fails_only_this_songs_waiters(conn: psycopg.Connection) -> None:
    user_id = _make_user(conn)
    song_id = _make_song(conn)
    other_song_id = _make_song(conn)
    repo = PostgresAnalysisRepository(conn)

    waiting_1 = _make_analysis(
        conn, user_id=user_id, song_id=song_id, status="waiting_for_reference"
    )
    waiting_2 = _make_analysis(
        conn, user_id=user_id, song_id=song_id, status="waiting_for_reference"
    )
    other_songs_waiter = _make_analysis(
        conn, user_id=user_id, song_id=other_song_id, status="waiting_for_reference"
    )

    failed_ids = repo.fail_waiting_for_reference(song_id, "REFERENCE_PREP_FAILED")

    assert set(failed_ids) == {waiting_1, waiting_2}
    status_1, _, error_1 = _status_of(conn, waiting_1)
    status_2, _, error_2 = _status_of(conn, waiting_2)
    status_other, _, _ = _status_of(conn, other_songs_waiter)
    assert status_1 == "failed" and error_1 == "REFERENCE_PREP_FAILED"
    assert status_2 == "failed" and error_2 == "REFERENCE_PREP_FAILED"
    assert status_other == "waiting_for_reference"

    # Cleanup: this row is deliberately left in a non-terminal state to
    # prove exclusivity above -- but this file is the only place any
    # integration test ever creates a waiting_for_reference row, so
    # leaving it behind would leak into test_oldest_waiting_song_id_*'s
    # otherwise-clean view of the table.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM analyses WHERE id = %s", (other_songs_waiter,))
    conn.commit()


def test_oldest_waiting_song_id_returns_earliest_submission(conn: psycopg.Connection) -> None:
    user_id = _make_user(conn)
    repo = PostgresAnalysisRepository(conn)

    # This file is the only place any integration test creates a
    # waiting_for_reference row, and every other test in it resolves (wakes
    # or fails) every row it creates -- so the table is genuinely empty of
    # them here, regardless of run order among the tests above.
    assert repo.oldest_waiting_song_id() is None
    # Regression: this read used to leave conn `idle in transaction`
    # (psycopg opens an implicit transaction on the first statement of a
    # session even for a plain SELECT). Scheduler calls this before every
    # songs:prep tick on one long-lived connection -- an empty result with
    # nothing else ever committing on that connection left it open
    # indefinitely, holding a lock that blocked every later `ALTER TABLE
    # analyses`, including the API's own migrations.
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE

    first_song = _make_song(conn)
    _make_analysis(conn, user_id=user_id, song_id=first_song, status="waiting_for_reference")
    assert repo.oldest_waiting_song_id() == first_song

    second_song = _make_song(conn)
    _make_analysis(conn, user_id=user_id, song_id=second_song, status="waiting_for_reference")
    # Still the first song's waiter -- it was queued first (lower queue_seq).
    assert repo.oldest_waiting_song_id() == first_song

    repo.wake_waiting_for_reference(first_song)
    # Once the first song's waiter is promoted out of waiting_for_reference,
    # the second song's becomes the oldest remaining one.
    assert repo.oldest_waiting_song_id() == second_song

    repo.wake_waiting_for_reference(second_song)
