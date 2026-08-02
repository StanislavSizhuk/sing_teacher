"""Postgres implementations of the repository interfaces (spec 7).

Every statement is parameterised; the one dynamic identifier (an aspect
score column name) goes through `psycopg.sql.Identifier`, never string
interpolation (spec 11.5, 12.5: no concatenated SQL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from vocalcoach.models.audio import Lyrics, PianoRollData, PitchCurve
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult
from vocalcoach.scoring.weights import ASPECTS

_SONG_COLUMNS = """id, content_hash, duration_sec, prep_status, vocal_stem_path,
                   reference_pitch, reference_pitch_meta, lyrics_json, lyrics_available,
                   prep_stages_json"""


def _row_to_song_record(row: tuple[Any, ...]) -> SongRecord:
    (
        song_id,
        content_hash,
        duration_sec,
        prep_status,
        vocal_stem_path,
        pitch_bytes,
        pitch_meta,
        lyrics_json,
        lyrics_available,
        prep_stages_json,
    ) = row
    prep_stages = (
        {name: StageResult.model_validate(value) for name, value in prep_stages_json.items()}
        if prep_stages_json
        else {}
    )
    return SongRecord(
        id=str(song_id),
        content_hash=content_hash,
        duration_sec=duration_sec,
        prep_status=prep_status,
        vocal_stem_path=Path(vocal_stem_path) if vocal_stem_path is not None else None,
        reference_pitch=(
            PitchCurve.from_bytes(bytes(pitch_bytes), pitch_meta)
            if pitch_bytes is not None
            else None
        ),
        lyrics=Lyrics.model_validate(lyrics_json) if lyrics_json is not None else None,
        lyrics_available=lyrics_available,
        prep_stages=prep_stages,
    )


class PostgresSongRepository:
    """`SongRepository` backed by the `songs` table the Go API also owns.

    Read by both job kinds (spec 6.6): the warm path only ever reads a
    `ready` song's cached reference; the cold path (`SongPrepJobHandler`)
    also owns every write here, including per-P-stage progress
    (`mark_prep_processing`/`save_prep_stage_progress`, this repository's
    `ProgressReporter`-facing half) and the terminal `ready`/`failed` writes.
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def get_by_id(self, song_id: str) -> SongRecord:
        # _SONG_COLUMNS is a fixed module-level column list, never
        # user-controlled input -- the only variable part of this
        # statement is the parameterized %s (spec 11.5, 12.5).
        query = f"SELECT {_SONG_COLUMNS} FROM songs WHERE id = %s"  # noqa: S608
        with self._conn.cursor() as cur:
            cur.execute(query, (song_id,))
            row = cur.fetchone()
        self._conn.rollback()  # closes the implicit transaction a read still opens
        if row is None:
            raise LookupError(f"song {song_id} not found")
        return _row_to_song_record(row)

    def mark_prep_processing(
        self, song_id: str, first_stage: str, _stage_index: int, _total_stages: int
    ) -> None:
        """`ProgressReporter.mark_processing`: `songs` tracks the current
        P-stage by name only (spec 7 schema), unlike `analyses` which also
        renders "stage N of M" for the client (spec 8.3) -- the cold path
        has no live UI consumer for that granularity yet, only FR-14's
        prep_status/prep_stage.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE songs SET prep_status = 'processing', prep_stage = %s WHERE id = %s",
                (first_stage, song_id),
            )
        self._conn.commit()

    def save_prep_stage_progress(
        self,
        song_id: str,
        result: StageResult,
        next_stage: str | None,
        _next_stage_index: int | None,
        _total_stages: int,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE songs
                SET prep_stages_json = COALESCE(prep_stages_json, '{}'::jsonb) || %s::jsonb,
                    prep_stage = %s
                WHERE id = %s
                """,
                (Jsonb({result.stage: result.model_dump(mode="json")}), next_stage, song_id),
            )
        self._conn.commit()

    def mark_prep_ready(
        self,
        song_id: str,
        *,
        vocal_stem_path: Path,
        reference_pitch: PitchCurve,
        lyrics: Lyrics | None,
        lyrics_available: bool,
    ) -> None:
        """Terminal success (spec 6.4): every P-stage finished, P3
        (transcription) optionally so -- `lyrics_available` is the
        authoritative flag either way (FR-18), `lyrics` itself stays NULL
        when P3 was skipped.
        """
        data, meta = reference_pitch.to_bytes()
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE songs
                SET prep_status = 'ready', prep_stage = NULL, prep_error_code = NULL,
                    vocal_stem_path = %s, reference_pitch = %s, reference_pitch_meta = %s,
                    lyrics_json = %s, lyrics_available = %s, prepared_at = now()
                WHERE id = %s
                """,
                (
                    str(vocal_stem_path),
                    data,
                    Jsonb(meta),
                    Jsonb(lyrics.model_dump(mode="json")) if lyrics is not None else None,
                    lyrics_available,
                    song_id,
                ),
            )
        self._conn.commit()

    def mark_prep_failed(self, song_id: str, error_code: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE songs
                SET prep_status = 'failed', prep_error_code = %s, prep_stage = NULL
                WHERE id = %s
                """,
                (error_code, song_id),
            )
        self._conn.commit()


class PostgresAnalysisRepository:
    """`AnalysisRepository` backed by the `analyses` table the Go API also owns."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def get_by_id(self, analysis_id: str) -> AnalysisRecord:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, song_id, status, stages_json, mode, allow_transposition
                FROM analyses WHERE id = %s
                """,
                (analysis_id,),
            )
            row = cur.fetchone()
        self._conn.rollback()  # closes the implicit transaction a read still opens
        if row is None:
            raise LookupError(f"analysis {analysis_id} not found")
        id_, user_id, song_id, status, stages_json, mode, allow_transposition = row
        stages = (
            {name: StageResult.model_validate(value) for name, value in stages_json.items()}
            if stages_json
            else {}
        )
        return AnalysisRecord(
            id=str(id_),
            user_id=str(user_id),
            song_id=str(song_id),
            status=status,
            stages=stages,
            mode=mode,
            allow_transposition=allow_transposition,
        )

    def mark_processing(
        self, analysis_id: str, first_stage: str, stage_index: int, total_stages: int
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET status = 'processing', current_stage = %s, current_stage_index = %s,
                    total_stages = %s, current_stage_started_at = now(), queue_position = NULL
                WHERE id = %s
                """,
                (first_stage, stage_index, total_stages, analysis_id),
            )
        self._conn.commit()

    def save_stage_progress(
        self,
        analysis_id: str,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET stages_json = COALESCE(stages_json, '{}'::jsonb) || %s::jsonb,
                    current_stage = %s,
                    current_stage_index = %s,
                    total_stages = %s,
                    current_stage_started_at = CASE WHEN %s THEN now() ELSE NULL END
                WHERE id = %s
                """,
                (
                    Jsonb({result.stage: result.model_dump(mode="json")}),
                    next_stage,
                    next_stage_index,
                    total_stages,
                    next_stage is not None,
                    analysis_id,
                ),
            )
        self._conn.commit()

    def save_aspect_score(self, analysis_id: str, aspect: str, score: float) -> None:
        if aspect not in ASPECTS:
            raise ValueError(f"unknown aspect {aspect!r}, expected one of {ASPECTS}")
        column = psycopg.sql.Identifier(f"{aspect}_score")
        query = psycopg.sql.SQL("UPDATE analyses SET {column} = %s WHERE id = %s").format(
            column=column
        )
        with self._conn.cursor() as cur:
            cur.execute(query, (score, analysis_id))
        self._conn.commit()

    def save_piano_roll(self, analysis_id: str, data: PianoRollData) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE analyses SET pitch_curve_json = %s WHERE id = %s",
                (Jsonb(data.model_dump(mode="json")), analysis_id),
            )
        self._conn.commit()

    def save_user_pitch_curve(self, analysis_id: str, curve: PitchCurve) -> None:
        data, meta = curve.to_bytes()
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE analyses SET user_pitch = %s, user_pitch_meta = %s WHERE id = %s",
                (data, Jsonb(meta), analysis_id),
            )
        self._conn.commit()

    def prune_dense_stage_fields(self, analysis_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET stages_json = stages_json
                    #- '{pitch,data,user_pitch_curve}'
                    #- '{pitch,data,reference_pitch_curve}'
                    #- '{pitch,data,piano_roll}'
                WHERE id = %s
                """,
                (analysis_id,),
            )
        self._conn.commit()

    def save_scoring_result(
        self,
        analysis_id: str,
        overall_score: float,
        feedback_text: str,
        scoring_version: str,
        *,
        weights_profile: str,
        effective_mode: str,
        confidence: str,
        aspect_confidence: dict[str, str],
        warnings: list[str],
        unavailable_aspects: dict[str, str],
        key_shift_semitones: float | None,
        accompaniment_level: float,
        voiced_ratio: float,
        alignment_cost: float,
    ) -> None:
        """Persists stage 11's full output (spec 6.14, 6.15, 6.19, FR-41),
        not just the score/text/version M2 already wrote -- `weights_profile`
        and `effective_mode` are what makes a stored `overall_score`
        interpretable at all (spec 6.14: scores under different profiles are
        not directly comparable), the rest is the confidence model and its
        diagnostic inputs (spec 6.15, FR-47).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET overall_score = %s, feedback_text = %s, scoring_version = %s,
                    weights_profile = %s, effective_mode = %s, confidence = %s,
                    aspect_confidence_json = %s, warnings_json = %s,
                    unavailable_aspects_json = %s, key_shift_semitones = %s,
                    accompaniment_level = %s, voiced_ratio = %s, alignment_cost = %s
                WHERE id = %s
                """,
                (
                    overall_score,
                    feedback_text,
                    scoring_version,
                    weights_profile,
                    effective_mode,
                    confidence,
                    Jsonb(aspect_confidence),
                    Jsonb(warnings),
                    Jsonb(unavailable_aspects),
                    key_shift_semitones,
                    accompaniment_level,
                    voiced_ratio,
                    alignment_cost,
                    analysis_id,
                ),
            )
        self._conn.commit()

    def record_progress_snapshot(
        self, analysis_id: str, user_id: str, overall_score: float, *, mode: str, confidence: str
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO progress_snapshots
                    (user_id, analysis_id, overall_score, mode, confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (analysis_id)
                DO UPDATE SET overall_score = EXCLUDED.overall_score, mode = EXCLUDED.mode,
                    confidence = EXCLUDED.confidence, created_at = now()
                """,
                (user_id, analysis_id, overall_score, mode, confidence),
            )
        self._conn.commit()

    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET status = 'done', current_stage = NULL, current_stage_index = NULL,
                    total_stages = NULL, current_stage_started_at = NULL, queue_position = NULL,
                    model_versions = %s, completed_at = now()
                WHERE id = %s
                """,
                (Jsonb(model_versions), analysis_id),
            )
        self._conn.commit()

    def mark_failed(self, analysis_id: str, error_code: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET status = 'failed', error_code = %s, current_stage = NULL,
                    current_stage_index = NULL, total_stages = NULL,
                    current_stage_started_at = NULL, queue_position = NULL, completed_at = now()
                WHERE id = %s
                """,
                (error_code, analysis_id),
            )
        self._conn.commit()

    def wake_waiting_for_reference(self, song_id: str) -> tuple[list[str], dict[str, int]]:
        """Transitions every `waiting_for_reference` analysis of song_id to
        `queued` (spec 10.3, FR-16) once its cold path reaches `ready`, then
        recalculates FIFO `queue_position` for every now-queued row. The
        `WITH ranked AS (...) UPDATE ... RETURNING` below is an exact mirror
        of `api/internal/repository/postgres.AnalysisRepository.
        RecalculatePositions` -- Go and Python must never derive this
        differently (spec 12.1 DRY), so if that query changes, this one
        must change with it. Original `queue_seq` (submission order) is
        kept, not redrawn: an analysis that waited for a slow song does not
        lose its place to one submitted later against an already-ready song.

        Returns `(newly_queued_ids, changed_positions)` as two separate
        things on purpose: `newly_queued_ids` is only the ids this call
        itself moved out of waiting -- the caller XADDs exactly these onto
        analyses:run (an already-queued row publishing a second stream
        entry for the same job_id would make the consumer see it twice).
        `changed_positions` is every id whose `queue_position` changed,
        which can include already-queued rows the newly-woken ones' earlier
        queue_seq pushed back -- those still get a `queued` WS event with
        their new position, just no fresh stream entry.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses SET status = 'queued'
                WHERE song_id = %s AND status = 'waiting_for_reference'
                RETURNING id
                """,
                (song_id,),
            )
            newly_queued = [str(row[0]) for row in cur.fetchall()]

            cur.execute(
                """
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY queue_seq) AS rn
                    FROM analyses
                    WHERE status = 'queued'
                )
                UPDATE analyses a
                SET queue_position = ranked.rn
                FROM ranked
                WHERE a.id = ranked.id AND a.queue_position IS DISTINCT FROM ranked.rn
                RETURNING a.id, ranked.rn
                """
            )
            changed_positions = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        self._conn.commit()
        return newly_queued, changed_positions

    def fail_waiting_for_reference(self, song_id: str, error_code: str) -> list[str]:
        """FR-17: every analysis still waiting on a song whose cold path
        just failed gets failed too -- it can never complete on its own.
        The user restarts the song's prep (POST /songs/{id}/prepare) and
        retries the analysis (FR-26). Returns the ids failed, so the caller
        can publish a `failed` event for each over WS.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET status = 'failed', error_code = %s, completed_at = now()
                WHERE song_id = %s AND status = 'waiting_for_reference'
                RETURNING id
                """,
                (error_code, song_id),
            )
            rows = cur.fetchall()
        self._conn.commit()
        return [str(row[0]) for row in rows]

    def oldest_waiting_song_id(self) -> str | None:
        """The song whose cold path a waiting analysis has been queued
        against the longest (spec 10.2 rule 2: that song's `songs:prep`
        entry jumps the line). `Scheduler` checks this before every
        `songs:prep` tick.

        Rolls back rather than skipping any transaction-close at all:
        psycopg opens an implicit transaction on the first statement of a
        session regardless of whether it's a read, and `_conn` is
        long-lived (one per repository instance, not per call) -- with
        nothing ever ending it, this connection sat `idle in transaction`
        indefinitely on Scheduler's very first empty tick (no song ever
        waiting) and held a lock that blocked every later `ALTER TABLE
        analyses` from the API, including its own migrations, until
        something else happened to commit on this same connection.
        `rollback()`, not `commit()`, since nothing here writes.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT song_id FROM analyses
                WHERE status = 'waiting_for_reference'
                ORDER BY queue_seq LIMIT 1
                """
            )
            row = cur.fetchone()
        self._conn.rollback()
        return str(row[0]) if row is not None else None
