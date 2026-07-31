# ADR-0021: `faster-whisper` as the transcription runtime

- Status: Accepted
- Date: 2026-07-30

## Context

`transcribe` (spec 6.3.3) used `openai-whisper`'s own PyTorch inference.
Measured on a real ~207s song's isolated vocal stem (`docs/PERFORMANCE.md`
"before" column), `transcribe` with `WHISPER_MODEL=base` took 22.9s of a
64.4s total warm-path run -- 36% of it, second only to pitch detection.
ADR-0014 already found `transcribe` sitting on top of its 180s timeout at
`WHISPER_MODEL=small`; `base` bought margin, but the stage itself was still
running the slowest inference path available for the same model weights.

Spec 5.1/6.6 name `faster-whisper` -- a CTranslate2-backed reimplementation
of Whisper inference, not a different model -- specifically for this: int8
quantization and a native (not PyTorch-autograd) inference graph are
substantially cheaper on CPU at comparable transcription quality, which is
all this stage needs (spec 6.3.3: word-level timecodes feeding `align`'s
DTW map, not user-facing transcript text).

## Decision

Replace `openai-whisper` with `faster-whisper` in
`worker/src/vocalcoach/pipeline/registry.py`'s `WhisperTranscriber`, at
`compute_type=int8` (new `WHISPER_COMPUTE_TYPE` config, defaulting to
`int8`). `WHISPER_MODEL` keeps its ADR-0014 default (`base`) and meaning --
`faster-whisper` loads the same named Whisper checkpoints, converted to
CTranslate2's format; this is a runtime swap, not a model swap, consistent
with ADR-0015's decision to optimize via runtimes rather than a rewrite.

`pyworld` (spec 6.6's other named runtime change, for the *pitch* engine)
is explicitly **out of scope for this ADR and for M1**: M1's acceptance
list (spec 18) names `faster-whisper` but not `pyworld`, and the current
`PITCH_ENGINE=crepe`/`pyin` choice is not what the M1 profiling flagged as
a runtime-swap opportunity (CREPE's cost is addressed by the spec 6.5 VAD
gate instead, in this same milestone). Revisit `pyworld` with its own ADR
if a future measurement calls for it.

## Consequences

- `transcribe` runs on CTranslate2 int8 inference instead of `openai-whisper`'s
  float32 PyTorch path, for the same weights and the same
  `word_timestamps=True` requirement.
- `openai-whisper`, and the `triton`/`tiktoken`/`regex` dependencies it
  pulled transitively, drop out of `worker/pyproject.toml` entirely.
- `faster-whisper`'s API returns segments as a generator plus a separate
  `info` object (language, etc.), not a single dict -- `WhisperTranscriber.transcribe`
  is restructured accordingly; the `Transcriber` protocol and every caller
  are unaffected (spec 12.3: `PipelineRunner`/`align` never depend on which
  library backs a stage, only on `Lyrics`).
- Model weights are CTranslate2-converted checkpoints downloaded from
  Hugging Face on first use into the existing `model-weights` volume/cache
  dir, not `openai-whisper`'s own `.pt` format -- a deployment upgrading in
  place re-downloads once per configured `WHISPER_MODEL`.

## Alternatives considered

- **Keep `openai-whisper`, only lower `WHISPER_MODEL`** -- already the
  extent of ADR-0014's fix; this ADR is the next lever in spec 6.17's
  prescribed order (runtime, after cache, before hardware), not a
  replacement for it.
- **`whisper.cpp` (via a Python binding)** -- rejected: less mature Python
  packaging than `faster-whisper`, and spec 5.1 names `faster-whisper`
  specifically, already vetted against this project's multilingual
  (Ukrainian/English) requirement.
