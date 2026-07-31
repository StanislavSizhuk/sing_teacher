"""Integration tests: a real, migrated Postgres is required (spec 15.1).
CI applies `api/migrations` with goose before running these; see the
`test-worker` job in `.github/workflows/ci.yml`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from vocalcoach.models.audio import Lyrics, LyricsWord, PianoRollData, PitchCurve
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.repositories.postgres import PostgresAnalysisRepository, PostgresSongRepository

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
    connection.close()


@pytest.fixture
def seeded_ids(conn: psycopg.Connection) -> tuple[str, str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash, display_name, email_verified) "
            "VALUES (gen_random_uuid(), %s, 'x', 'Test', true) RETURNING id",
            (f"{uuid.uuid4()}@example.com",),
        )
        (user_id,) = _fetchone(cur)
        cur.execute(
            "INSERT INTO songs (id, source_type, content_hash, title, duration_sec) "
            "VALUES (gen_random_uuid(), 'upload', %s, 'Test Song', 180) RETURNING id",
            (str(uuid.uuid4()),),
        )
        (song_id,) = _fetchone(cur)
        cur.execute(
            "INSERT INTO analyses (id, user_id, song_id, status) "
            "VALUES (gen_random_uuid(), %s, %s, 'queued') RETURNING id",
            (user_id, song_id),
        )
        (analysis_id,) = _fetchone(cur)
    conn.commit()
    return str(user_id), str(song_id), str(analysis_id)


def test_song_repository_prep_lifecycle_round_trip(
    conn: psycopg.Connection, seeded_ids: tuple[str, str, str]
) -> None:
    _user_id, song_id, _analysis_id = seeded_ids
    repo = PostgresSongRepository(conn)

    song = repo.get_by_id(song_id)
    assert song.prep_status == "pending"
    assert song.lyrics is None
    assert song.vocal_stem_path is None
    assert song.reference_pitch is None
    assert song.lyrics_available is False

    repo.mark_prep_processing(song_id, "prep_reference", 1, 4)
    song = repo.get_by_id(song_id)
    assert song.prep_status == "processing"

    stage_result = StageResult(
        stage="prep_reference", status=StageStatus.DONE, duration_ms=10, data={"a": 1}
    )
    repo.save_prep_stage_progress(song_id, stage_result, "separate_reference", 2, 4)
    song = repo.get_by_id(song_id)
    assert set(song.prep_stages.keys()) == {"prep_reference"}
    assert song.prep_stages["prep_reference"].duration_ms == 10

    lyrics = Lyrics(language="en", words=[LyricsWord(word="la", start=0.0, end=0.2)])
    curve = PitchCurve(hop_seconds=0.01, hz=[440.0, None, 441.5])
    stem_path = Path("/data/song-stems") / f"song-stem-{song_id}.wav"
    repo.mark_prep_ready(
        song_id,
        vocal_stem_path=stem_path,
        reference_pitch=curve,
        lyrics=lyrics,
        lyrics_available=True,
    )
    song = repo.get_by_id(song_id)
    assert song.prep_status == "ready"
    assert song.vocal_stem_path == stem_path
    assert song.reference_pitch == curve
    assert song.lyrics == lyrics
    assert song.lyrics_available is True


def test_song_repository_mark_prep_failed(
    conn: psycopg.Connection, seeded_ids: tuple[str, str, str]
) -> None:
    _user_id, song_id, _analysis_id = seeded_ids
    repo = PostgresSongRepository(conn)

    repo.mark_prep_processing(song_id, "separate_reference", 2, 4)
    repo.mark_prep_failed(song_id, "REFERENCE_TOO_QUIET")

    song = repo.get_by_id(song_id)
    assert song.prep_status == "failed"


def test_analysis_repository_progress_and_terminal_states(
    conn: psycopg.Connection, seeded_ids: tuple[str, str, str]
) -> None:
    _user_id, _song_id, analysis_id = seeded_ids
    repo = PostgresAnalysisRepository(conn)

    with conn.cursor() as cur:
        cur.execute("UPDATE analyses SET queue_position = 9 WHERE id = %s", (analysis_id,))
    conn.commit()

    repo.mark_processing(analysis_id, "preprocess", stage_index=1, total_stages=3)
    assert repo.get_by_id(analysis_id).status == "processing"
    with conn.cursor() as cur:
        # spec 8.2: queue_position is "Absent once no longer queued" -- a
        # job the worker just picked up must not still claim a queue spot.
        cur.execute("SELECT queue_position FROM analyses WHERE id = %s", (analysis_id,))
        (queue_position,) = _fetchone(cur)
    assert queue_position is None

    repo.save_stage_progress(
        analysis_id,
        StageResult(stage="preprocess", status=StageStatus.DONE, duration_ms=10, data={"a": 1}),
        next_stage="align",
        next_stage_index=2,
        total_stages=3,
    )
    repo.save_stage_progress(
        analysis_id,
        StageResult(stage="align", status=StageStatus.DONE, duration_ms=20, data={}),
        next_stage="pitch",
        next_stage_index=3,
        total_stages=3,
    )
    record = repo.get_by_id(analysis_id)
    # Both entries must survive -- this is the jsonb `||` merge resumability depends on.
    assert set(record.stages.keys()) == {"preprocess", "align"}
    assert record.stages["preprocess"].duration_ms == 10

    # current_stage_index/total_stages/current_stage_started_at (spec 6.2,
    # 8.3) aren't on AnalysisRecord (the worker's own pipeline logic never
    # reads them back), so check the row directly -- this is what the Go
    # API's GET /analyses/{id} actually surfaces to the client.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_stage, current_stage_index, total_stages, "
            "current_stage_started_at FROM analyses WHERE id = %s",
            (analysis_id,),
        )
        current_stage, current_stage_index, total_stages, started_at = _fetchone(cur)
    assert current_stage == "pitch"
    assert current_stage_index == 3
    assert total_stages == 3
    assert started_at is not None

    repo.save_aspect_score(analysis_id, "pitch", 87.5)
    with conn.cursor() as cur:
        cur.execute("SELECT pitch_score FROM analyses WHERE id = %s", (analysis_id,))
        (score,) = _fetchone(cur)
    assert float(score) == 87.5

    # analyses_clean_has_breath_score (migration 00011, spec 7.1) requires a
    # `clean`-mode row to carry a breath_score before it can reach `done`
    # below -- this row defaults to mode='clean' (migration 00011).
    repo.save_aspect_score(analysis_id, "breath", 90.0)

    with pytest.raises(ValueError, match="unknown aspect"):
        repo.save_aspect_score(analysis_id, "not_a_real_aspect", 1.0)

    piano_roll = PianoRollData(
        hop_seconds=0.01,
        user_hz=[440.0, None],
        reference_hz=[440.0, None],
        deviation_cents=[0.0, None],
        off_pitch=[False, False],
    )
    repo.save_piano_roll(analysis_id, piano_roll)
    with conn.cursor() as cur:
        cur.execute("SELECT pitch_curve_json FROM analyses WHERE id = %s", (analysis_id,))
        (stored,) = _fetchone(cur)
    assert PianoRollData.model_validate(stored) == piano_roll

    repo.save_scoring_result(
        analysis_id,
        88.4,
        "Overall score: 88/100.",
        "1.0",
        weights_profile="clean_v1",
        effective_mode="clean",
        confidence="high",
        aspect_confidence={"pitch": "high"},
        warnings=["KEY_SHIFT_APPLIED"],
        unavailable_aspects={},
        key_shift_semitones=-2.0,
        accompaniment_level=0.05,
        voiced_ratio=0.9,
        alignment_cost=10.0,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT overall_score, feedback_text, scoring_version, weights_profile,
                   effective_mode, confidence, aspect_confidence_json, warnings_json,
                   unavailable_aspects_json, key_shift_semitones, accompaniment_level,
                   voiced_ratio, alignment_cost
            FROM analyses WHERE id = %s
            """,
            (analysis_id,),
        )
        (
            overall_score,
            feedback_text,
            scoring_version,
            weights_profile,
            effective_mode,
            confidence,
            aspect_confidence_json,
            warnings_json,
            unavailable_aspects_json,
            key_shift_semitones,
            accompaniment_level,
            voiced_ratio,
            alignment_cost,
        ) = _fetchone(cur)
    assert float(overall_score) == 88.4
    assert feedback_text == "Overall score: 88/100."
    assert scoring_version == "1.0"
    assert weights_profile == "clean_v1"
    assert effective_mode == "clean"
    assert confidence == "high"
    assert aspect_confidence_json == {"pitch": "high"}
    assert warnings_json == ["KEY_SHIFT_APPLIED"]
    assert unavailable_aspects_json == {}
    assert float(key_shift_semitones) == -2.0
    assert float(accompaniment_level) == 0.05
    assert float(voiced_ratio) == 0.9
    assert float(alignment_cost) == 10.0

    # A job's queue_position is only meaningful while queued (spec 8.2:
    # "Absent once no longer queued") -- set it as if this row were still
    # sitting in the queue, so mark_done clearing it is actually exercised.
    with conn.cursor() as cur:
        cur.execute("UPDATE analyses SET queue_position = 7 WHERE id = %s", (analysis_id,))
    conn.commit()

    repo.mark_done(analysis_id, {"demucs": "htdemucs"})
    assert repo.get_by_id(analysis_id).status == "done"
    with conn.cursor() as cur:
        cur.execute("SELECT queue_position FROM analyses WHERE id = %s", (analysis_id,))
        (queue_position,) = _fetchone(cur)
    assert queue_position is None


def test_record_progress_snapshot_upserts_on_retry(
    conn: psycopg.Connection, seeded_ids: tuple[str, str, str]
) -> None:
    user_id, _song_id, analysis_id = seeded_ids
    repo = PostgresAnalysisRepository(conn)

    repo.record_progress_snapshot(analysis_id, user_id, 60.0, mode="clean", confidence="high")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, overall_score, mode, confidence FROM progress_snapshots "
            "WHERE analysis_id = %s",
            (analysis_id,),
        )
        stored_user_id, overall_score, mode, confidence = _fetchone(cur)
    assert str(stored_user_id) == user_id
    assert float(overall_score) == 60.0
    assert mode == "clean"
    assert confidence == "high"

    # A retried job re-scores under the same analysis_id (spec 6.8) -- the
    # chart point must update in place, not duplicate (FR-35), and can
    # switch mode (FR-30: retry in `mixed` after an accompaniment warning).
    repo.record_progress_snapshot(analysis_id, user_id, 75.0, mode="mixed", confidence="medium")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT overall_score, mode, confidence FROM progress_snapshots WHERE analysis_id = %s",
            (analysis_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert float(rows[0][0]) == 75.0
    assert rows[0][1] == "mixed"
    assert rows[0][2] == "medium"


def test_mark_failed(conn: psycopg.Connection, seeded_ids: tuple[str, str, str]) -> None:
    _user_id, _song_id, analysis_id = seeded_ids
    repo = PostgresAnalysisRepository(conn)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE analyses SET queue_position = 3, current_stage = 'pitch', "
            "current_stage_index = 5, total_stages = 11, current_stage_started_at = now() "
            "WHERE id = %s",
            (analysis_id,),
        )
    conn.commit()

    repo.mark_failed(analysis_id, "NO_VOICE_DETECTED")

    assert repo.get_by_id(analysis_id).status == "failed"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT queue_position, current_stage, current_stage_index, total_stages, "
            "current_stage_started_at FROM analyses WHERE id = %s",
            (analysis_id,),
        )
        row = _fetchone(cur)
    assert row == (None, None, None, None, None)
