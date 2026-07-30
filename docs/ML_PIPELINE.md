# ML Pipeline

Status: reflects stages E4-E5 -- all eleven spec-6.2 stages, including
aggregation, the text report, and the piano-roll data (spec 18: "Агрегація
балів, текстовий звіт, piano-roll"), plus a twelfth stage recording a
`progress_snapshots` point (spec 18/E5, FR-35), and an eleventh implementing
spec 6.9's recording-condition check (a post-E5 audit fix; spec 6.2's table
does not assign it a stage number of its own).

## Where the code lives

| Path | Responsibility |
|---|---|
| `worker/src/vocalcoach/pipeline/base.py` | `PipelineStage` contract every stage implements |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage subprocess/timeout, retries, progress persistence (ADR-0012) |
| `worker/src/vocalcoach/pipeline/registry.py` | `ModelRegistry`: lazy Demucs/Whisper/CREPE/pYIN construction behind narrow `Protocol`s |
| `worker/src/vocalcoach/pipeline/stages/` | One file per stage, `preprocess.py` .. `aggregate.py` |
| `worker/src/vocalcoach/pipeline/report.py` | Stage 12's FR-32 per-aspect text report, built from the same stage data |
| `worker/src/vocalcoach/audio/` | Shared DSP helpers: ffmpeg wrapper, loudness, WAV IO, RMS envelope, DTW time-mapping |
| `worker/src/vocalcoach/queue/` | Redis Streams consumer, job handler, Redis Pub/Sub event publisher (ADR-0010) |
| `worker/src/vocalcoach/repositories/` | `AnalysisRepository`/`SongRepository` Postgres implementations |
| `worker/src/vocalcoach/worker.py` | Entrypoint: wires config -> repositories -> registry -> stages -> runner -> consumer |

## Stage order (spec 6.2)

| # | Stage | Technology | Timeout | Cached |
|---|---|---|---|---|
| 1 | `preprocess` | `pyloudnorm`, ffmpeg resample | 30s | no |
| 2 | `separate_reference` | Demucs v4 (`htdemucs`, ADR-0003) | 300s | yes, per song |
| 3 | `transcribe` | Whisper (`WHISPER_MODEL`) | 180s | yes, per song |
| 4 | `align` | `dtw-python` (ADR-0004) | 60s | no |
| 5 | `pitch` | CREPE (`torchcrepe`) or pYIN (`PITCH_ENGINE`) | 180s | reference curve: yes |
| 6 | `rhythm` | `librosa.onset` + the stage-4 time map | 30s | no |
| 7 | `vibrato` | autocorrelation on the stage-5 pitch curve | 30s | no |
| 8 | `dynamics` | `librosa.feature.rms` + the stage-4 time map | 30s | no |
| 9 | `timbre` | `librosa.feature.mfcc` + the stage-4 time map | 30s | no |
| 10 | `breath` | RMS-envelope silence detection | 30s | no |
| 11 | `recording_condition` | RMS envelope + stage-5 pitch curve (own logic, spec 6.9) | 30s | no |
| 12 | `aggregate` | weighted sum + text report (own logic) | 10s | no |

Every stage runs in its own spawned child process (ADR-0012) -- this is
what makes each timeout an enforceable ceiling rather than an advisory one,
and what satisfies spec 6.5's "Demucs and Whisper never resident together"
as a natural consequence rather than a special case. `PipelineRunner`
persists a `StageResult` (spec 6.1: `stage`, `status`, `duration_ms`,
`data`, `error_code`/`error_message`) into `analyses.stages_json` after
every stage, and publishes a WS `stage` event (ADR-0010) before it starts
the next one -- this is what makes progress visible in the UI and what a
retry resumes from (see "Resumability" below).

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
3. **`transcribe`** -- Whisper (`WHISPER_MODEL`) transcribes the vocal
   stem to words with per-word timecodes (`Lyrics`/`LyricsWord`). Also
   short-circuits on `vocal_stem_processed`. Does not write
   `songs.lyrics_json` itself -- it returns `lyrics`/`cached` in its
   `StageResult` and `AnalysisJobHandler._persist_song_cache` writes the
   cache once the whole pipeline finishes, in the parent process (see
   "Caching" below for why).
4. **`align`** -- 13-coefficient MFCC frames (`ALIGN_MFCC_COEFFICIENTS`,
   `ALIGN_HOP_SECONDS = 0.05`) for the recording and the reference stem,
   windowed DTW (`dtw-python`, `symmetric2` step pattern, Sakoe-Chiba
   window of `ALIGN_WINDOW_SECONDS = 10.0`, per ADR-0004's alignment
   choice and spec 19's risk table on bounding the warping window).
   Produces the warping path (`index1`/`index2`, parallel frame-index
   arrays) every later stage turns into a `TimeMap`
   (`audio/timemap.py`) to compare the two signals at corresponding
   moments despite the user's different tempo/timing. Raises
   `ALIGNMENT_FAILED` if the normalized DTW distance exceeds
   `ALIGN_MAX_NORMALIZED_DISTANCE = 40.0` (an empirical starting point,
   not yet calibrated -- see "Known limitations"), and also if `dtw-python`
   itself raises first: a length difference between the two signals alone
   can exceed what the Sakoe-Chiba window can bridge, which the library
   reports as a bare `ValueError` ("no warping path found") before a
   distance is ever computed -- caught and reclassified the same way, so
   it is non-retryable like any other alignment failure rather than an
   opaque, retried `INTERNAL` error.

   A `TimeMap` built from a coarse (50ms) alignment hop is not indexed
   into directly by a finer-hop signal (pitch runs at 10ms); every stage
   below converts through *time* (`frame_index * hop_seconds`), never
   reuses a raw frame index across two signals with different hops. A
   test in `test_pitch_stage.py` exists specifically because an earlier
   draft got this wrong.
5. **`pitch`** -- tracks both signals' fundamental frequency at
   `PITCH_HOP_SECONDS = 0.01`, `PITCH_FMIN_HZ..PITCH_FMAX_HZ = 65..1050`
   (C2 to C6). `PITCH_ENGINE=crepe` uses `torchcrepe` (`model="tiny"`,
   trading accuracy for CPU speed -- spec 19's documented fallback if this
   is still too slow on real hardware is to switch to `pyin`);
   `PITCH_ENGINE=pyin` uses `librosa.pyin`. Raises `NO_VOICE_DETECTED` if
   fewer than `MIN_VOICED_FRACTION = 5%` of the recording's frames are
   voiced. The reference curve is cached the same way as stage 3: this
   stage returns `reference_pitch_curve`/`reference_cached` in its
   `StageResult`, and `AnalysisJobHandler._persist_song_cache` writes
   `songs.reference_pitch_json` + flips `vocal_stem_processed` in one
   write once the pipeline finishes (see "Caching" below).
   Deviation is cents (`1200 * log2(user_hz / reference_hz)`) at each user
   frame, looked up against the reference curve through the stage-4
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
6. **`rhythm`** -- `librosa.onset.onset_detect` on both signals; each
   reference onset is mapped through the `TimeMap` to an expected user
   time, compared to the nearest actual user onset.
   `RHYTHM_ONSET_TOLERANCE_MS = 200`: the score decays linearly from 100
   at 0ms offset to 0 at or past this tolerance. A onset paired with its
   nearest neighbor is not the same as "on time" -- `onsets_within_tolerance`
   in the stage's data is the count actually inside the tolerance window;
   `mean_abs_offset_ms` (which drives the score) always includes every
   paired onset's true offset, however large.
7. **`vibrato`** -- for each contiguous voiced run of the pitch curve at
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
8. **`dynamics`** -- `librosa.feature.rms` at `ENVELOPE_HOP_SECONDS =
   0.05` for both signals, the reference envelope resampled onto the
   user's time grid via the `TimeMap`, Pearson correlation between the
   two. Score is `100 * max(0, correlation)` -- a negative correlation
   scores 0 rather than going negative.
9. **`timbre`** -- 13-coefficient MFCC (`TIMBRE_MFCC_COEFFICIENTS`) per
   frame, cosine similarity at each `TimeMap`-aligned pair, averaged.
   Score is `100 * max(0, mean cosine similarity)`. Per spec 6.3.9, this
   is a rough "how similar does it sound" indicator, not a diagnosis of
   vocal technique -- this stage only produces the honest number; stage
   12's report is what carries the mandatory disclaimer to the user.
10. **`breath`** -- reuses the stage-8 RMS envelope; a run at least
    `BREATH_MIN_PAUSE_SECONDS = 0.2` long and quieter than
    `BREATH_SILENCE_RELATIVE_DB = -35` dB relative to the track's own
    peak counts as a pause. Each reference pause is mapped through the
    `TimeMap`; if a user pause center falls within
    `BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5` of the expected time, it
    counts as matched. Score is `100 * matched / reference_pause_count`
    (100 if the reference has no pauses to match against at all).
11. **`recording_condition`** (spec 2.3, 6.9) -- a soft, non-blocking
    heuristic for likely background-music/instrument contamination in the
    user's own recording, which (per ADR-0003/spec 2.3's a cappella
    assumption) is never run through Demucs, so there is no real source
    separation to lean on here. Reuses the stage-5 pitch curve's per-frame
    voiced/unvoiced classification plus a fresh RMS envelope
    (`rms_envelope`, same `PITCH_HOP_SECONDS` hop) over the recording: a
    frame louder than `RECORDING_CONDITION_LOUD_RELATIVE_DB = -20` dB
    relative to the recording's own peak, yet unvoiced, is "loud and
    unvoiced" -- energetic but with no single clear pitch, i.e. plausibly an
    instrument rather than a pause/consonant. `background_music_detected`
    is set once that fraction reaches
    `RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION = 0.3`. Never fails the
    analysis or changes any score; stage 12 reads the flag to add one
    warning paragraph to the FR-32 report, nothing else.
12. **`aggregate`** (spec 6.3.11, 6.4, FR-32) -- reads the six aspect
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

`songs.vocal_stem_processed` gates stages 2, 3, and the reference half of
5 together, as one flag. Neither stage 3 nor stage 5 writes its own cache:
both run inside `PipelineRunner`'s spawn-based subprocess (ADR-0012), and
a `SongRepository` holding a live DB connection cannot be pickled across
that boundary to get there (a stage that tried this crashed the instant a
real job reached it). Instead each returns its cacheable payload in its
own `StageResult` (`lyrics`/`cached` for stage 3,
`reference_pitch_curve`/`reference_cached` for stage 5), and
`AnalysisJobHandler._persist_song_cache` -- which runs in the parent
process, after the whole pipeline finishes -- writes `songs.lyrics_json`
(`SongRepository.save_lyrics`) and `reference_pitch_json` +
`vocal_stem_processed` (`mark_vocal_stem_processed`, one write) from
whichever of the two actually ran fresh (skipped when `cached`/
`reference_cached` is already true, so a warm song is never re-written
with the same values). A run that fails before stage 12 never reaches
this write at all, so a song's cache only ever warms on a fully
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
| `NO_VOICE_DETECTED` | stage 5 | no |
| `ALIGNMENT_FAILED` | stage 4 | no |
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
`PITCH_ENGINE` (`crepe`|`pyin`), `WHISPER_MODEL`, `DEMUCS_MODEL`,
`SCORING_VERSION`, `SCORING_WEIGHTS` (parsed and checked to sum to 1.0 at
startup, consumed by stage 12's `AggregateStage`).
`worker/src/vocalcoach/config.py` fails fast, listing every problem at
once, exactly like `api/internal/config`.

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
is a DSP heuristic (stage 11, `recording_condition`), not a real
classifier: it only catches contamination loud enough, and consistently
enough, to dominate the loud-but-unvoiced frame fraction past
`RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION = 0.3`. Quiet background
music, or music that happens to share the vocal's pitch range densely
enough to still read as "voiced" to the pitch detector, will not trip it.
Its two thresholds are exactly the kind of "starting point, not
calibrated" value the rest of this section already flags.
