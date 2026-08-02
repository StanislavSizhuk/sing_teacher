"""Tests the lazy-caching contract only -- actually invoking a real model
would need network access and multi-GB weights, which spec 15.2 rules out
for unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from vocalcoach.pipeline.registry import (
    CrepePitchDetector,
    DemucsSeparator,
    ModelRegistry,
    PyinPitchDetector,
)


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


class _FakeDemucsSeparator:
    """Stands in for `demucs.api.Separator`, whose `separate_tensor` always
    returns audio at the *model's* native rate (44100 here) regardless of
    the `sr` it was told the input was -- exactly what the real Demucs API
    does (its own docstring: "the wave will be resampled if it doesn't
    match the model"). Reproduces the shape of a real call without needing
    network access or multi-GB weights (spec 15.2).
    """

    samplerate = 44100
    audio_channels = 2

    def separate_tensor(self, wav: Any, sr: int | None = None) -> tuple[Any, dict[str, Any]]:
        import torch

        seconds = wav.shape[-1] / (sr or self.samplerate)
        native_length = round(seconds * self.samplerate)
        vocals = torch.zeros(2, native_length)
        return wav, {"vocals": vocals}


def test_demucs_separator_returns_input_sample_rate_not_the_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: separate_vocals used to hand back whatever
    separate_tensor returned untouched. htdemucs' native rate (44100) never
    matches this pipeline's own rate (22050), so every caller that trusted
    "same sample rate as the input" (VocalSeparator's own documented
    contract) and wrote the result out labeled at the input's rate got a
    file whose real duration was silently double what its header claimed --
    which is what made every reference pitch curve roughly 2x too long and
    made every warm-path alignment against it fail outright.
    """
    separator = DemucsSeparator("htdemucs")
    monkeypatch.setattr(separator, "_loaded", lambda: _FakeDemucsSeparator())

    sample_rate_hz = 22050
    mixture = np.zeros(sample_rate_hz * 3, dtype=np.float32)  # 3s at the input's own rate

    vocals = separator.separate_vocals(mixture, sample_rate_hz)

    assert vocals.shape[0] == pytest.approx(len(mixture), rel=0.01)
