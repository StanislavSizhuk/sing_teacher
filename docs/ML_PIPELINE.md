# ML Pipeline

Status: reflects stage E3 -- stages 1-10 (spec 6.2). Stage 11 (weighted
aggregation into `overall_score`, the text report, `scoring_version`
stamping) is E4 scope: tech.md section 18 assigns "Агрегація балів,
текстовий звіт, piano-roll" to E4 specifically, and E3's own acceptance
criteria never mention scores or a report -- only that the pipeline
finishes, stages are visible, and retry resumes correctly. This document
covers what E3 actually ships; the aggregation formula (spec 6.4) and
report format land here again when E4 builds them.

## Where the code lives

| Path | Responsibility |
|---|---|
| `worker/src/vocalcoach/pipeline/base.py` | `PipelineStage` contract every stage implements |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage subprocess/timeout, retries, progress persistence (ADR-0012) |
| `worker/src/vocalcoach/pipeline/registry.py` | `ModelRegistry`: lazy Demucs/Whisper/CREPE/pYIN construction behind narrow `Protocol`s |
| `worker/src/vocalcoach/pipeline/stages/` | One file per stage, `preprocess.py` .. `breath.py` |
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

Every stage runs in its own spawned child process (ADR-0012) -- this is
what makes each timeout an enforceable ceiling rather than an advisory one,
and what satisfies spec 6.5's "Demucs and Whisper never resident together"
as a natural consequence rather than a special case. `PipelineRunner`
persists a `StageResult` (spec 6.1: `stage`, `status`, `duration_ms`,
`data`, `error_code`/`error_message`) into `analyses.stages_json` after
every stage, and publishes a WS `stage` event (ADR-0010) before it starts
the next one -- this is what makes progress visible in the UI (spec
18/E3's acceptance criterion) and what a retry resumes from (see
"Resumability" below).

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
   short-circuits on `vocal_stem_processed`; on a cache miss, persists the
   result to `songs.lyrics_json` (`SongRepository.save_lyrics`) before
   returning.
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
   not yet calibrated -- see "Known limitations").

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
   voiced. The reference curve is cached the same way as stages 2/3
   (`songs.reference_pitch_json` + `vocal_stem_processed`, set together in
   one write once this stage computes it fresh -- see "Caching" below).
   Deviation is cents (`1200 * log2(user_hz / reference_hz)`) at each
   voiced pair the stage-4 `TimeMap` aligns; this stage's own 0-100 score
   is `100 * (1 - min(1, mean_abs_cents / PITCH_SCORE_CENTS_FOR_ZERO))`
   with `PITCH_SCORE_CENTS_FOR_ZERO = 100` (one semitone of average
   deviation maps to 0). The user's curve (`PitchCurve`: `hop_seconds` +
   one Hz-or-null per frame, no redundant per-point timestamp) is what
   E4 persists into `analyses.pitch_curve_json` for the piano-roll (FR-31)
   -- the reference curve is *not* duplicated there, since it is already
   cached once per song in `songs.reference_pitch_json`.
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
   vocal technique -- the disclaimer text itself is E4's report-writing
   job, not this stage's; this stage only produces the honest number.
10. **`breath`** -- reuses the stage-8 RMS envelope; a run at least
    `BREATH_MIN_PAUSE_SECONDS = 0.2` long and quieter than
    `BREATH_SILENCE_RELATIVE_DB = -35` dB relative to the track's own
    peak counts as a pause. Each reference pause is mapped through the
    `TimeMap`; if a user pause center falls within
    `BREATH_PAUSE_MATCH_TOLERANCE_SECONDS = 0.5` of the expected time, it
    counts as matched. Score is `100 * matched / reference_pause_count`
    (100 if the reference has no pauses to match against at all).

## Caching (spec 6.6)

`songs.vocal_stem_processed` gates stages 2, 3, and the reference half of
5 together, as one flag -- flipped in a single write
(`SongRepository.mark_vocal_stem_processed`) at the end of stage 5, the
last of the three cached artifacts to complete, alongside
`reference_pitch_json`. Stage 3's `lyrics_json` write happens independently
at the end of stage 3 itself, since it has no ordering dependency on stage
5. This means a crash between stages 2/3 and 5 can leave
`vocal_stem_processed = false` with `lyrics_json` already populated -- that
is expected: the flag's contract (spec 6.6) is specifically "all three
ready," and the next analysis of that song simply redoes stages 2/3 (cheap
relative to running the whole pipeline once, and each is independently
idempotent per spec 6.1's stage contract).

The separated stem lives in its own `song-stems` Docker volume, not
`audio-tmp`: `audio-tmp` is swept by age (FR-43, <=5 minutes after
processing), but the stem is meant to survive indefinitely (spec 7.2,
"поки існує songs-запис").

## Resumability (spec 6.8, 18/E3 acceptance)

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
startup -- `SCORING_WEIGHTS`/`SCORING_VERSION` are read and validated now
but not yet consumed by any stage; stage 11/E4 is what applies them).
`worker/src/vocalcoach/config.py` fails fast, listing every problem at
once, exactly like `api/internal/config`.

## Known limitations (not yet calibrated)

Every threshold named above with "empirical"/"starting point" language
(`ALIGN_MAX_NORMALIZED_DISTANCE`, `MIN_VOCAL_LOUDNESS_LUFS`,
`VIBRATO_*`, `BREATH_*`, `RHYTHM_ONSET_TOLERANCE_MS`,
`PITCH_SCORE_CENTS_FOR_ZERO`) is a reasonable first value, not a value
tuned against real singing. Spec 19's risk table already anticipates this
("калібрування на golden-фікстурах"): calibration needs real recordings
and is deliberately deferred, the same way spec 18 defers scoring
aggregation itself to E4. `test_timbre_stage.py` documents one concrete
surprise from building this: MFCC cosine similarity is fairly insensitive
to spectral shape once loudness is normalized (spec 6.3.1), so the
"different spectra" test asserts a *relative* comparison rather than an
absolute threshold -- worth knowing before tuning the real timbre score
formula in E4.

## What E4 adds

Stage 11 (spec 6.3.11): the weighted sum of the six aspect scores this
document's stages 5-10 already compute (`analyses.pitch_score` /
`rhythm_score` / `vibrato_score` / `breath_score` / `dynamics_score` /
`timbre_score` are all populated by the corresponding stage already) into
`overall_score`, stamped with `scoring_version` so old results stay
reproducible if the weights ever change (spec 6.4). Plus the text report
and the piano-roll UI that reads `analyses.pitch_curve_json` and
`songs.reference_pitch_json` together.
