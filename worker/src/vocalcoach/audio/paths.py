"""Deterministic, id-derived file paths -- never a user-supplied name (spec
11.3), mirroring `api/internal/storage.FileStore.PathFor`.
"""

from __future__ import annotations

from pathlib import Path


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
