# ADR-0025: Harmonic-salience DSP melody extraction, not an ONNX model; go decision for M3

- Status: Superseded by ADR-0034
- Date: 2026-07-31
- 2026-08-02: `extract_melody` and `dsp/melody.py` are deleted by ADR-0034 --
  `mixed` mode now separates the recording with Demucs, the same as the
  reference, instead of extracting F0 from the raw mixture. The DSP-vs-ONNX
  question this ADR answered no longer applies to A4; kept here unedited as
  the historical record of that decision and the spike that justified M3's
  go call.

## Context

Spec 6.6 gates the `mixed` analysis mode (spec 2.3, M3, TECH.md v2.0) on a
spike: extract F0 from a polyphonic mixture on synthetic fixtures (known
vocal F0 plus overlaid accompaniment at SNR 0 dB and -6 dB) and measure
median F0 error on vocal frames. Below 50 cents median error is a go;
above it, spec 18's post-M3 decision point requires narrowing `mixed` to a
single aspect (rhythm) or moving it out of MVP, recorded as its own ADR.

Spec 6.6 names an ONNX melody-extraction model as the target
implementation (stage A4), the same way it originally named Silero VAD for
A2. ADR-0023 already worked through the equivalent question for VAD and
chose a DSP heuristic instead of a new ONNX dependency for M1, on the
reasoning that a real ONNX runtime and model-weights infrastructure were
not yet justified by that milestone alone. For melody extraction the
infrastructure question is sharper, not softer: spec 11.3 requires every
model's weights be pinned by a fixed checksum verified at worker startup.
Silero VAD is a small, widely-used, easily vendored model that would have
satisfied that requirement trivially. No comparably standard, freely
licensable, checksummable melody-extraction ONNX model exists to vendor the
same way -- unlike VAD, this is not "not yet built," it is "nothing to
point the infrastructure at."

## Decision

Implement A4 as a classical harmonic-summation salience extractor
(`worker/src/vocalcoach/dsp/melody.py`), built entirely from `numpy`/
`scipy` (both already dependencies), with one addition beyond textbook
harmonic summation: per-candidate rolling-window background subtraction.
Measured directly (see below), plain frame-wise harmonic salience picks
whichever source is louder, vocal or accompaniment, regardless of harmonic
fit -- below 0 dB SNR against a harmonically related chord (a singer
performing in key, the realistic case, not an unrelated interval) it locks
onto the accompaniment outright. Subtracting each candidate's own median
salience over the trailing 0.6s exploits the one structural difference
between the two sources that loudness alone does not capture: an
accompaniment note sits at a fixed pitch for as long as it rings, so its
candidate column stays loud across many consecutive frames, while a sung
melody moves continuously (vibrato, portamento) and so does not.

**Measured** (`worker/tests/test_melody_extraction.py`, T4, spec 15.2):
median F0 error 5-24 cents across several melody/chord/register scenarios
at both SNR 0 dB and -6 dB -- comfortably under the 50-cent threshold, with
roughly 2x margin even in the hardest scenario tried. **Go.** M3 proceeds
to A3 (spec 6.16 classification/reconciliation), A4 (this module, wired as
a pipeline stage), A8 (key normalization, spec 6.8), the `clean_v1`/
`mixed_v1` weight profiles and confidence model (spec 6.14/6.15).

## Consequences

- No new runtime dependency, no model-weights volume entry, no checksum
  step (spec 11.3) for A4 -- the same "cheap now, real cost deferred"
  shape as ADR-0023's VAD decision, except here there is no known point in
  time this gets swapped for a vendored model, since none exists to swap
  to. A future ONNX model, if one becomes available and licensable, would
  replace `extract_melody`'s implementation behind the same
  `(mixture, sample_rate_hz, hop_seconds) -> list[float | None]` signature
  without touching the A4 stage or anything downstream of it.
- Known, named limitation (spec G7 -- confidence must not overstate
  accuracy): a note held perfectly steady, without vibrato or portamento,
  for longer than the 0.6s background window is partly suppressed by its
  own recent history, the same as a static accompaniment note would be.
  This is a real accuracy risk for singers with very still pitch, not just
  a theoretical edge case. Mitigation: A4's own voicing ratio feeds spec
  6.15's confidence model (`aspect_confidence` for pitch/vibrato drops when
  it runs low), so a recording this affects reports lower confidence
  rather than a wrong number presented as certain.
- The candidate grid (~964 F0 candidates x 6 harmonics) is a real memory
  and CPU cost that scales with recording length; `dsp/melody.py` chunks
  frames (`MELODY_CHUNK_FRAMES`) to bound peak memory the same way the
  banded DTW bounds its own working set (NFR-16's principle applied here,
  not NFR-16 itself, which is specifically about DTW). NFR-01c (mixed warm
  path, <=150s) still needs measuring on realistic recording lengths, not
  just the few-second fixtures this spike used -- tracked as the
  remaining M3 work, not settled by this ADR.
- The fixture in `test_melody_extraction.py` is deliberately not the
  easiest case (melody notes diatonic to the accompaniment's chords, i.e.
  harmonically confusable on purpose) so that test staying green means
  something under future changes to `dsp/melody.py`'s constants.

## Alternatives considered

- **Vendor a pretrained ONNX melody-extraction model** (e.g. an
  open-source Melodia/multi-pitch-streaming re-implementation exported to
  ONNX) -- rejected for this milestone: no such model is both freely
  licensable and small enough to vendor with the same confidence as Silero
  VAD, and spec 11.3's checksum requirement assumes a specific, fixed set
  of weights to point it at, not a build-it-yourself export pipeline.
  Revisit if/when such a model becomes available; the DSP implementation's
  interface is designed to be swapped without downstream changes.
- **Frame-wise argmax on raw harmonic salience, no background
  subtraction** -- rejected: measured to pick the louder source outright
  below 0 dB SNR against a harmonically related chord, failing the spike
  criterion in exactly the case that matters (a singer performing in key).
- **Full Melodia-style multi-pitch-streaming + melody selection** (contour
  creation, salience/duration/pitch-continuity based selection among
  competing contours) -- rejected as disproportionate to what the spike
  needed to answer: the simpler background-subtraction addition already
  clears the 50-cent bar with margin; the added complexity of a full
  contour-tracking system is not justified without evidence the simpler
  approach falls short.
- **Narrow `mixed` to rhythm-only now, skip the spike** -- rejected: spec
  18 explicitly requires running the spike before making that call: "Обіцяти
  якість наперед ТЗ не має права" applies in both directions, refusing to
  try the cheap option first is the same mistake as promising it would work.
