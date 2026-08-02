"""Stage 5: DTW-align the user's recording to the reference vocal stem
(spec 6.3.4, ADR-0004, 6.7). Every later stage compares the two signals
through the mapping this stage produces, since the user did not sing at
exactly the reference's tempo.

Two levels (spec 6.7): a coarse pass over the shared feature cache's MFCC
(one frame every `FEATURES_HOP_SECONDS`) finds the overall correspondence
within a wide-but-bounded band; a second pass refines it at a much finer
hop (`PITCH_HOP_SECONDS`), in a narrow band centered on the coarse path
instead of the diagonal. Both passes are banded (`O(n * band)` memory, spec
NFR-16) and numba-jit (NFR-17) -- see `dsp/dtw.py`.

ADR-0030: when the recording and reference differ in duration by more than
`ALIGN_WINDOW_SECONDS` alone -- a take cut short, or one that ran past the
song's own end -- that used to hard-fail here (`dtw.py`'s own
"unreachable" check) even though the *content* the two share might align
just fine. `_crop_to_overlap` below crops the longer side down to exactly
the shorter side's length before either DTW pass runs (not plus the usual
tempo-drift band -- see that function's own docstring for why the band
itself would force an unnaturally stretched path), and reports that it
did so (`length_mismatch` in this stage's `StageResult.data`) rather than
raising.

ADR-0032: `_crop_to_overlap` still assumes both signals *start* together.
A reference that opens with an instrumental intro, sung over by a
recording that only starts once the user starts singing, breaks that
assumption outright -- frame 0 of the recording is actually frame `k` of
the reference, not frame 0. When the direct (offset 0) attempt fails
either way (unreachable within the band, or reachable but over
`ALIGN_MAX_NORMALIZED_DISTANCE`), `_find_reference_start_offset` searches
a bounded range of candidate reference start frames (`dsp.dtw.
locate_start_offset_scores`, deliberately not full DTW -- see its own
docstring) and retries the *same* two-level pipeline anchored at the best
candidate. A genuine content mismatch still raises `AlignmentFailed`
exactly as before once no offset works either -- `dtw.py`'s own pipeline
is otherwise unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vocalcoach.constants import (
    ALIGN_MAX_NORMALIZED_DISTANCE,
    ALIGN_MAX_START_OFFSET_SECONDS,
    ALIGN_REFINE_WINDOW_SECONDS,
    ALIGN_START_OFFSET_CANDIDATE_COUNT,
    ALIGN_TIMEOUT_SECONDS,
    ALIGN_WINDOW_SECONDS,
    FEATURES_HOP_SECONDS,
    PITCH_HOP_SECONDS,
)
from vocalcoach.dsp.dtw import WarpingPath, banded_dtw, locate_start_offset_scores, refine_center
from vocalcoach.dsp.features import compute_mfcc, load_shared_features
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "align"

_MAX_NORMALIZED_DISTANCE_MESSAGE = (
    "DTW normalized distance {distance:.1f} exceeds the {ceiling} ceiling -- "
    "recording and reference diverge too far in tempo/content to align reliably"
)


def _crop_to_overlap(
    user_mfcc: np.ndarray, reference_mfcc: np.ndarray, coarse_band: int
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Crops whichever side is longer down to *exactly* the shorter side's
    frame count when the two differ by more than `coarse_band` -- so a
    recording that is simply much shorter or longer than the reference (a
    take cut short, or one that ran past the song's own end) can still be
    scored on the overlap instead of failing outright (ADR-0030).

    Cropped to the shorter length exactly, not shorter-plus-band: both
    `banded_dtw` calls this feeds always force the *last* frame of each
    side to match the other's last frame (that is what "reach the far
    corner" means, spec 6.7's own endpoint constraint) -- cropping to
    shorter+band would force whichever side is naturally shorter to be
    stretched across the full extra band's worth of the other side's
    frames, producing an unnaturally warped path that then routinely
    violates level 2's much narrower refine band. Cropping to an exact
    match turns this back into an ordinary same-length pass, so the
    existing diagonal-plus-band tolerance still absorbs genuine tempo
    drift on the overlap, same as it does when lengths already matched.

    Frame counts within `coarse_band` of each other are left untouched:
    that gap is already absorbed by the band as ordinary tempo variation,
    not a length mismatch. Only ever crops the *end* of the longer side --
    both signals are assumed to start together, the same assumption the
    diagonal band itself already makes.
    """
    n, m = user_mfcc.shape[0], reference_mfcc.shape[0]
    if abs(n - m) <= coarse_band:
        return user_mfcc, reference_mfcc, False
    if m > n:
        return user_mfcc, reference_mfcc[:n], True
    return user_mfcc[:m], reference_mfcc, True


@dataclass(frozen=True)
class _AlignAttempt:
    fine: WarpingPath
    coarse: WarpingPath
    length_mismatch: bool


def _attempt_align(
    user_mfcc: np.ndarray,
    reference_mfcc: np.ndarray,
    user_fine_mfcc: np.ndarray,
    reference_fine_mfcc: np.ndarray,
    offset_frames: int,
    coarse_band: int,
    refine_band: int,
) -> _AlignAttempt:
    """One full two-level alignment attempt (ADR-0030's crop-to-overlap,
    then the coarse + refine banded DTW passes), against the reference
    starting at `offset_frames` coarse-hop frames in (0 for the ordinary
    case, ADR-0032's found candidate otherwise). Raises `AlignmentFailed`
    (from `banded_dtw`, or the caller's own ceiling check) if this offset
    doesn't work either -- the caller decides whether to retry with a
    different offset or give up for real.
    """
    offset_reference_mfcc = reference_mfcc[offset_frames:]
    cropped_user_mfcc, cropped_reference_mfcc, length_mismatch = _crop_to_overlap(
        user_mfcc, offset_reference_mfcc, coarse_band
    )
    coarse = banded_dtw(cropped_user_mfcc, cropped_reference_mfcc, coarse_band)

    fine_offset_frames = round(offset_frames * FEATURES_HOP_SECONDS / PITCH_HOP_SECONDS)
    cropped_user_fine_mfcc = user_fine_mfcc
    cropped_reference_fine_mfcc = reference_fine_mfcc[fine_offset_frames:]
    if length_mismatch:
        # Same reasoning as the non-offset case: crop the fine-hop pair to
        # the exact same time extent the coarse pass just used, in
        # seconds (FEATURES_HOP_SECONDS and PITCH_HOP_SECONDS round
        # differently -- refine_center's own docstring).
        keep_seconds = (
            min(cropped_user_mfcc.shape[0], cropped_reference_mfcc.shape[0]) * FEATURES_HOP_SECONDS
        )
        keep_frames = round(keep_seconds / PITCH_HOP_SECONDS)
        cropped_user_fine_mfcc = cropped_user_fine_mfcc[:keep_frames]
        cropped_reference_fine_mfcc = cropped_reference_fine_mfcc[:keep_frames]

    refine_full_center = refine_center(
        coarse,
        FEATURES_HOP_SECONDS,
        PITCH_HOP_SECONDS,
        n_fine=cropped_user_fine_mfcc.shape[0],
        m_fine=cropped_reference_fine_mfcc.shape[0],
    )
    fine = banded_dtw(
        cropped_user_fine_mfcc,
        cropped_reference_fine_mfcc,
        refine_band,
        full_center=refine_full_center,
    )
    return _AlignAttempt(fine=fine, coarse=coarse, length_mismatch=length_mismatch)


def _find_reference_start_offset(
    user_mfcc: np.ndarray,
    reference_mfcc: np.ndarray,
    user_fine_mfcc: np.ndarray,
    reference_fine_mfcc: np.ndarray,
    coarse_band: int,
    refine_band: int,
    original_exc: AlignmentFailed,
) -> tuple[int, _AlignAttempt]:
    """ADR-0032: the direct (offset 0) attempt failed -- search a bounded
    range of candidate reference start frames for one where the recording
    actually lines up (a reference that opens with an instrumental intro
    the recording didn't include), verifying each candidate against the
    *same* two-level pipeline and ceiling the direct attempt used. Re-raises
    the *original* failure (not a new one about the search itself) once no
    candidate works either -- the search finding nothing is not new
    information a user can act on beyond what the original error already said.
    """
    max_offset_frames = min(
        round(ALIGN_MAX_START_OFFSET_SECONDS / FEATURES_HOP_SECONDS),
        reference_mfcc.shape[0] - 1,
    )
    if max_offset_frames < 1:
        raise original_exc

    scores = locate_start_offset_scores(user_mfcc, reference_mfcc, max_offset_frames)
    candidates = np.argsort(scores)[:ALIGN_START_OFFSET_CANDIDATE_COUNT]

    for candidate in candidates:
        offset_frames = int(candidate)
        if offset_frames == 0 or not np.isfinite(scores[offset_frames]):
            continue
        try:
            attempt = _attempt_align(
                user_mfcc,
                reference_mfcc,
                user_fine_mfcc,
                reference_fine_mfcc,
                offset_frames,
                coarse_band,
                refine_band,
            )
        except AlignmentFailed:
            continue
        if attempt.fine.normalized_distance <= ALIGN_MAX_NORMALIZED_DISTANCE:
            return offset_frames, attempt

    raise original_exc


class AlignStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `index1`/`index2` (the warping path, as parallel
    frame-index arrays into the user/reference sequences at the fine hop),
    `hop_seconds`, `normalized_distance`, `coarse_normalized_distance`
    (level 1's own cost, kept for observability), `length_mismatch` (bool,
    ADR-0030: whether the recording and reference had to be cropped to a
    shared overlap before aligning), `reference_start_offset_seconds`
    (float, ADR-0032: how far into the reference the recording's own start
    was found to actually correspond to, 0.0 when untouched). `AggregateStage`
    turns either signal into a confidence step-down and a warning, not a
    failure.
    """

    name = STAGE_NAME
    timeout_seconds = ALIGN_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)

        coarse_band = max(1, round(ALIGN_WINDOW_SECONDS / FEATURES_HOP_SECONDS))
        refine_band = max(1, round(ALIGN_REFINE_WINDOW_SECONDS / PITCH_HOP_SECONDS))
        user_fine_mfcc = compute_mfcc(Path(preprocess["recording_path"]), PITCH_HOP_SECONDS)
        reference_fine_mfcc = compute_mfcc(context.reference_vocal_stem_path, PITCH_HOP_SECONDS)

        offset_frames = 0
        try:
            attempt = _attempt_align(
                features.user.mfcc,
                features.reference.mfcc,
                user_fine_mfcc,
                reference_fine_mfcc,
                offset_frames,
                coarse_band,
                refine_band,
            )
            if attempt.fine.normalized_distance > ALIGN_MAX_NORMALIZED_DISTANCE:
                raise AlignmentFailed(
                    _MAX_NORMALIZED_DISTANCE_MESSAGE.format(
                        distance=attempt.fine.normalized_distance,
                        ceiling=ALIGN_MAX_NORMALIZED_DISTANCE,
                    )
                )
        except AlignmentFailed as direct_failure:
            offset_frames, attempt = _find_reference_start_offset(
                features.user.mfcc,
                features.reference.mfcc,
                user_fine_mfcc,
                reference_fine_mfcc,
                coarse_band,
                refine_band,
                direct_failure,
            )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "index1": attempt.fine.index1,
                "index2": attempt.fine.index2,
                "hop_seconds": PITCH_HOP_SECONDS,
                "normalized_distance": attempt.fine.normalized_distance,
                "coarse_normalized_distance": attempt.coarse.normalized_distance,
                "length_mismatch": attempt.length_mismatch,
                "reference_start_offset_seconds": offset_frames * FEATURES_HOP_SECONDS,
            },
        )
