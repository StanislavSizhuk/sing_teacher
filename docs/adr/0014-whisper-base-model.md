# ADR-0014: Default `WHISPER_MODEL` to `base`, not `small`

- Status: Accepted
- Date: 2026-07-30

## Context

Spec 6.2's stage table names Whisper `small` for stage 3 (`transcribe`)
with a 180s timeout, and spec 19's risk table separately flags, as a
*high-probability* risk, that Demucs/Whisper on CPU may be slower than the
target wall time -- with "Whisper `base` instead of `small`" listed first
among its own prescribed reactions, contingent on "measure on real
hardware" (spec 18's E3 acceptance criterion).

That measurement had never actually happened (see the post-E6 audit,
`docs/DEV_LOG.md` 2026-07-30): every prior pipeline run either failed
before reaching `transcribe` or used short synthetic fixtures, not a real
song. Running a real analysis end to end today (reference: SadSvit --
"Небо", 225s; recording: a fan cover, 231s) through
`deploy/docker-compose.dev.yml`, on this machine's 12 vCPUs (no CPU limit
set in either compose file -- only `SEPARATE_REFERENCE`'s 6GB memory
ceiling is enforced), `transcribe` with Whisper `small` and
`word_timestamps=True` (required for spec 6.3.3's per-word timecodes) took:

| Attempt | Outcome | Duration |
|---|---|---|
| 1 | `TIMEOUT` | >180s |
| 2 | `TIMEOUT` | >180s |
| 3 | `TIMEOUT` | >180s |
| 4 | `TIMEOUT` | >180s |
| 5 | `TIMEOUT` | >180s |
| 6 | done, but the *next* job then failed `ALIGNMENT_FAILED` | 176.7s |
| 7 | `TIMEOUT` | >180s |

`separate_reference` (Demucs) stayed well inside its 300s budget throughout
(92-129s), so this is specific to `transcribe`. `small` on CPU is landing
right on top of its 180s ceiling -- close enough that ordinary run-to-run
variance (thermal, other containers, retry backoff not affecting this) is
the difference between a pass and a `TIMEOUT`, not a one-off fluke. Every
job a user submits with a real several-minute song fails or barely
survives; this is the reported bug ("Error: TIMEOUT").

## Decision

Change `WHISPER_MODEL`'s default from `small` to `base`
(`worker/src/vocalcoach/config.py`, `.env`, `.env.example`) -- exactly the
first reaction spec 19's risk table already prescribes for this
measured outcome, not a new deviation invented for this fix. `base` is
still the multilingual (non-`.en`) checkpoint spec 6.3.3's Ukrainian/English
requirement needs, at roughly a third of `small`'s parameter count.

Re-running the same real reference/recording pair after the change, with
`base`'s weights already cached (no one-time download inflating the
number, unlike the first post-change run which still finished in 172s):
`transcribe` took **143.5s**, ~33s/19% faster than `small`'s one clean
(non-`TIMEOUT`) data point of 176.7s -- real margin under the 180s ceiling
instead of sitting on top of it. The rest of the pipeline still reaches
`ALIGNMENT_FAILED` for this specific pair (a fan cover genuinely diverges
in tempo/arrangement from the original beyond DTW's bounded window) -- a
separate, correctly-classified outcome (spec 6.8), not a symptom of this bug.

## Consequences

- `transcribe` has real margin under its spec-mandated 180s budget instead
  of sitting on top of it (143.5s measured vs. 176.7-186s+ before), so
  `TIMEOUT` stops being the default outcome for an ordinary song.
- Word/segment transcription accuracy is somewhat lower than `small`'s.
  Spec 6.3.3 only uses the transcript for word-level timecodes feeding
  `align`'s DTW map, not user-facing text, so this is an acceptable
  trade -- the same reasoning already applied to `PITCH_ENGINE`'s
  `CREPE_MODEL_CAPACITY = "tiny"` (`constants.py`).
  `docs/ML_PIPELINE.md`'s calibration caveat (spec 19: everything here is a
  starting point, not tuned against golden fixtures) applies here too.
- `WHISPER_MODEL` stays operator config (spec 12.1); a deployment that has
  CPU to spare can still set it back to `small` or up to `medium` in its
  own `.env` without a code change.

## Alternatives considered

- **Raise `TRANSCRIBE_TIMEOUT_SECONDS` past 180s** -- rejected: spec 6.2
  fixes this value in the stage table; widening it also eats directly into
  spec 18's E3 acceptance criterion ("a 3-minute song analyzes in under 3
  minutes"), which a slower stage 3 works directly against.
- **Drop `word_timestamps=True`** -- rejected: spec 6.3.3 requires
  per-word timecodes for `align`'s DTW map; without them stage 4 has
  nothing to align against.
- **Transcribe only the chorus** -- spec 19's other prescribed option.
  Deferred: it needs a way to locate the chorus before transcription runs
  (the thing `transcribe` itself would otherwise help locate), which is
  more design than this fix warrants; revisit if `base` turns out not to
  be enough margin on the production VPS's real (not yet provisioned)
  hardware.
- **Pin worker CPU count up instead of shrinking the model** -- rejected:
  spec 5.1/NFR-04 target 4 vCPU total for the whole stack, and this
  dev machine already gives the worker all 12 cores with no limit; more
  CPU is not a lever available on the target VPS.
