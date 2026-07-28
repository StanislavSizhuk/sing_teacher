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

# Retry policy for transient stage failures (spec 6.8).
MAX_STAGE_RETRIES = 2
RETRY_BACKOFF_BASE_SECONDS = 2.0

# Queue reliability (spec 10.1).
PENDING_CLAIM_MIN_IDLE = 15 * 60  # seconds a delivered job may sit unacked before reclaim
MAX_CLAIM_ATTEMPTS = 3  # after this many reclaims the job is given up on as failed
