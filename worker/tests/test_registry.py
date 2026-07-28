"""Tests the lazy-caching contract only -- actually invoking a real model
would need network access and multi-GB weights, which spec 15.2 rules out
for unit tests."""

from __future__ import annotations

from pathlib import Path

from vocalcoach.pipeline.registry import CrepePitchDetector, ModelRegistry, PyinPitchDetector


def _registry(tmp_path: Path, pitch_engine: str = "crepe") -> ModelRegistry:
    return ModelRegistry(
        demucs_model="htdemucs",
        whisper_model="small",
        pitch_engine=pitch_engine,  # type: ignore[arg-type]
        weights_dir=tmp_path,
    )


def test_vocal_separator_is_cached(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.vocal_separator() is registry.vocal_separator()


def test_transcriber_is_cached(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.transcriber() is registry.transcriber()


def test_pitch_detector_selects_implementation_from_config(tmp_path: Path) -> None:
    assert isinstance(_registry(tmp_path, "crepe").pitch_detector(), CrepePitchDetector)
    assert isinstance(_registry(tmp_path, "pyin").pitch_detector(), PyinPitchDetector)


def test_release_all_resets_cache(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    first = registry.pitch_detector()
    registry.release_all()
    second = registry.pitch_detector()
    assert first is not second
