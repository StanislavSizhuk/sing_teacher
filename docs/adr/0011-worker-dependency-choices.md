# ADR-0011: Worker dependency choices for the E3 pipeline

- Status: Accepted
- Date: 2026-07-28

## Context

Spec 6 names the ML libraries (`librosa`, Demucs, CREPE, Whisper,
`dtw-python`) but not the Python dependency manager, nor how to reconcile
CREPE's usual TensorFlow dependency with Demucs and Whisper both already
requiring PyTorch. Getting either wrong is expensive to unwind later: a
dependency-manager switch touches every `Dockerfile`/CI step, and a second
deep-learning framework doubles the image size and the memory-management
surface spec 6.5 already treats as the worker's tightest constraint.

## Decision

- **`uv`** manages `worker/`'s dependencies and virtual environment
  (`pyproject.toml` + `uv.lock`, mirroring `go.sum`/`package-lock.json`'s
  role for the other two services). `uv sync --frozen` in CI and the
  Dockerfile installs the exact locked graph; `uv run` runs tools against it.
- **CREPE via `torchcrepe`**, not the reference `crepe` package (which pulls
  in TensorFlow). `torchcrepe` reuses the PyTorch install Demucs and Whisper
  already require, so the worker never has two deep-learning frameworks
  resident, and `PITCH_ENGINE=crepe` costs nothing extra in image size
  beyond `torchcrepe`'s own (small) footprint.
- **CPU-only PyTorch**, pulled from `https://download.pytorch.org/whl/cpu`
  via a `uv` explicit index (`[tool.uv.sources]`), not the default PyPI
  wheel (which bundles CUDA). The worker is a single CPU-only replica (spec
  5.1, NFR-04); the CUDA wheel is several times larger for a capability
  never used.

## Consequences

One lockfile pins the full dependency graph, including transitive ones
(spec 12.1: reproducible builds), and `uv`'s resolver is fast enough that CI
re-resolution is not a bottleneck. Reusing PyTorch for pitch detection keeps
spec 6.5's "Demucs and Whisper never resident together" constraint scoped to
exactly two models instead of three, and per-stage subprocess isolation
(ADR-0012) already reclaims each one's memory regardless of framework. The
CPU wheel means a future GPU worker (out of scope per spec 2.2, but not
impossible someday) would need this ADR revisited.

## Alternatives considered

- Poetry or plain `pip` + `requirements.txt` -- rejected: Poetry's resolver
  is markedly slower on a dependency graph this size (PyTorch alone pulls in
  dozens of transitive packages), and plain `pip`/`requirements.txt` has no
  equivalent of a cross-platform lockfile with hashes.
- Reference `crepe` (TensorFlow) -- rejected: a second deep-learning
  framework alongside PyTorch roughly doubles the image's ML-library weight
  for a capability (a different pitch estimator) `torchcrepe` already
  provides on the framework already required.
- Default PyPI PyTorch wheel (CUDA-bundled) -- rejected: multiple GB larger
  for a hardware path (`NFR-04`'s single CPU worker) this deployment never
  exercises.
