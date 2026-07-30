"""Fixed pipeline constants (spec 6.2, 6.3). These describe the ML design
itself, not operator-tunable behaviour, so they are code constants rather
than env config (spec 12.1: magic numbers are forbidden, but a constant with
a name and a spec citation is not a magic number).
"""

from __future__ import annotations

# Stage 1 preprocessing targets (spec 6.3.1).
PIPELINE_SAMPLE_RATE_HZ = 22050
TARGET_LOUDNESS_LUFS = -23.0

# Per-stage timeouts in seconds (spec 6.2 table).
PREPROCESS_TIMEOUT_SECONDS = 30
SEPARATE_REFERENCE_TIMEOUT_SECONDS = 300
TRANSCRIBE_TIMEOUT_SECONDS = 180
ALIGN_TIMEOUT_SECONDS = 60
PITCH_TIMEOUT_SECONDS = 180
RHYTHM_TIMEOUT_SECONDS = 30
VIBRATO_TIMEOUT_SECONDS = 30
DYNAMICS_TIMEOUT_SECONDS = 30
TIMBRE_TIMEOUT_SECONDS = 30
BREATH_TIMEOUT_SECONDS = 30

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

# Stage 4 alignment (spec ADR-0004): MFCC hop and a Sakoe-Chiba window that
# bounds DTW to a plausible tempo drift, both to keep the O(window * N) cost
# matrix tractable within the 60s timeout and to reject wildly diverging
# takes outright instead of forcing a bad alignment (spec 6.8 risk table).
ALIGN_HOP_SECONDS = 0.05
ALIGN_WINDOW_SECONDS = 10.0
ALIGN_MFCC_COEFFICIENTS = 13
# Empirical starting point for dtw-python's per-step normalized distance on
# ALIGN_MFCC_COEFFICIENTS-dimensional MFCC frames; recalibrate once golden
# fixtures exist (spec 19).
ALIGN_MAX_NORMALIZED_DISTANCE = 40.0

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

# RMS envelope hop, shared by stage 8 (dynamics) and stage 10 (breath):
# loudness contour shape doesn't need pitch-grade (10ms) resolution.
ENVELOPE_HOP_SECONDS = 0.05

# Stage 9 timbre: MFCC coefficient count for the profile comparison (spec 6.3.9).
TIMBRE_MFCC_COEFFICIENTS = 13

# Stage 10 breath: a frame quieter than this, relative to the track's own
# peak RMS, counts as silence; sustained for MIN_PAUSE_SECONDS it is a
# breath/phrase boundary (spec 6.3.10). -35 dB below peak is well under
# normal singing dynamics but above digital-silence noise floor.
BREATH_SILENCE_RELATIVE_DB = -35.0
BREATH_MIN_PAUSE_SECONDS = 0.2
BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5

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

# Queue reliability (spec 10.1).
PENDING_CLAIM_MIN_IDLE = 15 * 60  # seconds a delivered job may sit unacked before reclaim
MAX_CLAIM_ATTEMPTS = 3  # after this many reclaims the job is given up on as failed

# Stage 11 recording-condition check (spec 2.3, 6.9): the user's own
# recording is never run through Demucs (ADR-0003), so this is a cheap
# substitute for real source separation -- a frame loud enough to matter,
# relative to this recording's own peak RMS, yet where the pitch stage (5)
# found no single clear pitch, is a soft signal of non-vocal energy
# (instruments, noise) rather than a singing voice. -20 dB is well above
# BREATH_SILENCE_RELATIVE_DB, deliberately: this only wants to catch frames
# energetic enough to plausibly be an instrument, not normal room tone
# under a quiet vocal.
RECORDING_CONDITION_TIMEOUT_SECONDS = 30
RECORDING_CONDITION_LOUD_RELATIVE_DB = -20.0
# A recording where at least this fraction of frames are loud-yet-unvoiced
# is flagged in the report (spec 6.9) -- a starting point, not calibrated
# against real recordings (same caveat as every other threshold here, spec
# 19 risk table); every voice has *some* loud-but-momentarily-unvoiced
# frames (consonants, breath noise), so this sits well above zero.
RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION = 0.3

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
