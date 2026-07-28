"""Postgres implementations of the repository interfaces (spec 7).

Every statement is parameterised; the one dynamic identifier (an aspect
score column name) goes through `psycopg.sql.Identifier`, never string
interpolation (spec 11.5, 12.5: no concatenated SQL).
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from vocalcoach.config import ASPECTS
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult


class PostgresSongRepository:
    """`SongRepository` backed by the `songs` table the Go API also owns."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def get_by_id(self, song_id: str) -> SongRecord:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content_hash, duration_sec, vocal_stem_processed,
                       lyrics_json, reference_pitch_json
                FROM songs
                WHERE id = %s
                """,
                (song_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError(f"song {song_id} not found")
        song_id_, content_hash, duration_sec, processed, lyrics_json, pitch_json = row
        return SongRecord(
            id=str(song_id_),
            content_hash=content_hash,
            duration_sec=duration_sec,
            vocal_stem_processed=processed,
            lyrics=Lyrics.model_validate(lyrics_json) if lyrics_json is not None else None,
            reference_pitch=(
                PitchCurve.model_validate(pitch_json) if pitch_json is not None else None
            ),
        )

    def save_lyrics(self, song_id: str, lyrics: Lyrics) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE songs SET lyrics_json = %s WHERE id = %s",
                (Jsonb(lyrics.model_dump(mode="json")), song_id),
            )
        self._conn.commit()

    def mark_vocal_stem_processed(self, song_id: str, reference_pitch: PitchCurve) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE songs
                SET reference_pitch_json = %s, vocal_stem_processed = true
                WHERE id = %s
                """,
                (Jsonb(reference_pitch.model_dump(mode="json")), song_id),
            )
        self._conn.commit()


class PostgresAnalysisRepository:
    """`AnalysisRepository` backed by the `analyses` table the Go API also owns."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def get_by_id(self, analysis_id: str) -> AnalysisRecord:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, song_id, status, stages_json FROM analyses WHERE id = %s",
                (analysis_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError(f"analysis {analysis_id} not found")
        id_, user_id, song_id, status, stages_json = row
        stages = (
            {name: StageResult.model_validate(value) for name, value in stages_json.items()}
            if stages_json
            else {}
        )
        return AnalysisRecord(
            id=str(id_), user_id=str(user_id), song_id=str(song_id), status=status, stages=stages
        )

    def mark_processing(self, analysis_id: str, first_stage: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE analyses SET status = 'processing', current_stage = %s WHERE id = %s",
                (first_stage, analysis_id),
            )
        self._conn.commit()

    def save_stage_progress(
        self, analysis_id: str, result: StageResult, next_stage: str | None
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET stages_json = COALESCE(stages_json, '{}'::jsonb) || %s::jsonb,
                    current_stage = %s
                WHERE id = %s
                """,
                (Jsonb({result.stage: result.model_dump(mode="json")}), next_stage, analysis_id),
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

    def save_pitch_curve(self, analysis_id: str, curve: PitchCurve) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE analyses SET pitch_curve_json = %s WHERE id = %s",
                (Jsonb(curve.model_dump(mode="json")), analysis_id),
            )
        self._conn.commit()

    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analyses
                SET status = 'done', current_stage = NULL, model_versions = %s, completed_at = now()
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
                SET status = 'failed', error_code = %s, current_stage = NULL, completed_at = now()
                WHERE id = %s
                """,
                (error_code, analysis_id),
            )
        self._conn.commit()
