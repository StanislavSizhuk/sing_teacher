"""Pipeline error taxonomy (spec 6.8).

`error_code` is the machine-readable code stored on `analyses.error_code`
and shown to the UI; which exception class a stage raises decides whether
`PipelineRunner` retries it. Transient errors (timeout, a DB hiccup) get up
to `MAX_STAGE_RETRIES` attempts with backoff; logical errors are a property
of the audio itself, so retrying the same input would just fail the same
way, and the runner gives up on the first one.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base for every error a pipeline stage can raise."""

    error_code: str = "INTERNAL"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code


class TransientPipelineError(PipelineError):
    """Retryable: a timeout or a temporary infrastructure problem."""


class LogicalPipelineError(PipelineError):
    """Not retryable: a property of the input audio itself."""


class StageTimeout(TransientPipelineError):
    """A stage did not finish inside its declared timeout (spec 6.2)."""

    error_code = "TIMEOUT"

    def __init__(self, stage_name: str, timeout_seconds: int) -> None:
        super().__init__(f"stage '{stage_name}' exceeded its {timeout_seconds}s timeout")


class InternalPipelineError(TransientPipelineError):
    """An unexpected failure (DB blip, uncaught exception) -- worth a retry,
    since most causes are transient infrastructure trouble rather than a
    property of the input."""

    error_code = "INTERNAL"


class ReferenceTooQuiet(LogicalPipelineError):
    """The reference vocal stem has too little energy to analyze reliably."""

    error_code = "REFERENCE_TOO_QUIET"


class NoVoiceDetected(LogicalPipelineError):
    """No singing voice was detected in the user's recording."""

    error_code = "NO_VOICE_DETECTED"


class AlignmentFailed(LogicalPipelineError):
    """DTW could not align the recording to the reference within the
    configured warping window (spec ADR-0004, risk table: tempo diverged too far)."""

    error_code = "ALIGNMENT_FAILED"


class AlignmentTooLarge(LogicalPipelineError):
    """The banded DTW's cell count (spec 6.7) exceeds `DTW_MAX_CELLS` before
    a single cell is computed -- refused upfront rather than let a
    pathological input (e.g. both signals hours long) eat unbounded memory
    (NFR-16)."""

    error_code = "ALIGNMENT_TOO_LARGE"
