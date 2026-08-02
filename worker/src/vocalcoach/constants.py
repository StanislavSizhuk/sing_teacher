"""Fixed pipeline constants (spec 6.2, 6.3). These describe the ML design
itself, not operator-tunable behaviour, so they are code constants rather
than env config (spec 12.1: magic numbers are forbidden, but a constant with
a name and a spec citation is not a magic number).
"""

from __future__ import annotations

# Stage 1 preprocessing targets (spec 6.3.1).
PIPELINE_SAMPLE_RATE_HZ = 22050
TARGET_LOUDNESS_LUFS = -23.0

# Warm-path (A1-A10) per-stage timeouts in seconds (spec 6.5 table, M2).
PREPROCESS_TIMEOUT_SECONDS = 45  # A1: recording only, the reference half moved to P1 (M2)
# ADR-0034: Demucs separation of the user's own recording, `mixed` only.
# Matches SEPARATE_REFERENCE_TIMEOUT_SECONDS -- same model, same worst case.
# Recordings are capped shorter (MAX_AUDIO_SECONDS=360) than the cold path's
# 6-minute reference assumption, so this is a safety margin, not a measured
# figure; re-measure per docs/PERFORMANCE.md once real numbers exist.
SEPARATE_RECORDING_TIMEOUT_SECONDS = 600
ALIGN_TIMEOUT_SECONDS = 60
PITCH_TIMEOUT_SECONDS = 180
RHYTHM_TIMEOUT_SECONDS = 30
VIBRATO_TIMEOUT_SECONDS = 30
DYNAMICS_TIMEOUT_SECONDS = 30
TIMBRE_TIMEOUT_SECONDS = 30
BREATH_TIMEOUT_SECONDS = 30

# Cold-path (P1-P4) per-stage timeouts in seconds (spec 6.4 table, M2): run
# once per song, asynchronously, well before any analysis waits on them.
PREP_REFERENCE_TIMEOUT_SECONDS = 60  # P1: decode/normalize the reference mixture
SEPARATE_REFERENCE_TIMEOUT_SECONDS = 600  # P2: Demucs
TRANSCRIBE_TIMEOUT_SECONDS = 240  # P3: faster-whisper, optional (FR-18)
PREP_REFERENCE_PITCH_TIMEOUT_SECONDS = 120  # P4: reference pitch curve

# Below this integrated loudness (LUFS, ITU-R BS.1770) a separated vocal
# stem is mostly separation artifacts, not an audible voice (spec 6.8
# REFERENCE_TOO_QUIET). Shared by SeparateReferenceStage and
# SeparateRecordingStage (ADR-0034) -- one Demucs-output quality floor,
# not two copies that could drift.
MIN_VOCAL_LOUDNESS_LUFS = -50.0

# Pitch detection range: C2 (65.4 Hz) to C6 (1046.5 Hz) comfortably spans a
# solo singing voice from low bass to high soprano/whistle-adjacent belting.
PITCH_FMIN_HZ = 65.0
PITCH_FMAX_HZ = 1050.0
PITCH_HOP_SECONDS = 0.01  # 10ms frames, standard for both CREPE and pYIN

# A recording with fewer voiced pitch frames than this fraction is treated
# as containing no singing at all (spec 6.8 NO_VOICE_DETECTED).
MIN_VOICED_FRACTION = 0.05

# 100 cents (one semitone) of average deviation maps to a pitch score of 0;
# 0 cents maps to 100. Linear in between -- a starting point, not a
# calibrated curve (spec 19 risk table: scoring gets calibrated against
# golden fixtures once they exist).
PITCH_SCORE_CENTS_FOR_ZERO = 100.0

# Stage 3 shared feature cache (spec 6.9): one MFCC and one RMS envelope per
# audio file, computed once and reused by every stage that used to compute
# its own (align + timbre shared this exact MFCC hop/coefficient count
# already; dynamics + breath shared this exact RMS hop already -- the cache
# just stops paying for that twice). ALIGN_WINDOW_SECONDS/MAX_NORMALIZED_DISTANCE
# stay with the align stage itself (spec 6.7) since they describe its DTW,
# not a cached representation.
FEATURES_TIMEOUT_SECONDS = 30
FEATURES_HOP_SECONDS = 0.05
FEATURES_MFCC_COEFFICIENTS = 13

# Stage 5 alignment (spec ADR-0004, 6.7): level 1's Sakoe-Chiba band, around
# the literal diagonal, bounds DTW to a plausible tempo drift -- both to keep
# the banded cost matrix tractable within the timeout and to reject wildly
# diverging takes outright instead of forcing a bad alignment (spec 6.8 risk
# table). Level 2 then refines within a much narrower band centered on level
# 1's own path, at a finer hop (PITCH_HOP_SECONDS).
ALIGN_WINDOW_SECONDS = 10.0
ALIGN_REFINE_WINDOW_SECONDS = 0.2
# ADR-0033: empirical starting point for the banded DTW's per-step
# normalized cost on the pitch-class unit-circle embedding
# (dsp/pitch_embedding.py, PITCH_HOP_SECONDS, level 2's own hop, since
# that is the pass this ceiling is checked against) -- bounded [0, 2] by
# the embedding itself, unlike MFCC's open-ended scale. Recalibrate once
# golden fixtures exist (spec 19). Measured directly (banded_dtw on real
# pyin extractions of synthetic melodies, not just asserted): identical
# takes 0.00, the same melody an octave up 0.0017 / down 0.0041, ~40 cents
# flat throughout 0.0995, a different vibrato style 0.056 -- all comfortably
# under 0.2. A genuinely different melody at comparable length/range
# measured 0.55, and a wide-range mismatch 0.81. 0.45 sits in the gap with
# margin on both sides.
ALIGN_PITCH_MAX_NORMALIZED_DISTANCE = 0.45
# Upfront size guard (spec 6.7, NFR-16): refuses to even start a DTW whose
# banded cell count would exceed this, rather than let a pathological input
# (near-duplicate, but each hours long) eat unbounded memory/time.
DTW_MAX_CELLS = 50_000_000

# ADR-0032: when the direct (offset 0) alignment attempt fails, the
# fallback search only considers reference start offsets up to this many
# seconds in -- a bound, not a calibrated figure (most song intros are
# well under a minute), chosen specifically so the cheap unwarped scan
# stays O(n * search_range) instead of reintroducing the O(n * m) memory
# NFR-16/DTW_MAX_CELLS exist to avoid.
ALIGN_MAX_START_OFFSET_SECONDS = 60.0
# How many of the cheap scan's lowest-scoring candidate offsets get
# verified against the real (banded, tempo-tolerant) pipeline -- more than
# one because the unwarped scan is only an approximation of what the real
# DTW cost will be.
ALIGN_START_OFFSET_CANDIDATE_COUNT = 3

# Stage 6 rhythm: an onset within this many milliseconds of the reference's
# (mapped through DTW) counts as on time; the score decays linearly to 0 at
# this offset (spec 6.3.6).
RHYTHM_ONSET_TOLERANCE_MS = 200.0

# Stage 7 vibrato: singing vibrato typically oscillates at 4-8 Hz with at
# least a few tens of cents of depth (spec 6.3.7); below MIN_DEPTH_CENTS is
# treated as "no vibrato" rather than noise being mistaken for one.
VIBRATO_MIN_RATE_HZ = 3.5
VIBRATO_MAX_RATE_HZ = 9.0
VIBRATO_MIN_DEPTH_CENTS = 20.0
VIBRATO_MIN_SEGMENT_SECONDS = 0.3  # shortest sustained-pitch run worth analyzing
VIBRATO_AUTOCORR_PEAK_THRESHOLD = 0.3  # normalized autocorrelation peak needed to call it periodic
VIBRATO_RATE_TOLERANCE_HZ = 2.0  # rate error at which the rate-match half-score reaches 0
VIBRATO_DEPTH_TOLERANCE_CENTS = 50.0  # depth error at which the depth-match half-score reaches 0
VIBRATO_PRESENCE_MISMATCH_SCORE = 40.0  # one signal has vibrato and the other doesn't

# Stage 10 breath: a frame quieter than this, relative to the track's own
# peak RMS, counts as silence; sustained for MIN_PAUSE_SECONDS it is a
# breath/phrase boundary (spec 6.3.10). -35 dB below peak is well under
# normal singing dynamics but above digital-silence noise floor.
BREATH_SILENCE_RELATIVE_DB = -35.0
BREATH_MIN_PAUSE_SECONDS = 0.2
BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5

# VAD gate (spec 6.5, A2): reuses BREATH_SILENCE_RELATIVE_DB's "is this
# frame silence" definition (dsp/vad.py) rather than a second threshold for
# the same question. A silent run must last at least this long before the
# pitch detector is skipped over it -- this is a performance threshold
# (worth the gating overhead), not a scoring one, so it is deliberately
# looser than BREATH_MIN_PAUSE_SECONDS, which exists to catch real breath
# gaps precisely.
VAD_MIN_SILENT_RUN_SECONDS = 0.3

# torchcrepe: "tiny" trades some accuracy for CPU speed, needed to keep a
# ~6-minute clip inside the 180s pitch-stage timeout on 4 vCPU (spec 6.2,
# risk table: "pYIN замість CREPE" is the documented fallback if this still
# isn't fast enough on real hardware).
CREPE_MODEL_CAPACITY = "tiny"
CREPE_BATCH_SIZE = 2048
CREPE_VOICED_THRESHOLD = 0.5  # torchcrepe periodicity below this = unvoiced

# Retry policy for transient stage failures (spec 6.8).
MAX_STAGE_RETRIES = 2
RETRY_BACKOFF_BASE_SECONDS = 2.0

# Queue reliability (spec 10.1, 10.3). Both streams share one threshold:
# ADR-0034 gave `analyses:run` a stage (SeparateRecordingStage, `mixed`
# only) that can run as long as `songs:prep`'s own Demucs pass (P2), so the
# 15-minute bar that used to be safe only because "no warm-path stage runs
# that long" no longer holds -- a still-working `mixed` analysis could get
# reclaimed mid-flight and eventually fail (MAX_CLAIM_ATTEMPTS) instead of
# finishing.
PENDING_CLAIM_MIN_IDLE = (
    20 * 60
)  # seconds a delivered analyses:run job may sit unacked before reclaim
SONGS_PREP_PENDING_CLAIM_MIN_IDLE = 20 * 60
MAX_CLAIM_ATTEMPTS = 3  # after this many reclaims the job is given up on as failed

# Stage A3 recording-condition check (spec 2.3, 6.16): reads the recording
# from *before* SeparateRecordingStage runs (ADR-0034) -- its whole job is
# comparing what the user actually recorded against what they declared, so
# it must see the raw mixture, not a stem that already had accompaniment
# removed. See pipeline/stages/recording_condition.py for the
# accompaniment_level formula itself.
RECORDING_CONDITION_TIMEOUT_SECONDS = 30
# A median over a handful of unvoiced frames is noise, not a signal -- a
# single pitch-detector edge artifact (observed: pYIN's very first frame,
# on an otherwise perfectly voiced clean tone) would otherwise set the
# entire "unvoiced" median off one sample. Below this many unvoiced frames,
# accompaniment_level is reported as 0 rather than computed from too little
# data to be meaningful.
RECORDING_CONDITION_MIN_UNVOICED_FRAMES = 10

# Stage A8 key-shift normalization (spec 6.8). Budget is 5s (spec 6.17);
# timeout carries the usual margin over budget the other stages use.
KEY_NORMALIZATION_TIMEOUT_SECONDS = 10

# Stage 12 aggregation (spec 6.2/6.3.11, FR-32).
AGGREGATE_TIMEOUT_SECONDS = 10

# Feedback tiers every aspect's report text reads off its own score
# against (spec FR-32): EXCELLENT reads as "nailed it", GOOD as "solid,
# minor notes", FAIR as "noticeable room to improve"; below FAIR is POOR.
# Starting points, not calibrated against real singers (same caveat as
# every other scoring threshold in this file -- spec 19 risk table).
FEEDBACK_EXCELLENT_THRESHOLD = 90.0
FEEDBACK_GOOD_THRESHOLD = 75.0
FEEDBACK_FAIR_THRESHOLD = 50.0

# FR-31: a piano-roll frame whose |deviation_cents| exceeds this is drawn as
# an off-pitch note. Half a semitone is comfortably past normal intonation
# wobble but well inside a genuinely wrong note.
PIANO_ROLL_OFF_PITCH_CENTS = 50.0

# Stage A10 confidence model (spec 6.15). Each is the trigger point for one
# named warning/confidence step-down; deliberately below the *hard-failure*
# threshold covering the same signal (MIN_VOICED_FRACTION,
# ALIGN_MAX_NORMALIZED_DISTANCE) -- this is "worth a caveat", not "worth
# failing the analysis outright".
CONFIDENCE_LOW_VOICED_RATIO = 0.5
CONFIDENCE_WEAK_ALIGNMENT_COST = 45.0
