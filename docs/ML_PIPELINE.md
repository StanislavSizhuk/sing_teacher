# ML Pipeline

Status: reflects stages E4-E5 plus M1 (spec 18): the single-pipeline
performance pass -- a shared feature cache, a VAD gate on pitch detection,
an own banded two-level DTW, parallel aspect stages, `faster-whisper`, and
dense curves stored as `bytea` -- applied to the same v1.0 pipeline (no
cold/warm split yet; that is M2), which now runs 13 stages: the original
eleven spec-6.2 stages plus M1's `features` stage (spec 6.9) and a
recording-condition check (a post-E5 audit fix; spec 6.2's table does not
assign it a stage number of its own). See `docs/PERFORMANCE.md` for
measured before/after numbers and `docs/adr/0015`, `0017`, `0021`, `0022`,
`0023` for the M1 decisions.

## Where the code lives

| Path | Responsibility |
|---|---|
| `worker/src/vocalcoach/pipeline/base.py` | `PipelineStage` contract every stage implements |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage subprocess/timeout, retries, progress persistence (ADR-0012) |
| `worker/src/vocalcoach/pipeline/registry.py` | `ModelRegistry`: lazy Demucs/Whisper/CREPE/pYIN construction behind narrow `Protocol`s |
| `worker/src/vocalcoach/pipeline/stages/` | One file per stage, `preprocess.py` .. `aggregate.py` |
| `worker/src/vocalcoach/pipeline/report.py` | Stage 13's FR-32 per-aspect text report, built from the same stage data |
| `worker/src/vocalcoach/dsp/` | M1: shared feature cache (`features.py`), VAD gate (`vad.py`), banded two-level DTW (`dtw.py`) |
| `worker/src/vocalcoach/runtime/` | M1: explicit BLAS/torch thread configuration (`threads.py`, spec 6.11) |
| `worker/src/vocalcoach/audio/` | Shared DSP helpers: ffmpeg wrapper, loudness, WAV IO, DTW time-mapping |
| `worker/src/vocalcoach/queue/` | Redis Streams consumer, job handler, Redis Pub/Sub event publisher (ADR-0010) |
| `worker/src/vocalcoach/repositories/` | `AnalysisRepository`/`SongRepository` Postgres implementations |
| `worker/src/vocalcoach/worker.py` | Entrypoint: wires config -> repositories -> registry -> stages -> runner -> consumer |

## Stage order (spec 6.2, extended by M1's spec 6.9 feature cache)

| # | Stage | Technology | Timeout | Cached |
|---|---|---|---|---|
| 1 | `preprocess` | `pyloudnorm`, ffmpeg resample | 30s | no |
| 2 | `separate_reference` | Demucs v4 (`htdemucs`, ADR-0003) | 300s | yes, per song |
| 3 | `features` (M1, spec 6.9) | `librosa` MFCC/RMS/onset, once per side | 30s | no |
| 4 | `transcribe` | `faster-whisper` (`WHISPER_MODEL`, ADR-0014, ADR-0021) | 180s | yes, per song |
| 5 | `align` | own two-level banded DTW (ADR-0017) | 60s | no |
| 6 | `pitch` | CREPE (`torchcrepe`) or pYIN (`PITCH_ENGINE`), VAD-gated (ADR-0023) | 180s | reference curve: yes |
| 7-11 | `rhythm`, `vibrato`, `dynamics`, `timbre`, `breath` | read the stage-3 cache + stage-5 time map | 30s each | no |
| 12 | `recording_condition` | stage-3 fine RMS + stage-6 pitch curve (own logic, spec 6.9) | 30s | no |
| 13 | `aggregate` | weighted sum + text report (own logic) | 10s | no |

Every stage runs in its own spawned child process (ADR-0012) -- this is
what makes each timeout an enforceable ceiling rather than an advisory one,
and what satisfies spec 6.5's "Demucs and Whisper never resident together"
as a natural consequence rather than a special case. `PipelineRunner`
persists a `StageResult` (spec 6.1: `stage`, `status`, `duration_ms`,
`data`, `error_code`/`error_message`) into `analyses.stages_json` after
every stage, and publishes a WS `stage` event (ADR-0010) before it starts
the next one -- this is what makes progress visible in the UI and what a
retry resumes from (see "Resumability" below).

**Parallel aspect stages (M1, spec 6.10).** Stages 7-11 depend only on
stage 3/5/6's already-finished output, never on each other, so
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

1. **`preprocess`** -- loudness-normalizes (target `TARGET_LOUDNESS_LUFS =
   -23.0`, ITU-R BS.1770 via `pyloudnorm`) and resamples both the
   recording and the reference to `PIPELINE_SAMPLE_RATE_HZ = 22050`, mono,
   via ffmpeg. Independent of the Go API's own upload-time ffmpeg
   transcode (`api/internal/media.Processor.Transcode`), which only
   sanitizes the container (spec 11.3) -- this is the ML pipeline's own
   resample, for pitch/onset/MFCC analysis, not a security step.
2. **`separate_reference`** -- isolates the reference's vocal stem with
   Demucs (`DEMUCS_MODEL`, default `htdemucs`). The mono signal from stage
   1 is duplicated to stereo before Demucs, since its pretrained models
   expect two channels. Short-circuits to the cached stem file
   (`song-stem-<song_id>.wav` under the `song-stems` volume) when
   `songs.vocal_stem_processed` is already true. Raises
   `REFERENCE_TOO_QUIET` if the separated stem's own measured loudness
   (before its own -23 LUFS normalization) is below `MIN_VOCAL_LOUDNESS_LUFS
   = -50.0` -- the check runs on the isolated stem, not the original
   mixture, since a full-band mixture essentially never reads as "quiet"
   even when the vocal buried in it is.
3. **`features`** (M1, spec 6.9) -- computes each shared representation
   exactly once per side (user recording, reference stem):
   `FEATURES_MFCC_COEFFICIENTS`-coefficient MFCC and an RMS envelope, both
   at `FEATURES_HOP_SECONDS = 0.05`, plus a finer RMS pass at
   `PITCH_HOP_SECONDS = 0.01` and onset timestamps. Before this stage
   existed, `align`+`timbre` each ran their own identical MFCC extraction
   and `dynamics`+`breath` each ran their own identical RMS extraction --
   the same `librosa` call, twice, for four stages that only ever wanted
   two results. Writes both sides' arrays to one `.npz` in `work_dir` and
   returns only its path in `StageResult.data` (`dsp/features.py`) -- the
   arrays themselves never enter `stages_json` (spec 7.3 bans dense
   per-frame data in JSONB), the same file-handoff pattern stage 1 already
   uses for its canonical WAVs. Stages 5, 7-11 read this cache instead of
   touching `librosa` directly; recomputing a representation the cache
   already has is a review blocker (spec 6.20).
4. **`transcribe`** -- `faster-whisper` (`WHISPER_MODEL`,
   `WHISPER_COMPUTE_TYPE=int8`, ADR-0021) transcribes the vocal stem to
   words with per-word timecodes (`Lyrics`/`LyricsWord`) -- the same
   Whisper checkpoints `openai-whisper` used, on CTranslate2's faster CPU
   inference instead. Also short-circuits on `vocal_stem_processed`. Does
   not write `songs.lyrics_json` itself -- it returns `lyrics`/`cached` in
   its `StageResult` and `AnalysisJobHandler._persist_song_cache` writes
   the cache once the whole pipeline finishes, in the parent process (see
   "Caching" below for why).
5. **`align`** (M1, spec 6.7, ADR-0017) -- two banded DTW passes
   (`dsp/dtw.py`), replacing `dtw-python`: its Sakoe-Chiba window only
   masked a full `n x m` cost matrix, so memory scaled with the *product*
   of both sequence lengths regardless of the window (an NFR-16
   violation). This own implementation stores only the band itself
   (`O(n * band)`), as a `numba.njit` kernel (NFR-17).

   **Level 1 (coarse)** runs on stage 3's cached MFCC (50ms hop), banded
   around the literal diagonal, radius `ALIGN_WINDOW_SECONDS = 10.0` --
   deliberately not scaled by the two sequences' length ratio, so a
   length mismatch alone can still make the target unreachable (the
   rejection spec 6.8's risk table and T9 depend on). **Level 2 (refine)**
   projects that coarse path through a `TimeMap` onto `PITCH_HOP_SECONDS`
   (10ms) resolution and runs a second banded pass centered on *that*
   projection, radius `ALIGN_REFINE_WINDOW_SECONDS = 0.2` -- a small,
   fixed-width correction, still bounded regardless of track length. The
   stage's final `index1`/`index2`/`hop_seconds` (now 10ms, not 50ms) come
   from level 2; `coarse_normalized_distance` (level 1's own cost) is kept
   in `StageResult.data` for observability only.

   Raises `ALIGNMENT_FAILED` if the final normalized cost exceeds
   `ALIGN_MAX_NORMALIZED_DISTANCE = 70.0` (recalibrated for this own cost
   function's scale, not carried over from `dtw-python`'s `symmetric2` --
   see ADR-0017; still an empirical starting point, not yet calibrated on
   real recordings), and also if either banded pass finds the target
   unreachable within its band at all (length/tempo diverged too far) or
   the upfront `DTW_MAX_CELLS` cell-count guard rejects the request
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
6. **`pitch`** -- tracks both signals' fundamental frequency at
   `PITCH_HOP_SECONDS = 0.01`, `PITCH_FMIN_HZ..PITCH_FMAX_HZ = 65..1050`
   (C2 to C6). `PITCH_ENGINE=crepe` uses `torchcrepe` (`model="tiny"`,
   trading accuracy for CPU speed -- spec 19's documented fallback if this
   is still too slow on real hardware is to switch to `pyin`);
   `PITCH_ENGINE=pyin` uses `librosa.pyin`.

   **VAD-gated (M1, spec 6.5, ADR-0023).** Per-frame pitch detection was
   the single most expensive warm-path stage measured on real audio
   (`docs/PERFORMANCE.md`: 60% of total wall time before M1). `dsp/vad.py`
   reuses `breath`'s relative-RMS-to-peak silence definition
   (`BREATH_SILENCE_RELATIVE_DB`) against stage 3's cached fine RMS
   envelope to build a voiced-frame mask (a silent run shorter than
   `VAD_MIN_SILENT_RUN_SECONDS = 0.3` is folded back to voiced -- not worth
   gating); the detector then runs once per voiced span instead of once
   over the whole track, with every other frame filled `None` directly. An
   interim, energy-based stand-in for spec 6.6's eventual Silero VAD --
   see ADR-0023 for why, and what changes when that lands.

   Raises `NO_VOICE_DETECTED` if fewer than `MIN_VOICED_FRACTION = 5%` of
   the recording's frames are voiced. The reference curve is cached the
   same way as stage 4: this stage returns
   `reference_pitch_curve`/`reference_cached` in its `StageResult`, and
   `AnalysisJobHandler._persist_song_cache` writes `songs.reference_pitch`
   (`bytea`, spec 7.3/ADR-0022) + flips `vocal_stem_processed` in one write
   once the pipeline finishes (see "Caching" below).
   Deviation is cents (`1200 * log2(user_hz / reference_hz)`) at each user
   frame, looked up against the reference curve through the stage-5
   `TimeMap` (`_align_and_compare`); this stage's own 0-100 score is
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
7. **`rhythm`** -- reads stage 3's cached onset timestamps for both
   signals (M1: previously its own `librosa.onset.onset_detect` call);
   each reference onset is mapped through the `TimeMap` to an expected
   user time, compared to the nearest actual user onset.
   `RHYTHM_ONSET_TOLERANCE_MS = 200`: the score decays linearly from 100
   at 0ms offset to 0 at or past this tolerance. A onset paired with its
   nearest neighbor is not the same as "on time" -- `onsets_within_tolerance`
   in the stage's data is the count actually inside the tolerance window;
   `mean_abs_offset_ms` (which drives the score) always includes every
   paired onset's true offset, however large.
8. **`vibrato`** -- for each contiguous voiced run of the pitch curve at
   least `VIBRATO_MIN_SEGMENT_SECONDS = 0.3` long, converts to cents
   relative to the run's median, autocorrelates the detrended series, and
   looks for a peak in the `VIBRATO_MIN_RATE_HZ..VIBRATO_MAX_RATE_HZ =
   3.5..9.0` band with normalized autocorrelation above
   `VIBRATO_AUTOCORR_PEAK_THRESHOLD = 0.3` and depth (5th-95th percentile
   spread) above `VIBRATO_MIN_DEPTH_CENTS = 20`. Runs are weighted by
   length and averaged into one rate/depth per signal. Scoring: both
   signals vibrato-free scores 100; presence mismatch (one has it, one
   doesn't) scores `VIBRATO_PRESENCE_MISMATCH_SCORE = 40`; both present
   scores down from 100 by rate and depth error relative to
   `VIBRATO_RATE_TOLERANCE_HZ = 2.0` / `VIBRATO_DEPTH_TOLERANCE_CENTS = 50`.
   Reads the pitch curves stage 6 already computed -- no `features`/audio
   dependency of its own.
9. **`dynamics`** -- reads stage 3's cached RMS envelope
   (`FEATURES_HOP_SECONDS = 0.05`, M1: previously its own
   `librosa.feature.rms` call) for both signals, the reference envelope
   resampled onto the user's time grid via the `TimeMap`, Pearson
   correlation between the two. Score is `100 * max(0, correlation)` -- a
   negative correlation scores 0 rather than going negative.
10. **`timbre`** -- reads stage 3's cached MFCC (M1: previously its own
    `librosa.feature.mfcc` call, at the same coefficient count and hop
    `align` already wanted independently -- the exact duplication the
    shared cache exists to remove), cosine similarity at each
    `TimeMap`-aligned pair, averaged. Score is `100 * max(0, mean cosine
    similarity)`. Per spec 6.3.9, this is a rough "how similar does it
    sound" indicator, not a diagnosis of vocal technique -- this stage only
    produces the honest number; stage 13's report is what carries the
    mandatory disclaimer to the user.
11. **`breath`** -- reuses stage 3's cached RMS envelope (M1: same source
    `dynamics` now reads, previously each ran its own `librosa.feature.rms`
    pass); a run at least `BREATH_MIN_PAUSE_SECONDS = 0.2` long and
    quieter than `BREATH_SILENCE_RELATIVE_DB = -35` dB relative to the
    track's own peak counts as a pause. Each reference pause is mapped
    through the `TimeMap`; if a user pause center falls within
    `BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5` of the expected time, it
    counts as matched. Score is `100 * matched / reference_pause_count`
    (100 if the reference has no pauses to match against at all).
12. **`recording_condition`** (spec 2.3, 6.9) -- a soft, non-blocking
    heuristic for likely background-music/instrument contamination in the
    user's own recording, which (per ADR-0003/spec 2.3's a cappella
    assumption) is never run through Demucs, so there is no real source
    separation to lean on here. Reuses stage 6's pitch curve's per-frame
    voiced/unvoiced classification plus stage 3's cached fine RMS envelope
    (same `PITCH_HOP_SECONDS` hop, M1: previously its own `rms_envelope`
    call) over the recording: a frame louder than
    `RECORDING_CONDITION_LOUD_RELATIVE_DB = -20` dB relative to the
    recording's own peak, yet unvoiced, is "loud and unvoiced" --
    energetic but with no single clear pitch, i.e. plausibly an
    instrument rather than a pause/consonant. `background_music_detected`
    is set once that fraction reaches
    `RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION = 0.3`. Never fails the
    analysis or changes any score; stage 13 reads the flag to add one
    warning paragraph to the FR-32 report, nothing else.
13. **`aggregate`** (spec 6.3.11, 6.4, FR-32) -- reads the six aspect
    stages' own `score` values (never recomputes them) and weighted-sums
    them into `overall_score` via `SCORING_WEIGHTS`
    (`ScoringWeights.as_dict()`, so no `getattr`-on-`Any` typing hazard),
    rounded to one decimal. `pipeline/report.py` builds the FR-32 text
    report from the *same* stage data: one summary line naming the
    lowest-scoring aspect as the suggested focus, then one paragraph per
    aspect in `config.ASPECTS` order, each grounded in that aspect's own
    numbers (mean cents, ms offset, matched-pause counts, correlation,
    vibrato rate/depth, ...) rather than generic advice. Feedback is
    tiered by score against `FEEDBACK_EXCELLENT_THRESHOLD = 90` /
    `FEEDBACK_GOOD_THRESHOLD = 75` / `FEEDBACK_FAIR_THRESHOLD = 50`. The
    timbre paragraph always includes spec 6.3.9's mandatory disclaimer,
    both when it reads well and when it doesn't. The job handler persists
    `overall_score`/`feedback_text`/`scoring_version` in one write
    (`AnalysisRepository.save_scoring_result`) once every stage's result
    is already in `stages_json`, then upserts the same `overall_score` into
    `progress_snapshots` (`record_progress_snapshot`, E5, FR-35) -- keyed
    on `analysis_id` so a job that fails and later succeeds on retry
    updates its one chart point instead of duplicating it.

## Caching (spec 6.6)

`songs.vocal_stem_processed` gates stage 2, stage 4 (`transcribe`), and the
reference half of stage 6 (`pitch`) together, as one flag. Neither
`transcribe` nor `pitch` writes its own cache: both run inside
`PipelineRunner`'s spawn-based subprocess (ADR-0012), and a
`SongRepository` holding a live DB connection cannot be pickled across
that boundary to get there (a stage that tried this crashed the instant a
real job reached it). Instead each returns its cacheable payload in its
own `StageResult` (`lyrics`/`cached` for `transcribe`,
`reference_pitch_curve`/`reference_cached` for `pitch`), and
`AnalysisJobHandler._persist_song_cache` -- which runs in the parent
process, after the whole pipeline finishes -- writes `songs.lyrics_json`
(`SongRepository.save_lyrics`) and `reference_pitch` + `reference_pitch_meta`
(`bytea` + JSONB sidecar, spec 7.3/ADR-0022, not JSONB text) +
`vocal_stem_processed` (`mark_vocal_stem_processed`, one write) from
whichever of the two actually ran fresh (skipped when `cached`/
`reference_cached` is already true, so a warm song is never re-written
with the same values). Similarly, `pitch`'s own dense `user_pitch_curve`
and the FR-31 `piano_roll` it also carries are written into
`analyses.user_pitch` (`bytea`, spec 7.3) and `analyses.pitch_curve_json`
respectively by `_persist_scores`, then `AnalysisRepository.prune_dense_stage_fields`
strips all three dense fields back out of `stages_json` once they're
durably saved elsewhere -- `stages_json`'s per-stage write exists for
mid-run resumability (spec 6.8), not as permanent storage for data spec
7.3 says never belongs in JSONB. A run that fails before stage 13 never
reaches this write at all, so a song's cache only ever warms on a fully
successful analysis -- an accepted trade-off given spec NFR-04 (one active
worker, jobs processed strictly in sequence): nothing else is going to
race to reuse a half-finished cache in that window anyway.

The separated stem lives in its own `song-stems` Docker volume, not
`audio-tmp`: `audio-tmp` is swept by age (FR-43, <=5 minutes after
processing), but the stem is meant to survive indefinitely (spec 7.2,
"поки існує songs-запис").

## Resumability (spec 6.8)

`PipelineRunner.run` takes `already_done: dict[str, StageResult]`, read
from `analyses.stages_json` before the first stage runs; any stage already
present there is skipped, and the context is rebuilt from its stored
`data` rather than recomputed. A retry (`POST /analyses/{id}/retry`,
`api/internal/service/analysis/retry.go`) clears `current_stage` and
`queue_stream_id` but never touches `stages_json` -- that's precisely what
lets the worker resume from the first stage retry didn't already finish,
not from zero.

## Errors and retries (spec 6.8)

| `error_code` | Raised by | Retryable |
|---|---|---|
| `REFERENCE_TOO_QUIET` | stage 2 | no |
| `NO_VOICE_DETECTED` | stage 6 | no |
| `ALIGNMENT_FAILED` | stage 5 | no |
| `ALIGNMENT_TOO_LARGE` | stage 5, `DTW_MAX_CELLS` guard (M1, spec 6.7, NFR-16) | no |
| `TIMEOUT` | the runner, on any stage exceeding its budget | yes, up to `MAX_STAGE_RETRIES = 2` |
| `INTERNAL` | any unclassified exception a stage raises | yes, up to `MAX_STAGE_RETRIES = 2` |

A retryable failure gets exponential backoff
(`RETRY_BACKOFF_BASE_SECONDS = 2.0`, so 2s then 4s) between attempts of
*that stage*, inside the same job run. A non-retryable
(`LogicalPipelineError`) failure raises immediately; the caller
(`AnalysisJobHandler`) marks the analysis `failed` and publishes the WS
`failed` event. See ADR-0012 for why a stage's timeout is enforceable at
all (subprocess isolation, not `signal.alarm`).

## Configuration

All from the same `.env` the Go API reads (spec 20.5):
`PITCH_ENGINE` (`crepe`|`pyin`), `WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE`
(M1, ADR-0021, default `int8`), `DEMUCS_MODEL`, `SCORING_VERSION`,
`SCORING_WEIGHTS` (parsed and checked to sum to 1.0 at startup, consumed by
stage 13's `AggregateStage`), `WORKER_CPU_THREADS` (M1, spec 6.11; `0`
autodetects from the container's cgroup CPU limit, applied to every BLAS
env var before numpy/torch is ever imported --
`runtime/threads.py::configure_worker_threads`, called from `__main__.py`
before `vocalcoach.worker` is), `PIPELINE_PARALLEL_ASPECTS` (M1, spec 6.10,
default `true`). `worker/src/vocalcoach/config.py` fails fast, listing
every problem at once, exactly like `api/internal/config`.

`WHISPER_MODEL` defaults to `base`, not spec 6.2's named `small` (ADR-0014):
real-hardware measurement (a real several-minute song, not a synthetic
fixture) showed `small` landing on top of `TRANSCRIBE_TIMEOUT_SECONDS`
instead of comfortably under it, exactly the risk spec 19's risk table
anticipated and prescribed this same fallback for.

## Known limitations (not yet calibrated)

Every threshold named above with "empirical"/"starting point" language
(`ALIGN_MAX_NORMALIZED_DISTANCE`, `MIN_VOCAL_LOUDNESS_LUFS`,
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

Spec 6.9's "significant non-vocal energy in the recording" soft-detection
is a DSP heuristic (stage 12, `recording_condition`), not a real
classifier: it only catches contamination loud enough, and consistently
enough, to dominate the loud-but-unvoiced frame fraction past
`RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION = 0.3`. Quiet background
music, or music that happens to share the vocal's pitch range densely
enough to still read as "voiced" to the pitch detector, will not trip it.
Its two thresholds are exactly the kind of "starting point, not
calibrated" value the rest of this section already flags.
