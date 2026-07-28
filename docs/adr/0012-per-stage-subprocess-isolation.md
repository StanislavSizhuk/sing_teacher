# ADR-0012: Every pipeline stage runs in its own child process

- Status: Accepted
- Date: 2026-07-28

## Context

Spec 6.1 gives every stage its own timeout as a first-class property, and
spec 6.5 requires Demucs and Whisper to never be resident in memory
together, explicitly calling for stages 2 and 3 to run "in a separate child
process -- guaranteed return of memory to the OS after completion." Two
different problems, both needing the same tool: Python cannot forcibly
interrupt a blocked native call (a stuck BLAS/torch kernel) from inside the
same process a timeout is supposed to bound -- only killing the process
from outside does that reliably. And a model's memory is only *guaranteed*
gone, not just eligible for GC, once the process holding it exits.

## Decision

`PipelineRunner` runs *every* stage (not just 2 and 3) in its own freshly
spawned (`multiprocessing.get_context("spawn")`) child process, with a hard
`process.join(timeout)` / `terminate()` / `kill()` escalation on timeout.
The stage object and `AnalysisContext` are pickled into the child; the
`StageResult` it returns is pickled back. A stage's own model dependency
(`VocalSeparator`/`Transcriber`/`PitchDetector`, from `ModelRegistry`) still
calls `release()` at the end of its own `run()` for explicit hygiene and a
log line marking when the memory *should* drop, even though process exit
makes that unconditional.

`spawn`, not `fork`: forking a process that has already imported `torch`
can deadlock (a fork only copies the calling thread, but torch's native
extensions may hold locks other threads owned at fork time) -- a
well-documented hazard with PyTorch specifically, not a hypothetical one.

## Consequences

Every stage's timeout is real, not advisory, uniformly, and adding a new
stage never has to decide "does this one need process isolation" -- it
already has it, for free, from `PipelineRunner`'s Open/Closed stage list
(spec 12.3). Spec 6.5's Demucs/Whisper-never-together requirement is
satisfied as a natural consequence of isolating every stage, not a special
case for stages 2 and 3. The cost is per-stage process startup overhead
(spawn re-imports the interpreter and every heavy library fresh each time,
on the order of a few seconds) -- acceptable against stage timeouts of
30-300s (spec 6.2), and against the alternative of a timeout that cannot
actually fire.

This is also the seam that makes stages independently unit-testable on
synthetic signals (spec 15.2) without a shared runner harness: a test
constructs a stage directly and calls `.run(context)` in-process, since the
process boundary is `PipelineRunner`'s concern, not the stage's own.

## Alternatives considered

- Child process only for stages 2 and 3, as spec 6.5's literal wording
  suggests -- rejected: leaves every other stage's timeout unenforceable
  against a genuinely stuck native call, which is exactly the failure mode
  a timeout exists to bound. Uniform isolation costs the same code path for
  every stage instead of a special case for two of them.
- `signal.alarm`/`SIGALRM` for the timeout, keeping the process shared --
  rejected: a signal is only delivered when the interpreter next checks for
  one, which does not happen inside a blocked C-extension call (BLAS,
  torch's native kernels) -- it cannot actually preempt the failure mode
  that matters most.
- `fork` instead of `spawn` -- rejected: known deadlock hazard once torch
  is already imported in the parent, and `ModelRegistry`'s whole point is
  that a model *will* be imported during the worker's lifetime.
