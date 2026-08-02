"""Stage 5: DTW-align the user's recording to the reference vocal stem
(spec 6.3.4, ADR-0004, 6.7). Every later stage compares the two signals
through the mapping this stage produces, since the user did not sing at
exactly the reference's tempo.

ADR-0033: both DTW passes run on **pitch contour** (melody shape), not
MFCC -- melody is largely invariant to who is singing, unlike timbre,
so two people singing the same song in different voices/registers now
align instead of failing. `dsp/pitch_embedding.py` embeds each pitch
value as a point on the unit circle (one turn per octave), so the
existing banded-DTW kernel's plain Euclidean distance already behaves
the way a musical distance should, unchanged. The user's F0 curve is
extracted here (mode-aware: `detect_gated` for `clean`, `extract_melody`
for `mixed` -- the same extraction `PitchStage`/`MelodyPitchStage` used
to each do themselves) and exposed as `user_pitch_curve` in this stage's
own result, so those stages read it back instead of re-extracting it.
The reference's F0 curve needs no extraction at all: it is cold-path
output, already on `context.reference_pitch`. Pitch has only one natural
hop (`PITCH_HOP_SECONDS`); the coarse pass strides through it instead of
computing a separate coarse representation the way MFCC needed one.

ADR-0030: when the recording and reference differ in duration by more
than `ALIGN_WINDOW_SECONDS` alone -- a take cut short, or one that ran
past the song's own end -- that used to hard-fail here (`dtw.py`'s own
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
either way (unreachable within the band, or reachable but over the
ceiling), `_find_reference_start_offset` searches a bounded range of
candidate reference start frames (`dsp.dtw.locate_start_offset_scores`,
deliberately not full DTW -- see its own docstring) and retries the
*same* two-level pipeline anchored at the best candidate. A genuine
content mismatch still raises `AlignmentFailed` exactly as before once no
offset works either -- `dtw.py`'s own pipeline is otherwise unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import (
    ALIGN_MAX_START_OFFSET_SECONDS,
    ALIGN_PITCH_MAX_NORMALIZED_DISTANCE,
    ALIGN_REFINE_WINDOW_SECONDS,
    ALIGN_START_OFFSET_CANDIDATE_COUNT,
    ALIGN_TIMEOUT_SECONDS,
    ALIGN_WINDOW_SECONDS,
    FEATURES_HOP_SECONDS,
    MIN_VOICED_FRACTION,
    PITCH_HOP_SECONDS,
)
from vocalcoach.dsp.dtw import WarpingPath, banded_dtw, locate_start_offset_scores, refine_center
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.dsp.melody import extract_melody
from vocalcoach.dsp.pitch_detection import detect_gated
from vocalcoach.dsp.pitch_embedding import embed_pitch_curve
from vocalcoach.dsp.pitch_scoring import voiced_fraction
from vocalcoach.errors import AlignmentFailed, MelodyExtractionFailed, NoVoiceDetected
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import PitchDetector

STAGE_NAME = "align"

_MAX_NORMALIZED_DISTANCE_MESSAGE = (
    "DTW normalized distance {distance:.2f} exceeds the {ceiling} ceiling -- "
    "recording and reference diverge too far in tempo/content to align reliably"
)


def _crop_to_overlap(
    user_embedding: np.ndarray, reference_embedding: np.ndarray, coarse_band: int
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
    n, m = user_embedding.shape[0], reference_embedding.shape[0]
    if abs(n - m) <= coarse_band:
        return user_embedding, reference_embedding, False
    if m > n:
        return user_embedding, reference_embedding[:n], True
    return user_embedding[:m], reference_embedding, True


@dataclass(frozen=True)
class _AlignAttempt:
    fine: WarpingPath
    coarse: WarpingPath
    length_mismatch: bool


def _attempt_align(
    user_hz: list[float | None],
    reference_hz: list[float | None],
    offset_frames: int,
    coarse_stride: int,
    coarse_band: int,
    refine_band: int,
) -> _AlignAttempt:
    """One full two-level alignment attempt (ADR-0030's crop-to-overlap,
    then the coarse + refine banded DTW passes, ADR-0033's pitch-class
    embedding), against the reference starting at `offset_frames` fine
    (`PITCH_HOP_SECONDS`) frames in (0 for the ordinary case, ADR-0032's
    found candidate otherwise). Raises `AlignmentFailed` (from
    `banded_dtw`, or the caller's own ceiling check) if this offset
    doesn't work either -- the caller decides whether to retry with a
    different offset or give up for real.
    """
    offset_reference_hz = reference_hz[offset_frames:]

    user_coarse = embed_pitch_curve(user_hz[::coarse_stride])
    reference_coarse = embed_pitch_curve(offset_reference_hz[::coarse_stride])
    cropped_user_coarse, cropped_reference_coarse, length_mismatch = _crop_to_overlap(
        user_coarse, reference_coarse, coarse_band
    )
    coarse = banded_dtw(cropped_user_coarse, cropped_reference_coarse, coarse_band)

    cropped_user_fine = embed_pitch_curve(user_hz)
    cropped_reference_fine = embed_pitch_curve(offset_reference_hz)
    if length_mismatch:
        # Same reasoning as ADR-0030's original: crop the fine-hop pair to
        # the exact same time extent the coarse pass just used. Pitch has
        # only the one natural hop, so this is exact in fine-frame counts,
        # no seconds-based conversion needed the way two different MFCC
        # hops once did.
        keep_frames = (
            min(cropped_user_coarse.shape[0], cropped_reference_coarse.shape[0]) * coarse_stride
        )
        cropped_user_fine = cropped_user_fine[:keep_frames]
        cropped_reference_fine = cropped_reference_fine[:keep_frames]

    refine_full_center = refine_center(
        coarse,
        coarse_stride * PITCH_HOP_SECONDS,
        PITCH_HOP_SECONDS,
        n_fine=cropped_user_fine.shape[0],
        m_fine=cropped_reference_fine.shape[0],
    )
    fine = banded_dtw(
        cropped_user_fine, cropped_reference_fine, refine_band, full_center=refine_full_center
    )
    return _AlignAttempt(fine=fine, coarse=coarse, length_mismatch=length_mismatch)


def _find_reference_start_offset(
    user_hz: list[float | None],
    reference_hz: list[float | None],
    coarse_stride: int,
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
        round(ALIGN_MAX_START_OFFSET_SECONDS / PITCH_HOP_SECONDS),
        len(reference_hz) - 1,
    )
    if max_offset_frames < 1:
        raise original_exc

    user_embedding = embed_pitch_curve(user_hz)
    reference_embedding = embed_pitch_curve(reference_hz)
    scores = locate_start_offset_scores(user_embedding, reference_embedding, max_offset_frames)
    candidates = np.argsort(scores)[:ALIGN_START_OFFSET_CANDIDATE_COUNT]

    for candidate in candidates:
        offset_frames = int(candidate)
        if offset_frames == 0 or not np.isfinite(scores[offset_frames]):
            continue
        try:
            attempt = _attempt_align(
                user_hz, reference_hz, offset_frames, coarse_stride, coarse_band, refine_band
            )
        except AlignmentFailed:
            continue
        if attempt.fine.normalized_distance <= ALIGN_PITCH_MAX_NORMALIZED_DISTANCE:
            return offset_frames, attempt

    raise original_exc


class AlignStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `index1`/`index2` (the warping path, as parallel
    frame-index arrays into the user/reference sequences at
    `PITCH_HOP_SECONDS`), `hop_seconds`, `normalized_distance`,
    `coarse_normalized_distance` (level 1's own cost, kept for
    observability), `length_mismatch` (bool, ADR-0030), `reference_start_
    offset_seconds` (float, ADR-0032, 0.0 when untouched), `user_pitch_curve`
    (ADR-0033: the user's raw F0 curve, mode-aware extraction, so
    `PitchStage`/`MelodyPitchStage` never re-extract it). `AggregateStage`
    turns `length_mismatch`/`reference_start_offset_seconds` into a
    confidence step-down and a warning, not a failure.
    """

    name = STAGE_NAME
    timeout_seconds = ALIGN_TIMEOUT_SECONDS

    def __init__(self, detector: PitchDetector) -> None:
        self._detector = detector

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])
        features = load_shared_features(Path(context.result("features").data["features_path"]))

        try:
            user_samples, _sr = read_mono(Path(preprocess["recording_path"]))
            if context.mode == "mixed":
                user_hz = extract_melody(user_samples, sample_rate, PITCH_HOP_SECONDS)
            else:
                user_hz = detect_gated(
                    self._detector,
                    user_samples,
                    sample_rate,
                    PITCH_HOP_SECONDS,
                    features.user.rms_fine,
                )
        finally:
            self._detector.release()

        fraction = voiced_fraction(user_hz)
        if fraction < MIN_VOICED_FRACTION:
            if context.mode == "mixed":
                raise MelodyExtractionFailed(
                    f"only {fraction:.1%} of the recording had a confident melody "
                    f"estimate, below the {MIN_VOICED_FRACTION:.0%} floor"
                )
            raise NoVoiceDetected(
                f"only {fraction:.1%} of the recording is voiced, "
                f"below the {MIN_VOICED_FRACTION:.0%} floor"
            )

        reference_hz = context.reference_pitch.hz
        coarse_stride = max(1, round(FEATURES_HOP_SECONDS / PITCH_HOP_SECONDS))
        coarse_band = max(1, round(ALIGN_WINDOW_SECONDS / FEATURES_HOP_SECONDS))
        refine_band = max(1, round(ALIGN_REFINE_WINDOW_SECONDS / PITCH_HOP_SECONDS))

        offset_frames = 0
        try:
            attempt = _attempt_align(
                user_hz, reference_hz, offset_frames, coarse_stride, coarse_band, refine_band
            )
            if attempt.fine.normalized_distance > ALIGN_PITCH_MAX_NORMALIZED_DISTANCE:
                raise AlignmentFailed(
                    _MAX_NORMALIZED_DISTANCE_MESSAGE.format(
                        distance=attempt.fine.normalized_distance,
                        ceiling=ALIGN_PITCH_MAX_NORMALIZED_DISTANCE,
                    )
                )
        except AlignmentFailed as direct_failure:
            offset_frames, attempt = _find_reference_start_offset(
                user_hz, reference_hz, coarse_stride, coarse_band, refine_band, direct_failure
            )

        user_curve = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=user_hz)

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
                "reference_start_offset_seconds": offset_frames * PITCH_HOP_SECONDS,
                "user_pitch_curve": user_curve.model_dump(mode="json"),
            },
        )
