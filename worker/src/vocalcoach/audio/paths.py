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
    """Scratch directory for one analysis run's intermediate files (each
    stage's cached output in stages_json points back into here). Deleted
    on success, immediately (spec FR-43). Left in place on failure instead:
    a retry (FR-26) resumes from already-completed stages by reopening
    exactly these files, so removing them here would make retry always
    fail on the first cached stage it tries to reuse.
    """
    return audio_storage_dir / f"work-{analysis_id}"
