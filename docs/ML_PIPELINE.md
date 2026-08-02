# ML Pipeline

Status: reflects stages E4-E5, M1, M2, and M3 (spec 18). M1: the single-
pipeline performance pass -- a shared feature cache, a VAD gate on pitch
detection, an own banded two-level DTW, parallel aspect stages,
`faster-whisper`, and dense curves stored as `bytea`. M2 (spec 6.2, 6.4,
6.5, 10): splits that single pipeline into a **cold path** (P1-P4: decode
the reference, separate its vocal stem, optionally transcribe it, detect
its pitch curve -- run exactly once per song, asynchronously, starting the
moment the song is added) and a **warm path** (A1-A10: everything an
analysis itself needs, run only once its song's cold path has reached
`ready`, always reading the reference vocal stem/pitch curve/lyrics the
cold path already cached instead of recomputing any of it). M3 (spec 6.6,
6.8, 6.14-6.16): adds the `mixed` analysis mode -- melody extraction as an
alternate pitch source, key-shift normalization, and the weight-profile/
confidence model that make an aspect's absence in `mixed` an honest `null`
rather than a silent `0` (FR-41). See "Mixed mode (M3)" below for the
mode-specific stages and `docs/PERFORMANCE.md` for measured before/after
numbers and `docs/adr/0015`, `0017`, `0021`-`0027` for the M1/M2/M3
decisions.

## Where the code lives

| Path | Responsibility |
|---|---|
| `worker/src/vocalcoach/pipeline/base.py` | `PipelineStage`/`ParallelGroup` contract, generic over `ContextT` (M2) so the same classes drive both paths |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage subprocess/timeout, retries, optional-stage skipping (M2), progress persistence (ADR-0012) |
| `worker/src/vocalcoach/pipeline/registry.py` | `ModelRegistry`: lazy Demucs/Whisper/CREPE/pYIN construction behind narrow `Protocol`s, shared by both paths |
| `worker/src/vocalcoach/pipeline/stages/` | One file per stage: `preprocess.py`/`features.py`/`align.py`/`pitch.py`/`melody.py` (M3, `mixed` only)/`key_normalization.py` (M3)/aspect stages/`recording_condition.py`/`aggregate.py` (warm), `prep_reference.py`/`separate_reference.py`/`transcribe.py`/`prep_reference_pitch.py` (cold) |
| `worker/src/vocalcoach/pipeline/report.py` | The FR-32 per-aspect text report, built from the same stage data -- M3: only the mode's own available aspects, plus an unavailable-aspect block (FR-41) |
| `worker/src/vocalcoach/scoring/` | M3, spec 12.3: `weights.py` (`MODE_ASPECTS`, the weighted-sum formula, `unavailable_aspects_for`), `confidence.py` (the high/medium/low model, spec 6.15) -- pure functions, no `PipelineContext` knowledge |
| `worker/src/vocalcoach/dsp/` | Shared feature cache (`features.py`), VAD gate (`vad.py`), banded two-level DTW (`dtw.py`), pitch-class unit-circle embedding for alignment (`pitch_embedding.py`, ADR-0033), VAD-gated pitch detection (`pitch_detection.py`, M2 -- shared by warm A3 and cold P4), pitch-vs-reference scoring (`pitch_scoring.py`, M3 -- shared by `pitch.py` and `melody.py`), melody extraction (`melody.py`, M3, `mixed` only, ADR-0025) |
| `worker/src/vocalcoach/runtime/` | M1: explicit BLAS/torch thread configuration (`threads.py`, spec 6.11) |
| `worker/src/vocalcoach/audio/` | Shared DSP helpers: ffmpeg wrapper (`decode_and_normalize`, M2: the one decode/normalize implementation both A1 and P1 call), loudness, WAV IO, DTW time-mapping |
| `worker/src/vocalcoach/queue/` | `scheduler.py` (M2: the two-stream priority loop, spec 10.2), `consumer.py` (Redis Streams, one instance per stream), `handler.py`/`prep_handler.py` (per-job-kind lifecycle), `streams.py` (M2: stream/group names), `events.py` (Redis Pub/Sub event publisher, ADR-0010) |
| `worker/src/vocalcoach/repositories/` | `AnalysisRepository`/`SongRepository` Postgres implementations |
| `worker/src/vocalcoach/worker.py` | Entrypoint: wires config -> repositories -> registry -> both stage sets -> two runners -> two handlers -> two consumers -> scheduler |

## Stage order

**Cold path (spec 6.4): once per song, asynchronously**

| # | Stage | Technology | Timeout | Required |
|---|---|---|---|---|
| P1 | `prep_reference` | `pyloudnorm`, ffmpeg resample (reference only) | 60s | yes |
| P2 | `separate_reference` | Demucs v4 (`htdemucs`, ADR-0003) | 600s | yes |
| P3 | `transcribe` | `faster-whisper` (`WHISPER_MODEL`, ADR-0014, ADR-0021) | 240s | **no** (FR-18) |
| P4 | `prep_reference_pitch` | CREPE/pYIN (`PITCH_ENGINE`), VAD-gated | 120s | yes |

**Warm path (spec 6.5): once per analysis, only once the song is `ready`**

| # | Stage | Technology | Timeout |
|---|---|---|---|
| A1 | `preprocess` | `pyloudnorm`, ffmpeg resample (recording only) | 45s |
| A2 | `features` (M1, spec 6.9) | `librosa` MFCC/RMS/onset, once per side | 30s |
| A3 | `align` | own two-level banded DTW over a pitch-contour embedding (ADR-0017, ADR-0033), CREPE/pYIN VAD-gated (ADR-0023) | 60s |
| A4 | `pitch` | reads A3's extracted user pitch curve, scores against the cold path's cached reference curve | 180s |
| A5-A9 | `rhythm`, `vibrato`, `dynamics`, `timbre`, `breath` | read A2's cache + A3's time map | 30s each |
| A10 | `recording_condition` | A2's fine RMS + A4's pitch curve (own logic, spec 6.9) | 30s |
| A11 | `aggregate` | weighted sum + text report (own logic) | 10s |

Every stage runs in its own spawned child process (ADR-0012) -- this is
what makes each timeout an enforceable ceiling rather than an advisory one,
and what satisfies spec 6.5's "Demucs and Whisper never resident together"
as a natural consequence rather than a special case (true across both
paths: the same `ModelRegistry` is shared between them, but the scheduler
never runs a cold-path and a warm-path job at once either, spec NFR-04/
NFR-07). `PipelineRunner` persists a `StageResult` (spec 6.1: `stage`,
`status` -- `done`, `failed`, or (M2) `skipped` for an optional stage that
failed without aborting the run -- `duration_ms`, `data`,
`error_code`/`error_message`) into `analyses.stages_json`/
`songs.prep_stages_json` after every stage, and publishes a WS `stage`
event (ADR-0010, warm path only -- the cold path has no per-stage WS push
yet, only the `prep_status`/`prep_stage` REST fields, FR-14) before it
starts the next one -- this is what makes progress visible in the UI and
what a retry resumes from (see "Resumability" below).

**Parallel aspect stages (M1, spec 6.10).** Stages A5-A9 depend only on
A2/A3/A4's already-finished output, never on each other, so
`worker.py::build_stages` groups them into one `pipeline.base.ParallelGroup`
by default (`PIPELINE_PARALLEL_ASPECTS=true`). `PipelineRunner` starts every
member's subprocess concurrently, forcing every BLAS thread env var to `1`
in its own environment right before spawning them (restored after) -- spec
6.10's explicit warning that N members x N threads each on a small box
makes parallelism a slowdown, not a speedup. `PIPELINE_PARALLEL_ASPECTS=false`
keeps them flat and sequential, for tests that need an exact, reproducible
stage order (spec 15.3); T13 (`tests/test_pipeline_parallel_consistency.py`)
checks both orderings score identically.

## Stage details

### Cold path (spec 6.4) -- once per song

1. **`prep_reference`** (P1) -- loudness-normalizes (target
   `TARGET_LOUDNESS_LUFS = -23.0`, ITU-R BS.1770 via `pyloudnorm`) and
   resamples the reference mixture to `PIPELINE_SAMPLE_RATE_HZ = 22050`,
   mono, via ffmpeg (`audio/ffmpeg.py::decode_and_normalize`, shared with
   A1's identical treatment of the recording -- M2 split what was one
   `preprocess` stage handling both files into this reference-only cold
   stage and A1's recording-only warm one, since they now run in
   different processes at different times). Independent of the Go API's
   own upload-time ffmpeg transcode (`api/internal/media.Processor.Transcode`),
   which only sanitizes the container (spec 11.3) -- this is the ML
   pipeline's own resample, for pitch/onset/MFCC analysis, not a security
   step.
2. **`separate_reference`** (P2) -- isolates the reference's vocal stem
   with Demucs (`DEMUCS_MODEL`, default `htdemucs`). The mono signal from
   P1 is duplicated to stereo before Demucs, since its pretrained models
   expect two channels. Demucs always processes and returns audio at its
   own native rate (44.1kHz for `htdemucs`) regardless of what sample rate
   it's told the input is (`separate_tensor`'s own docstring: "the wave
   will be resampled if it doesn't match the model") -- `DemucsSeparator`
   converts the separated stem back down to `PIPELINE_SAMPLE_RATE_HZ`
   before returning it, since every caller (`VocalSeparator`'s own
   contract) assumes "same sample rate as the input." Writes the isolated
   stem to
   `song-stem-<song_id>.wav` under the `song-stems` volume -- the
   scheduler never re-enqueues a song whose `prep_status` is already
   `ready` (spec 10.2), so this stage runs exactly once by construction
   and no longer needs a cache short-circuit of its own (pre-M2, this same
   Demucs call ran inline on every song's *first* analysis, gated by a
   `vocal_stem_processed` boolean check inside the stage itself). Raises
   `REFERENCE_TOO_QUIET` if the separated stem's own measured loudness
   (before its own -23 LUFS normalization) is below `MIN_VOCAL_LOUDNESS_LUFS
   = -50.0` -- the check runs on the isolated stem, not the original
   mixture, since a full-band mixture essentially never reads as "quiet"
   even when the vocal buried in it is.
3. **`transcribe`** (P3, `required = False`) -- `faster-whisper`
   (`WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE=int8`, ADR-0021) transcribes
   the vocal stem to words with per-word timecodes (`Lyrics`/`LyricsWord`)
   -- the same Whisper checkpoints `openai-whisper` used, on CTranslate2's
   faster CPU inference instead. The only stage in the whole pipeline
   declared optional (spec 6.3, FR-18): a timeout or any other failure is
   recorded by `PipelineRunner` as `StageStatus.SKIPPED` instead of
   aborting the cold path, and `SongPrepJobHandler` reads that as
   `songs.lyrics_available = false` rather than failing the whole song's
   prep over a transcript nothing downstream actually depends on for
   scoring.
4. **`prep_reference_pitch`** (P4) -- tracks the reference vocal stem's
   fundamental frequency at `PITCH_HOP_SECONDS = 0.01`,
   `PITCH_FMIN_HZ..PITCH_FMAX_HZ = 65..1050` (C2 to C6), VAD-gated exactly
   like A3's own `clean`-mode extraction (`dsp/pitch_detection.py::detect_gated`,
   shared by both -- spec 6.6 needs the same engine on both sides of a
   comparison for the result to be deterministic). The last cold-path stage: once it
   finishes, `SongPrepJobHandler` writes `songs.vocal_stem_path`/
   `reference_pitch`/`reference_pitch_meta` (`bytea` + JSONB sidecar, spec
   7.3/ADR-0022) and flips `prep_status` to `ready` in one write (see
   "Caching" below), then wakes every analysis of this song that was
   `waiting_for_reference` (spec 10.3, FR-16).

### Warm path (spec 6.5) -- once per analysis, only once the song is `ready`

5. **`preprocess`** (A1) -- the recording-only half of what P1 also does,
   same `decode_and_normalize` call, same targets.
6. **`features`** (A2, M1, spec 6.9) -- computes each shared
   representation exactly once per side (user recording, reference stem):
   `FEATURES_MFCC_COEFFICIENTS`-coefficient MFCC and an RMS envelope, both
   at `FEATURES_HOP_SECONDS = 0.05`, plus a finer RMS pass at
   `PITCH_HOP_SECONDS = 0.01` and onset timestamps -- for the reference
   side, straight from the cold path's already-cached stem file, no
   re-decoding. Before this stage existed, `align`+`timbre` each ran their
   own identical MFCC extraction and `dynamics`+`breath` each ran their
   own identical RMS extraction -- the same `librosa` call, twice, for
   four stages that only ever wanted two results. Writes both sides'
   arrays to one `.npz` in `work_dir` and returns only its path in
   `StageResult.data` (`dsp/features.py`) -- the arrays themselves never
   enter `stages_json` (spec 7.3 bans dense per-frame data in JSONB), the
   same file-handoff pattern A1 already uses for its canonical WAV. A3,
   A5-A9 read this cache instead of touching `librosa` directly;
   recomputing a representation the cache already has is a review blocker
   (spec 6.20).
7. **`align`** (A3, M1, spec 6.7, ADR-0017, ADR-0033) -- two banded DTW
   passes (`dsp/dtw.py`), replacing `dtw-python`: its Sakoe-Chiba window
   only masked a full `n x m` cost matrix, so memory scaled with the
   *product* of both sequence lengths regardless of the window (an NFR-16
   violation). This own implementation stores only the band itself
   (`O(n * band)`), as a `numba.njit` kernel (NFR-17).

   **ADR-0033: aligns on pitch contour, not MFCC.** Before this ADR, both
   DTW passes ran on A2's cached MFCC -- a timbre/spectral-envelope
   representation, the same one `timbre` uses to judge "does this voice
   sound similar." That made alignment sensitive to *who* is singing, not
   just *what*, so two people singing the same melody of the same song
   could fail to align outright if their voices differed enough (the real
   failure that motivated this change: repeated genuine attempts against
   a user's own reference raised a structural `ALIGNMENT_FAILED`, not a
   cost-ceiling one). This stage now extracts the user's own F0 curve
   itself -- mode-aware, the same way `pitch`/`melody` used to
   (`dsp/pitch_detection.py::detect_gated` in `clean`,
   `dsp/melody.py::extract_melody` in `mixed`) -- and reads the
   reference's curve directly off `context.reference_pitch` (cold path
   P4 output, already cached). Each `hz` value is embedded as a 2-D point
   on the unit circle, one full turn per octave
   (`dsp/pitch_embedding.py::embed_pitch_curve`,
   `theta = 2*pi * frac(log2(hz / PITCH_FMIN_HZ))`, `(cos theta, sin
   theta)`): octave errors and natural octave differences between voices
   no longer look like a large distance to the DTW cost function, an
   unvoiced frame embeds to the circle's center `(0, 0)` (constant `1.0`
   distance to any voiced point, `0.0` between two unvoiced frames, both
   for free from plain Euclidean distance -- no special-cased branch in
   the kernel), and the whole distance range is a small, fixed `[0, 2]`
   (unlike MFCC's open-ended scale). `_banded_dtw_kernel`/`banded_dtw`/
   `refine_center`/`locate_start_offset_scores` are all dimension-agnostic
   and needed zero changes -- only the input arrays changed shape, from
   `(n, 13)` MFCC to `(n, 2)` pitch embeddings. Raises `NO_VOICE_DETECTED`/
   `MELODY_EXTRACTION_FAILED` here now (moved up from `pitch`/`melody`) if
   fewer than `MIN_VOICED_FRACTION = 5%` of the recording's frames are
   voiced -- alignment on pitch is exactly as unreliable as scoring on it
   would have been without enough voice to embed, so failing before
   attempting a DTW pass on mostly-silence is strictly earlier and more
   honest than the previous order.

   **Level 1 (coarse)**: pitch is only ever extracted at
   `PITCH_HOP_SECONDS` (10ms) -- there is no separate coarse extraction
   the way MFCC had one (A2's 50ms hop). The fine embedding is downsampled
   by striding every `round(FEATURES_HOP_SECONDS / PITCH_HOP_SECONDS)`
   (= 5) frames instead, banded around the literal diagonal, radius
   `ALIGN_WINDOW_SECONDS = 10.0` -- deliberately not scaled by the two
   sequences' length ratio, so a *content* mismatch at comparable lengths
   still makes the target unreachable (the rejection spec 6.8's risk
   table and T9 depend on). When the two lengths themselves differ by
   more than that same band (ADR-0030: a take cut short, or one that ran
   past the song's own end), `_crop_to_overlap` crops whichever side is
   longer down to *exactly* the shorter side's length first -- not
   shorter-plus-band, since both `banded_dtw` passes always force their
   last frame to match the other side's last frame, and cropping with the
   extra band's worth of slack would force the shorter side to be
   stretched unnaturally across it. Recording and reference are then
   scored on that shared overlap instead of failing outright, and the
   stage records `length_mismatch: true` in its own `StageResult.data` --
   `AggregateStage` turns that into a confidence step-down and a
   `LENGTH_MISMATCH_PARTIAL_ANALYSIS` warning (spec 6.15/6.18), same shape
   as every other confidence signal, not a failure.

   ADR-0032: `_crop_to_overlap` still assumes both signals *start*
   together, which a reference that opens with an instrumental intro
   (sung over by a recording that only starts once the user starts
   singing) breaks outright. When the direct (offset 0) attempt fails
   either way -- unreachable within the band, or reachable but over
   `ALIGN_PITCH_MAX_NORMALIZED_DISTANCE` -- `_find_reference_start_offset`
   retries: a cheap, unwarped scan (`dsp/dtw.py::locate_start_offset_scores`,
   deliberately not DTW, `O(n * ALIGN_MAX_START_OFFSET_SECONDS)`, not
   `O(n * m)`, to stay within NFR-16) proposes a few candidate reference
   start frames, and the *same* two-level pipeline re-runs against the
   best one that passes the same ceiling. Found offsets beyond
   `ALIGN_MAX_START_OFFSET_SECONDS = 60.0` are not searched for at all --
   an explicit bound, not a calibrated one. Success records
   `reference_start_offset_seconds` in `StageResult.data` (`0.0` when
   untouched) and, when non-zero, `AggregateStage` turns it into a
   confidence step-down and a `REFERENCE_START_OFFSET_DETECTED` warning,
   the same shape as `LENGTH_MISMATCH_PARTIAL_ANALYSIS`. **Level 2
   (refine)** projects that coarse path through a `TimeMap` onto
   `PITCH_HOP_SECONDS` (10ms) resolution -- already the fine embedding's
   own native hop, so no extra extraction happens here either -- and runs
   a second banded pass centered on *that* projection, radius
   `ALIGN_REFINE_WINDOW_SECONDS = 0.2` -- a small, fixed-width correction,
   still bounded regardless of track length. The stage's final
   `index1`/`index2`/`hop_seconds` (10ms) come from level 2;
   `coarse_normalized_distance` (level 1's own cost) is kept in
   `StageResult.data` for observability only. `user_pitch_curve` (the same
   `PitchCurve`-shaped payload `pitch`/`melody` used to produce
   themselves) is also written into `StageResult.data`, so extraction
   moving here changes nothing about what those stages, or anything
   downstream, can read.

   Raises `ALIGNMENT_FAILED` if the final normalized cost exceeds
   `ALIGN_PITCH_MAX_NORMALIZED_DISTANCE = 0.45` (bounded `[0, 2]` by the
   embedding itself; ADR-0033's own comment in `constants.py` records
   real measurements against synthetic fixtures -- legitimate variation
   such as an octave shift or off-pitch singing stayed under 0.1, a
   genuinely different melody at comparable length measured 0.55-0.81 --
   still not calibrated against real singing, spec 19), and also if
   either banded pass finds the (already length-compatible, post-crop)
   target unreachable within its band at all -- once ADR-0032's offset
   search has also failed to find a reference start frame that works,
   meaning content genuinely diverged, not just length or start position
   -- or the upfront `DTW_MAX_CELLS` cell-count guard rejects the request
   outright (`ALIGNMENT_TOO_LARGE`) -- all non-retryable, like any other
   alignment failure.

   A `TimeMap` built from one hop is not indexed into directly by a
   different-hop signal; every stage below converts through *time*
   (`frame_index * hop_seconds`), never reuses a raw frame index across
   two signals with different hops. A test in `test_pitch_stage.py` exists
   specifically because an earlier draft got this wrong, and
   `dsp/dtw.py::refine_center`'s own tail-extrapolation fix (found by the
   `docs/PERFORMANCE.md` benchmark on a real song) is the same class of
   bug one level up: a coarse frame's nominal timestamp and a fine frame's
   don't cover exactly the same duration, and naively clamping instead of
   extrapolating past the coarse path's own range collapsed the last
   ~40 frames of a real track onto one center value.
8. **`pitch`** (A4) -- ADR-0033: scoring only. The user's F0 curve is
   extracted by `align` (A3) now, not here -- align needs it first, to
   align on melody rather than MFCC -- so this stage just reads
   `context.result("align").data["user_pitch_curve"]` back instead of
   re-running the same detector a second time (`PITCH_HOP_SECONDS = 0.01`,
   same range, same `PITCH_ENGINE`, same VAD gate as before the move). The
   voiced-fraction floor (`NO_VOICE_DETECTED`, `MIN_VOICED_FRACTION = 5%`)
   moved with the extraction, to A3, for the same reason -- see A3 above.
   Reads `context.reference_pitch` directly -- the cold path's P4 output,
   always already populated by the time an analysis's warm path can run
   at all -- rather than computing or caching anything reference-side
   itself; pre-M2, this same stage also computed (and, via the job
   handler, cached) the reference curve on a song's first analysis.

   Deviation is cents (`1200 * log2(user_hz / reference_hz)`) at each user
   frame, looked up against the reference curve through A3's `TimeMap`
   (`_align_and_compare`); this stage's own 0-100 score is
   `100 * (1 - min(1, mean_abs_cents / PITCH_SCORE_CENTS_FOR_ZERO))` with
   `PITCH_SCORE_CENTS_FOR_ZERO = 100` (one semitone of average deviation
   maps to 0), averaged only over frames where both sides are voiced.

   The same per-frame lookup also builds the FR-31 piano-roll payload
   (`PianoRollData`, persisted into `analyses.pitch_curve_json`): the
   reference curve *resampled onto the user's own time grid* (so the two
   curves line up frame-for-frame despite the user's different
   tempo/timing, rather than two curves on unrelated timelines), the
   signed cents deviation per frame, and that deviation already
   thresholded against `PIANO_ROLL_OFF_PITCH_CENTS = 50` into an
   `off_pitch` boolean array -- the client colors a note by reading this
   flag, never by re-deriving cents math in TypeScript (spec 12.1 DRY).
9. **`rhythm`** (A5) -- reads A2's cached onset timestamps for both
   signals (M1: previously its own `librosa.onset.onset_detect` call);
   each reference onset is mapped through the `TimeMap` to an expected
   user time, compared to the nearest actual user onset.
   `RHYTHM_ONSET_TOLERANCE_MS = 200`: the score decays linearly from 100
   at 0ms offset to 0 at or past this tolerance. A onset paired with its
   nearest neighbor is not the same as "on time" -- `onsets_within_tolerance`
   in the stage's data is the count actually inside the tolerance window;
   `mean_abs_offset_ms` (which drives the score) always includes every
   paired onset's true offset, however large.
10. **`vibrato`** (A6) -- for each contiguous voiced run of the pitch
    curve at least `VIBRATO_MIN_SEGMENT_SECONDS = 0.3` long, converts to
    cents relative to the run's median, autocorrelates the detrended
    series, and looks for a peak in the `VIBRATO_MIN_RATE_HZ..VIBRATO_MAX_RATE_HZ
    = 3.5..9.0` band with normalized autocorrelation above
    `VIBRATO_AUTOCORR_PEAK_THRESHOLD = 0.3` and depth (5th-95th percentile
    spread) above `VIBRATO_MIN_DEPTH_CENTS = 20`. Runs are weighted by
    length and averaged into one rate/depth per signal. Scoring: both
    signals vibrato-free scores 100; presence mismatch (one has it, one
    doesn't) scores `VIBRATO_PRESENCE_MISMATCH_SCORE = 40`; both present
    scores down from 100 by rate and depth error relative to
    `VIBRATO_RATE_TOLERANCE_HZ = 2.0` / `VIBRATO_DEPTH_TOLERANCE_CENTS = 50`.
    Reads the user pitch curve A4 already computed and `context.reference_pitch`
    directly (M2: no longer a second copy carried through A4's own
    `StageResult`, since it is already a first-class context field) -- no
    `features`/audio dependency of its own.
11. **`dynamics`** (A7) -- reads A2's cached RMS envelope
    (`FEATURES_HOP_SECONDS = 0.05`, M1: previously its own
    `librosa.feature.rms` call) for both signals, the reference envelope
    resampled onto the user's time grid via the `TimeMap`, Pearson
    correlation between the two. Score is `100 * max(0, correlation)` -- a
    negative correlation scores 0 rather than going negative.
12. **`timbre`** (A8) -- reads A2's cached MFCC (M1: previously its own
    `librosa.feature.mfcc` call, at the same coefficient count and hop
    `align` already wanted independently -- the exact duplication the
    shared cache exists to remove), cosine similarity at each
    `TimeMap`-aligned pair, averaged. Score is `100 * max(0, mean cosine
    similarity)`. Per spec 6.3.9, this is a rough "how similar does it
    sound" indicator, not a diagnosis of vocal technique -- this stage only
    produces the honest number; A11's report is what carries the
    mandatory disclaimer to the user.
13. **`breath`** (A9) -- reuses A2's cached RMS envelope (M1: same source
    `dynamics` now reads, previously each ran its own `librosa.feature.rms`
    pass); a run at least `BREATH_MIN_PAUSE_SECONDS = 0.2` long and
    quieter than `BREATH_SILENCE_RELATIVE_DB = -35` dB relative to the
    track's own peak counts as a pause. Each reference pause is mapped
    through the `TimeMap`; if a user pause center falls within
    `BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5` of the expected time, it
    counts as matched. Score is `100 * matched / reference_pause_count`
    (100 if the reference has no pauses to match against at all).
14. **`recording_condition`** (A10, spec 2.3, 6.16, reworked M3) -- a soft,
    non-blocking classifier for accompaniment in the user's own recording,
    which (per ADR-0003/spec 2.3) is never run through Demucs, so there is
    no real source separation to lean on here. Reuses stage `"pitch"`'s
    per-frame voiced/unvoiced classification (whichever of `PitchStage`/
    `MelodyPitchStage` actually ran, spec 12.3 -- this stage runs
    identically in both modes) plus A2's cached fine RMS envelope:
    `accompaniment_level = median(RMS of unvoiced frames) / median(RMS of
    voiced frames)` (spec 6.16). Below `RECORDING_CONDITION_MIN_UNVOICED_FRAMES
    = 10` unvoiced frames the ratio is reported as `0` rather than computed
    from a statistically meaningless sample (a single pitch-detector edge
    artifact was enough to false-positive a genuinely clean tone during
    testing). `accompaniment_detected` is set once the level reaches
    `ACCOMPANIMENT_DETECT_THRESHOLD = 0.15` (config, spec 20.5); reconciled
    against the declared mode into `effective_mode` and a warning
    (`ACCOMPANIMENT_IN_CLEAN_MODE` / `MODE_DOWNGRADED_TO_CLEAN`, FR-29/
    FR-30) -- see "Mixed mode (M3)" below for what this reconciliation does
    and, importantly, does not do. Never fails the analysis or changes any
    score by itself; A11 reads the result to add a report warning and feed
    the confidence model (spec 6.15).
15. **`aggregate`** (A11, spec 6.3.11, 6.4, 6.14, 6.15, FR-32, reworked M3)
    -- reads exactly `MODE_ASPECTS[context.mode]`'s aspect stages' own
    `score` values (never all six regardless of mode, never recomputes
    them; `scoring/weights.py`), substitutes `key_normalization`'s
    `adjusted_score` for `"pitch"` when a shift was applied (spec 6.8), and
    weighted-sums them into `overall_score` via that mode's own
    `SCORING_WEIGHTS_CLEAN`/`SCORING_WEIGHTS_MIXED` profile
    (`weighted_overall_score`), rounded to one decimal. Also computes the
    confidence model (`scoring/confidence.py`, spec 6.15) from
    `recording_condition`/`pitch`/`align`/`key_normalization`'s already-
    computed signals, and `unavailable_aspects_for(mode)` (FR-41: the
    aspects this mode never scores, each with a machine-readable reason,
    never a bare `0`). `pipeline/report.py` builds the FR-32 text report
    from the *same* stage data, in `context.locale` (ADR-0031: "en" or
    "uk", the caller's own choice at `POST /analyses`, fixed for this
    analysis at creation the same way `mode` is) -- which outcome applies
    (tier, matched-pause count, ...) is decided once regardless of locale,
    only the final phrase-template lookup differs per language: one
    summary line naming the lowest-scoring *available* aspect as the
    suggested focus, one paragraph per available
    aspect in spec 6.4 order, each grounded in that aspect's own numbers
    rather than generic advice, then one block per unavailable aspect
    explaining why (spec 6.19) -- never just silently missing. Feedback is
    tiered by score against `FEEDBACK_EXCELLENT_THRESHOLD = 90` /
    `FEEDBACK_GOOD_THRESHOLD = 75` / `FEEDBACK_FAIR_THRESHOLD = 50`. The
    timbre paragraph always includes spec 6.3.9's mandatory disclaimer,
    both when it reads well and when it doesn't (and is entirely absent, as
    an unavailable-aspect block, in `mixed`). The job handler persists
    `overall_score`/`feedback_text`/`scoring_version` in one write
    (`AnalysisRepository.save_scoring_result`) once every stage's result
    is already in `stages_json`, then upserts the same `overall_score` into
    `progress_snapshots` (`record_progress_snapshot`, E5, FR-35) -- keyed
    on `analysis_id` so a job that fails and later succeeds on retry
    updates its one chart point instead of duplicating it. `weights_profile`/
    `effective_mode`/`confidence`/`aspect_confidence`/`warnings`/
    `unavailable_aspects`/`key_shift_semitones`/`accompaniment_level`/
    `voiced_ratio`/`alignment_cost` are denormalized into their own
    `analyses` columns in the same write (migration 00011, M4), not just
    left inside `stages_json["aggregate"]` -- the Go API reads them
    straight off the row for `GET /analyses/{id}` (spec 8.4) rather than
    parsing the worker's internal stage JSON. `mode`/`allow_transposition`
    flow the other way: the Go API writes them at `POST /analyses` (FR-27,
    FR-31), and `AnalysisRecord.mode`/`allow_transposition` (read back by
    `PostgresAnalysisRepository.get_by_id`) is what the job handler builds
    each analysis's `AnalysisContext` from -- no more hardcoded `clean`
    default in the handler. `progress_snapshots.mode`/`confidence` are
    written by the same `record_progress_snapshot` call, so the FR-49
    progress chart can tell a `clean` point from a `mixed` one without a
    second query.

## Mixed mode (M3, spec 6.6, 6.8, 6.14-6.16)

`mixed` runs the same warm-path stage list as `clean`, but
`PipelineRunner.run(mode=...)` (spec 12.3) filters out any stage whose
`modes` excludes it *before* the run starts (`worker.py::build_stages`
builds one static list covering both modes). Three stages differ:

- **`pitch` via `MelodyPitchStage`** (`pipeline/stages/melody.py`,
  `modes={"mixed"}`) instead of `PitchStage` (`modes={"clean"}`) -- both
  write to the *same* stage name, so `key_normalization`, the aspect
  stages, `aggregate`, and the job handler's score persistence never know
  or care which one ran (ADR-0027). Both are scoring-only (ADR-0033): the
  F0 curve itself comes from `align` (A3), which in `mixed` extracts it via
  `dsp/melody.py::extract_melody` -- harmonic-summation salience over the
  mixture's own STFT, with a rolling per-candidate background subtraction
  that tells a moving melody line apart from a held accompaniment note --
  not the ONNX model spec 6.6 originally named (ADR-0025 has the go
  decision and the measured accuracy, `tests/test_melody_extraction.py`,
  T4).
- **`timbre`/`breath`** (`modes={"clean"}`) do not run in `mixed` at all --
  structurally unavailable (FR-41's `null`), not merely unreliable.
- **`key_normalization`** (`pipeline/stages/key_normalization.py`, spec
  6.8) runs in *both* modes, but only ever applies a shift if
  `context.mode == "mixed"` or the user opted into `allow_transposition`
  in `clean`, and only if the measured median shift is large enough
  (`KEY_SHIFT_MIN_SEMITONES`), stable enough (`KEY_SHIFT_MAX_IQR`), and
  in range (`MAX_KEY_SHIFT_SEMITONES`) -- see `tests/
  test_key_normalization_stage.py` (T1-T3) for the guard conditions
  directly. Reads `"pitch"`'s already-computed `piano_roll.deviation_cents`
  rather than re-detecting or re-aligning anything.

**`recording_condition` (A3, spec 6.16) does not gate which of the above
ran.** It runs *after* them (unchanged position from the table above) and
reports `effective_mode`/warnings as a diagnostic, confidence-affecting
signal alongside whatever this run already computed under its *declared*
mode -- not a retroactive "redo this with the other stage set" decision.
A `mixed`-declared analysis A3 finds is actually a cappella still reports
`mixed_v1`'s four aspects; it additionally reports `effective_mode: "clean"`
and `MODE_DOWNGRADED_TO_CLEAN`, prompting a retry rather than silently
substituting a different result. This is a real, documented gap from
FR-29's literal "cheaper and more accurate" -- `docs/adr/0026` has the
full reasoning and what would need to change (a two-phase pipeline run) to
close it.

## Caching (spec 6.6, restructured by M2)

`songs.prep_status` (not a single boolean, spec 6.2/10) gates the whole
cold path: the scheduler only ever enqueues P1-P4 for a song whose
`prep_status` isn't already `ready`, so by the time any analysis's warm
path runs, the reference vocal stem, pitch curve, and (optionally)
transcript are guaranteed already cached -- the warm path has no cache
*check* to make at all, only cached data to read.

Neither P3 (`transcribe`) nor P4 (`prep_reference_pitch`) writes its own
cache: both run inside `PipelineRunner`'s spawn-based subprocess
(ADR-0012), and a `SongRepository` holding a live DB connection cannot be
pickled across that boundary to get there (a stage that tried this
crashed the instant a real job reached it, pre-M2). Instead each returns
its cacheable payload in its own `StageResult` (`lyrics` for `transcribe`,
`reference_pitch_curve` for `prep_reference_pitch`), and
`SongPrepJobHandler._persist_ready` -- which runs in the parent process,
after the whole cold path finishes -- reads `prep_stages_json` back out
and writes `songs.vocal_stem_path`, `reference_pitch` +
`reference_pitch_meta` (`bytea` + JSONB sidecar, spec 7.3/ADR-0022, not
JSONB text), `lyrics_json`, `lyrics_available` (`true` only if
`transcribe`'s `StageResult.status` is `done`, not `skipped`), and
`prep_status = 'ready'` in one write. A cold-path run that fails before P4
never reaches this write at all, so a song's cache only ever warms on a
fully successful (or successfully-resumed-after-retry) prep run.

Similarly, on the warm side, `pitch`'s own dense `user_pitch_curve` and
the FR-31 `piano_roll` it carries are written into `analyses.user_pitch`
(`bytea`, spec 7.3) and `analyses.pitch_curve_json` respectively by
`AnalysisJobHandler._persist_scores`, then
`AnalysisRepository.prune_dense_stage_fields` strips those dense fields
back out of `stages_json` once they're durably saved elsewhere --
`stages_json`'s per-stage write exists for mid-run resumability (spec
6.8), not as permanent storage for data spec 7.3 says never belongs in
JSONB.

The separated stem lives in its own `song-stems` Docker volume, not
`audio-tmp`: `audio-tmp` is swept by age (FR-43, <=5 minutes after
processing), but the stem is meant to survive indefinitely (spec 7.2,
"поки існує songs-запис"). The cold path's raw reference upload is
deleted by `SongPrepJobHandler._cleanup` the moment `prep_status` reaches
`ready` (or, on failure, kept for a `POST /songs/{id}/prepare` retry to
reopen) -- the warm path never reads that file at all, at any point, post-M2.

## Resumability (spec 6.8)

`PipelineRunner.run` is shared by both paths (see "Where the code lives"),
and both feed it the same shape of `already_done: dict[str, StageResult]`,
read before the first stage runs; any stage already present there is
skipped, and the context is rebuilt from its stored `data` rather than
recomputed.

- **Warm path**: `already_done` comes from `analyses.stages_json`. A retry
  (`POST /analyses/{id}/retry`, `api/internal/service/analysis/retry.go`)
  clears `current_stage` and `queue_stream_id` but never touches
  `stages_json` -- that's precisely what lets the worker resume from the
  first stage retry didn't already finish, not from zero.
- **Cold path**: `already_done` comes from `songs.prep_stages_json`, the
  same mechanism under a different column, kept separate from
  `stages_json` since the two paths never share one job row. A retry
  (`POST /songs/{id}/prepare`, only reachable while `prep_status = 'failed'`
  -- `SongRepository.RetryPrep`'s conditional `UPDATE ... WHERE
  prep_status = 'failed'`) resets `prep_status` to `pending` and
  re-enqueues onto `songs:prep`, again without touching
  `prep_stages_json`, so a song that died on P3 resumes at P3, not P1.

## Errors and retries (spec 6.8)

| `error_code` | Raised by | Retryable |
|---|---|---|
| `REFERENCE_TOO_QUIET` | P2 (`separate_reference`) | no |
| `NO_VOICE_DETECTED` | A3 (`align`, `clean` only, ADR-0033: moved from A4) | no |
| `MELODY_EXTRACTION_FAILED` | A3 (`align`, `mixed` only, M3 spec 6.6, ADR-0033: moved from A4) | no |
| `ALIGNMENT_FAILED` | A3 (`align`) | no |
| `ALIGNMENT_TOO_LARGE` | A3 (`align`), `DTW_MAX_CELLS` guard (M1, spec 6.7, NFR-16) | no |
| `TIMEOUT` | the runner, on any stage exceeding its budget | yes, up to `MAX_STAGE_RETRIES = 2` |
| `INTERNAL` | any unclassified exception a stage raises | yes, up to `MAX_STAGE_RETRIES = 2` |

A retryable failure gets exponential backoff
(`RETRY_BACKOFF_BASE_SECONDS = 2.0`, so 2s then 4s) between attempts of
*that stage*, inside the same job run. A non-retryable
(`LogicalPipelineError`) failure raises immediately; the caller
(`AnalysisJobHandler` for the warm path, `SongPrepJobHandler` for the cold
one) marks the job `failed` (`analyses.status`/`songs.prep_status`
respectively) and publishes the matching WS event. See ADR-0012 for why a
stage's timeout is enforceable at all (subprocess isolation, not
`signal.alarm`).

**Optional stages (spec 6.3, FR-18, M2).** P3 (`transcribe`) is the one
stage in either path declared `required = False`. Its failure or timeout
never raises `LogicalPipelineError` at all -- `PipelineRunner` catches it
internally and records a `StageResult` with `status = StageStatus.SKIPPED`
instead, logging a warning but letting the cold path continue to P4.
`SongPrepJobHandler._persist_ready` reads that status back out of
`prep_stages_json` to set `songs.lyrics_available`. Every other stage in
both paths is still `required = True` (the default) and behaves exactly as
the table above describes.

## Configuration

All from the same `.env` the Go API reads (spec 20.5):
`PITCH_ENGINE` (`crepe`|`pyin`), `WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE`
(M1, ADR-0021, default `int8`), `DEMUCS_MODEL`, `SCORING_VERSION`,
`SCORING_WEIGHTS_CLEAN`/`SCORING_WEIGHTS_MIXED` (M3, spec 6.14 -- two
profiles, not one; each parsed and checked to sum to 1.0 over exactly that
mode's own `MODE_ASPECTS` at startup, consumed by A11's `AggregateStage`),
`ACCOMPANIMENT_DETECT_THRESHOLD` (M3, spec 6.16, default `0.15`,
`recording_condition`'s detection threshold), `KEY_SHIFT_MIN_SEMITONES`/
`KEY_SHIFT_MAX_IQR`/`MAX_KEY_SHIFT_SEMITONES` (M3, spec 6.8, defaults
`0.6`/`0.5`/`7.0`, `key_normalization`'s guard conditions),
`WORKER_CPU_THREADS` (M1, spec 6.11; `0` autodetects from the container's
cgroup CPU limit, applied to every BLAS env var before numpy/torch is ever
imported -- `runtime/threads.py::configure_worker_threads`, called from
`__main__.py` before `vocalcoach.worker` is), `PIPELINE_PARALLEL_ASPECTS`
(M1, spec 6.10, default `true`). `worker/src/vocalcoach/config.py` fails
fast, listing every problem at once, exactly like `api/internal/config`.

`WHISPER_MODEL` defaults to `base`, not spec 6.2's named `small` (ADR-0014):
real-hardware measurement (a real several-minute song, not a synthetic
fixture) showed `small` landing on top of `TRANSCRIBE_TIMEOUT_SECONDS`
instead of comfortably under it, exactly the risk spec 19's risk table
anticipated and prescribed this same fallback for.

## Known limitations (not yet calibrated)

Every threshold named above with "empirical"/"starting point" language
(`ALIGN_PITCH_MAX_NORMALIZED_DISTANCE`, `MIN_VOCAL_LOUDNESS_LUFS`,
`VIBRATO_*`, `BREATH_*`, `RHYTHM_ONSET_TOLERANCE_MS`,
`PITCH_SCORE_CENTS_FOR_ZERO`, `FEEDBACK_EXCELLENT_THRESHOLD` /
`FEEDBACK_GOOD_THRESHOLD` / `FEEDBACK_FAIR_THRESHOLD`,
`PIANO_ROLL_OFF_PITCH_CENTS`) is a reasonable first value, not a value
tuned against real singing. Spec 19's risk table already anticipates this
("калібрування на golden-фікстурах"): calibration needs real recordings
and is deliberately deferred. `test_timbre_stage.py` documents one concrete
surprise from building this: MFCC cosine similarity is fairly insensitive
to spectral shape once loudness is normalized (spec 6.3.1), so the
"different spectra" test asserts a *relative* comparison rather than an
absolute threshold -- worth knowing before tuning the real timbre score
formula.

The FR-32 report text is generated in English only and does not route
through the web app's i18n key system (spec 12.1, FR-41): it is dynamic
prose built from per-analysis numbers, not static UI copy, so there is no
fixed string to key. Localizing it (e.g. building the same sentences from
Ukrainian templates) is deferred until there is a concrete need.

Spec 6.16's accompaniment classification (A10, `recording_condition`) is a
DSP heuristic, not a real classifier: it only catches accompaniment loud
enough, relative to the vocal, to move the unvoiced/voiced RMS ratio past
`ACCOMPANIMENT_DETECT_THRESHOLD = 0.15`. Quiet background music, or music
that happens to share the vocal's pitch range densely enough to still read
as "voiced" to the pitch/melody stage, will not trip it. This threshold is
exactly the kind of "starting point, not calibrated" value the rest of
this section already flags.

M3's mode reconciliation (FR-29/FR-30) is diagnostic, not stage-selecting
(`docs/adr/0026`): a `mixed`-declared analysis A3 finds is actually a
cappella still pays melody extraction's cost and does not unlock
timbre/breath in the same run, only reports `effective_mode`/a warning
suggesting a `clean` retry. Revisit if this proves common in practice
(spec 19 already names "users always pick `mixed`" as a risk).

`dsp/melody.py`'s known limitation (ADR-0025): a note held perfectly
steady, without vibrato or portamento, for longer than
`MELODY_BACKGROUND_WINDOW_SECONDS = 0.6` gets partly suppressed by its own
recent history, the same as a static accompaniment note would be --
mitigated, not eliminated, by feeding its own voicing ratio into the
confidence model (spec 6.15) rather than reporting an unqualified score.

NFR-01c (mixed warm path, <=150s) is only partially measured: `dsp/melody.py`'s
own cost was measured directly on synthetic audio at several durations (see
`docs/PERFORMANCE.md`), but the full mixed warm path has not yet had a real
end-to-end run the way M1/M2's `clean`-path numbers did (no real `mixed`
test recording -- vocal plus accompaniment -- was available this session).
The estimate in `docs/PERFORMANCE.md` combines measured numbers from both
milestones; treat it as a reasonable estimate; re-measure end-to-end before
this is considered fully validated.
