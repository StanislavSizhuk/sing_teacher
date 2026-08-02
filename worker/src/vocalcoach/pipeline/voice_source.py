"""Resolves which audio file represents "the user's voice" for this
analysis (ADR-0034). `clean` never separates the recording, so this is
always `preprocess`'s canonical WAV. `mixed` inserts `SeparateRecordingStage`
right after `preprocess`; once it has run, every later consumer of user
audio must read its stem instead, or the shared feature cache and align's
own pitch extraction would silently disagree on which signal they are
looking at -- the exact class of curve mismatch ADR-0034 fixes on the
reference/recording side, reintroduced on this one instead if the two
callers below ever read different paths.

The two callers, `FeaturesStage` and `AlignStage`, are also the only two
places `preprocess`'s `recording_path` is ever read (spec 6.9) -- this
function is the one place that needs to know separation might have
happened at all.
"""

from __future__ import annotations

from pathlib import Path

from vocalcoach.models.context import AnalysisContext


def voice_audio_path(context: AnalysisContext) -> Path:
    """Returns the separated vocal stem if `separate_recording` has already
    run for this analysis, otherwise `preprocess`'s raw canonical WAV."""
    separated = context.completed.get("separate_recording")
    if separated is not None:
        return Path(separated.data["stem_path"])
    return Path(context.result("preprocess").data["recording_path"])
