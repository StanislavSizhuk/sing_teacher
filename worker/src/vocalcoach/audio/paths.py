"""Deterministic, id-derived file paths -- never a user-supplied name (spec
11.3), mirroring `api/internal/storage.FileStore.PathFor`.
"""

from __future__ import annotations

from pathlib import Path


def song_source_path(audio_storage_dir: Path, song_id: str) -> Path:
    """Where the Go API wrote the song's canonical upload (`service/song`,
    `filePrefix = "song"`) -- the worker only ever reads this file.
    """
    return audio_storage_dir / f"song-{song_id}.wav"


def recording_source_path(audio_storage_dir: Path, analysis_id: str) -> Path:
    """Where the Go API wrote the user's canonical recording
    (`service/analysis`, `filePrefix = "analysis"`) -- the worker only ever
    reads this file.
    """
    return audio_storage_dir / f"analysis-{analysis_id}.wav"


def song_stem_path(song_stems_dir: Path, song_id: str) -> Path:
    """Where the reference song's separated vocal stem is cached (spec 6.6),
    stable across every analysis of that song.
    """
    return song_stems_dir / f"song-stem-{song_id}.wav"


def analysis_work_dir(audio_storage_dir: Path, analysis_id: str) -> Path:
    """Scratch directory for one analysis run's intermediate files, deleted
    once the job reaches a terminal state (spec FR-43: no later than 5
    minutes after processing ends -- here, immediately).
    """
    return audio_storage_dir / f"work-{analysis_id}"
