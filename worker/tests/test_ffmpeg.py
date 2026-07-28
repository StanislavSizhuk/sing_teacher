from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from tests.conftest import sine_wave
from vocalcoach.audio.ffmpeg import canonicalize_for_pipeline, run_ffmpeg
from vocalcoach.audio.io import read_mono
from vocalcoach.errors import InternalPipelineError, StageTimeout

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@requires_ffmpeg
def test_canonicalize_for_pipeline_resamples_and_forces_mono(tmp_path: Path) -> None:
    channel = sine_wave(1.0, 44100, 440.0)
    stereo = np.stack([channel, channel], axis=-1)  # (n_samples, 2) -- soundfile's expected shape
    src = tmp_path / "src.wav"
    sf.write(src, stereo, 44100, subtype="PCM_16")

    dst = tmp_path / "dst.wav"
    canonicalize_for_pipeline(
        "ffmpeg", src, dst, sample_rate_hz=22050, timeout_seconds=10, stage_name="test"
    )

    samples, sample_rate = read_mono(dst)
    assert sample_rate == 22050
    assert abs(len(samples) / sample_rate - 1.0) < 0.05


def test_run_ffmpeg_raises_internal_on_nonzero_exit() -> None:
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"invalid data found"),
        ),
        pytest.raises(InternalPipelineError, match="invalid data found"),
    ):
        run_ffmpeg("ffmpeg", ["-i", "x"], timeout_seconds=5, stage_name="preprocess")


def test_run_ffmpeg_raises_timeout_as_stage_timeout() -> None:
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5)),
        pytest.raises(StageTimeout) as exc_info,
    ):
        run_ffmpeg("ffmpeg", ["-i", "x"], timeout_seconds=5, stage_name="preprocess")
    assert exc_info.value.error_code == "TIMEOUT"


def test_run_ffmpeg_raises_internal_when_binary_missing() -> None:
    with (
        patch("subprocess.run", side_effect=FileNotFoundError("no such file")),
        pytest.raises(InternalPipelineError),
    ):
        run_ffmpeg("ffmpeg-does-not-exist", [], timeout_seconds=5, stage_name="preprocess")
