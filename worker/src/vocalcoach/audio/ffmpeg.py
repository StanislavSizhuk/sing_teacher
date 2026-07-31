"""Thin wrapper around the `ffmpeg` binary: argument list only, never a shell
string, with an enforced timeout and memory cap (spec 11.3).
"""

from __future__ import annotations

import resource
import subprocess
from collections.abc import Sequence
from pathlib import Path

from vocalcoach.audio.io import read_mono, write_mono
from vocalcoach.audio.loudness import measure_and_normalize
from vocalcoach.errors import InternalPipelineError, StageTimeout

# Bounds a single ffmpeg invocation's virtual memory so a hostile or corrupt
# input cannot exhaust the container's memory budget (spec 11.3). Re-encoding
# a <=6-minute mono/stereo clip never needs anywhere close to this.
_FFMPEG_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024  # 1 GiB


def _limit_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (_FFMPEG_MEMORY_LIMIT_BYTES, _FFMPEG_MEMORY_LIMIT_BYTES))


def run_ffmpeg(
    ffmpeg_path: str, args: Sequence[str], *, timeout_seconds: float, stage_name: str
) -> None:
    """Runs `ffmpeg_path` with `args` (an argument list, never interpolated
    into a shell string) and raises on a non-zero exit, a timeout, or the
    binary being missing.

    Args:
        ffmpeg_path: absolute path or PATH-resolved binary name.
        args: ffmpeg CLI arguments, excluding the binary name itself.
        timeout_seconds: hard wall-clock limit for the whole invocation.
        stage_name: the pipeline stage this call belongs to, for the error message.
    """
    try:
        subprocess.run(  # noqa: S603 -- argument list, no shell (spec 11.3)
            [ffmpeg_path, *args],
            timeout=timeout_seconds,
            capture_output=True,
            check=True,
            preexec_fn=_limit_memory,
        )
    except subprocess.TimeoutExpired as exc:
        raise StageTimeout(stage_name, int(timeout_seconds)) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise InternalPipelineError(
            f"ffmpeg failed in stage '{stage_name}': {stderr[-2000:]}"
        ) from exc
    except OSError as exc:
        raise InternalPipelineError(
            f"could not run ffmpeg for stage '{stage_name}': {exc}"
        ) from exc


def canonicalize_for_pipeline(
    ffmpeg_path: str,
    src_path: Path,
    dst_path: Path,
    *,
    sample_rate_hz: int,
    timeout_seconds: float,
    stage_name: str,
) -> None:
    """Re-encodes `src_path` into mono PCM WAV at `sample_rate_hz` (spec 6.3
    stage 1: this is the ML pipeline's own resample, independent of the
    upload-time sanitization ffmpeg transcode the Go API already ran).
    """
    run_ffmpeg(
        ffmpeg_path,
        [
            "-y",
            "-i",
            str(src_path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate_hz),
            "-acodec",
            "pcm_s16le",
            str(dst_path),
        ],
        timeout_seconds=timeout_seconds,
        stage_name=stage_name,
    )


def decode_and_normalize(
    ffmpeg_path: str,
    src_path: Path,
    dst_path: Path,
    *,
    sample_rate_hz: int,
    target_loudness_lufs: float,
    timeout_seconds: float,
    stage_name: str,
) -> float:
    """Stage 1 (spec 6.3.1, split into A1/P1 by M2's cold/warm path):
    canonicalize `src_path` via ffmpeg, then loudness-normalize it in place
    at `dst_path`. Shared by both the warm path's recording-only
    `PreprocessStage` and the cold path's reference-only `PrepReferenceStage`
    -- identical decode/normalize mechanics either way (spec 12.1 DRY).

    Returns the pre-normalization loudness in LUFS (for downstream
    too-quiet checks and observability).
    """
    resampled = dst_path.with_suffix(".resampled.wav")
    canonicalize_for_pipeline(
        ffmpeg_path,
        src_path,
        resampled,
        sample_rate_hz=sample_rate_hz,
        timeout_seconds=timeout_seconds,
        stage_name=stage_name,
    )
    samples, sample_rate = read_mono(resampled)
    normalized, raw_loudness = measure_and_normalize(samples, sample_rate, target_loudness_lufs)
    write_mono(dst_path, normalized, sample_rate)
    resampled.unlink(missing_ok=True)
    return raw_loudness
