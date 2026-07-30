"""Runner tests use real spawned subprocesses (spec design: every stage
runs isolated for a real, enforceable timeout) rather than mocking
multiprocessing -- that would test the mock, not the isolation boundary
the runner actually depends on.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vocalcoach.errors import LogicalPipelineError, NoVoiceDetected, TransientPipelineError
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.runner import PipelineRunner, RunOutcome


class OkStage(PipelineStage):
    name = "ok_stage"
    timeout_seconds = 5

    def run(self, context: AnalysisContext) -> StageResult:
        return StageResult(
            stage=self.name, status=StageStatus.DONE, duration_ms=1, data={"ran": True}
        )


class SlowStage(PipelineStage):
    name = "slow_stage"
    timeout_seconds = 1

    def run(self, context: AnalysisContext) -> StageResult:
        time.sleep(10)
        return StageResult(  # pragma: no cover
            stage=self.name, status=StageStatus.DONE, duration_ms=1
        )


class AlwaysFailsTransient(PipelineStage):
    name = "always_fails"
    timeout_seconds = 5

    def run(self, context: AnalysisContext) -> StageResult:
        raise TransientPipelineError("nope, still broken")


class LogicalFailStage(PipelineStage):
    name = "logical_fail"
    timeout_seconds = 5

    def run(self, context: AnalysisContext) -> StageResult:
        raise NoVoiceDetected("no voice here")


class FlakyStage(PipelineStage):
    """Fails once, then succeeds -- attempt count is tracked on disk since
    each attempt runs in a fresh spawned process (no shared memory)."""

    name = "flaky_stage"
    timeout_seconds = 5

    def __init__(self, marker_path: Path) -> None:
        self._marker_path = marker_path

    def run(self, context: AnalysisContext) -> StageResult:
        count = int(self._marker_path.read_text()) + 1 if self._marker_path.exists() else 1
        self._marker_path.write_text(str(count))
        if count < 2:
            raise TransientPipelineError(f"transient failure, attempt {count}")
        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=1,
            data={"attempts": count},
        )


class RecordingAnalysisRepo:
    def __init__(self) -> None:
        self.processing_calls: list[tuple[str, str, int, int]] = []
        self.progress_calls: list[tuple[str, str, str | None, int | None, int]] = []

    def mark_processing(
        self, analysis_id: str, first_stage: str, stage_index: int, total_stages: int
    ) -> None:
        self.processing_calls.append((analysis_id, first_stage, stage_index, total_stages))

    def save_stage_progress(
        self,
        analysis_id: str,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None:
        self.progress_calls.append(
            (analysis_id, result.stage, next_stage, next_stage_index, total_stages)
        )


class RecordingEvents:
    def __init__(self) -> None:
        self.stage_events: list[tuple[str, str, int, int]] = []

    def publish_stage(self, analysis_id: str, name: str, index: int, total: int) -> None:
        self.stage_events.append((analysis_id, name, index, total))

    def publish_done(self, analysis_id: str) -> None:
        pass

    def publish_failed(self, analysis_id: str, error_code: str, message: str) -> None:
        pass


def make_context(tmp_path: Path) -> AnalysisContext:
    return AnalysisContext(
        analysis_id="a1",
        user_id="u1",
        song_id="s1",
        recording_path=tmp_path / "rec.wav",
        reference_path=tmp_path / "ref.wav",
        work_dir=tmp_path / "work",
        song_content_hash="hash",
        vocal_stem_processed=False,
        pitch_engine="pyin",
        whisper_model="small",
        demucs_model="htdemucs",
        model_weights_dir=tmp_path / "weights",
    )


def test_timeout_is_classified_and_retried_then_raises(tmp_path: Path) -> None:
    repo = RecordingAnalysisRepo()
    events = RecordingEvents()
    runner = PipelineRunner([OkStage(), SlowStage()], repo, events)

    with pytest.raises(TransientPipelineError) as exc_info:
        runner.run("a1", make_context(tmp_path), {})

    assert exc_info.value.error_code == "TIMEOUT"
    assert repo.progress_calls == [("a1", "ok_stage", "slow_stage", 2, 2)]
    assert repo.processing_calls == [("a1", "ok_stage", 1, 2)]
    assert events.stage_events == [("a1", "ok_stage", 1, 2), ("a1", "slow_stage", 2, 2)]


def test_transient_failure_retries_then_succeeds(tmp_path: Path) -> None:
    marker = tmp_path / "attempts.txt"
    repo = RecordingAnalysisRepo()
    runner = PipelineRunner([FlakyStage(marker)], repo, RecordingEvents())

    outcome = runner.run("a2", make_context(tmp_path), {})

    assert outcome == RunOutcome.COMPLETED
    assert repo.progress_calls[0][1] == "flaky_stage"


def test_transient_failure_raises_after_exhausting_retries(tmp_path: Path) -> None:
    runner = PipelineRunner([AlwaysFailsTransient()], RecordingAnalysisRepo(), RecordingEvents())
    with pytest.raises(TransientPipelineError):
        runner.run("a3", make_context(tmp_path), {})


def test_logical_failure_does_not_retry(tmp_path: Path) -> None:
    runner = PipelineRunner([LogicalFailStage()], RecordingAnalysisRepo(), RecordingEvents())

    start = time.monotonic()
    with pytest.raises(LogicalPipelineError) as exc_info:
        runner.run("a4", make_context(tmp_path), {})
    elapsed = time.monotonic() - start

    assert elapsed < 5, f"a logical error must not retry/backoff, took {elapsed}s"
    assert exc_info.value.error_code == "NO_VOICE_DETECTED"


def test_resumability_skips_already_done_stages(tmp_path: Path) -> None:
    marker = tmp_path / "attempts.txt"
    repo = RecordingAnalysisRepo()
    already_done = {
        "ok_stage": StageResult(
            stage="ok_stage", status=StageStatus.DONE, duration_ms=1, data={"cached": True}
        )
    }
    runner = PipelineRunner([OkStage(), FlakyStage(marker)], repo, RecordingEvents())

    runner.run("a5", make_context(tmp_path), already_done)

    # ok_stage is index 1 of 2 overall, but already_done -- flaky_stage
    # (index 2) is the one that actually starts running, and the resumed
    # job's WS/REST position must say so, not restart the count at 1.
    assert repo.processing_calls == [("a5", "flaky_stage", 2, 2)]
    assert [call[1] for call in repo.progress_calls] == ["flaky_stage"]


def test_should_stop_interrupts_between_stages(tmp_path: Path) -> None:
    repo = RecordingAnalysisRepo()
    runner = PipelineRunner([OkStage(), OkStage()], repo, RecordingEvents())

    outcome = runner.run("a6", make_context(tmp_path), {}, should_stop=lambda: True)

    assert outcome == RunOutcome.INTERRUPTED
    assert repo.progress_calls == []
