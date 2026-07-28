from __future__ import annotations

import pytest

from vocalcoach.errors import (
    AlignmentFailed,
    InternalPipelineError,
    LogicalPipelineError,
    NoVoiceDetected,
    PipelineError,
    ReferenceTooQuiet,
    StageTimeout,
    TransientPipelineError,
)


@pytest.mark.parametrize(
    ("exc_type", "expected_code", "is_transient"),
    [
        (ReferenceTooQuiet, "REFERENCE_TOO_QUIET", False),
        (NoVoiceDetected, "NO_VOICE_DETECTED", False),
        (AlignmentFailed, "ALIGNMENT_FAILED", False),
        (InternalPipelineError, "INTERNAL", True),
    ],
)
def test_error_codes_and_retry_classification(
    exc_type: type[PipelineError], expected_code: str, is_transient: bool
) -> None:
    exc = exc_type("boom")
    assert exc.error_code == expected_code
    assert isinstance(exc, TransientPipelineError) is is_transient
    assert isinstance(exc, LogicalPipelineError) is not is_transient


def test_stage_timeout_is_transient_with_timeout_code() -> None:
    exc = StageTimeout("pitch", 180)
    assert exc.error_code == "TIMEOUT"
    assert isinstance(exc, TransientPipelineError)
    assert "180s" in str(exc)


def test_error_code_override() -> None:
    exc = PipelineError("custom", error_code="CUSTOM")
    assert exc.error_code == "CUSTOM"


def test_default_error_code_is_internal() -> None:
    assert PipelineError("no override given").error_code == "INTERNAL"
