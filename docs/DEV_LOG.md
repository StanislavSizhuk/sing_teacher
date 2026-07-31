# Dev Log

Weekly status for the tech lead: done / next / blockers / risks, ~10 lines
(spec 14.4).

## 2026-07-27 -- E1

**Done:** Auth (email+password with 6-digit verification, Google
OAuth2/PKCE), full DB schema (users/songs/analyses/progress_snapshots) via
goose migrations embedded in the binary, Go API skeleton
(domain/service/repository/transport), Docker Compose for prod + dev, CI
(lint/test/security/commit-lint/build, no CD yet), 5 required ADRs + one for
the Go dependency choices made this stage.

**Next:** E2 -- song upload/YouTube import, browser recording, job queue with
live position (Redis Streams).

**Blockers:** none.

**Risks:** Google OAuth needs real `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
to test end-to-end (not yet registered with Google); email+password path is
fully tested without it. No production VPS targeted yet -- deploy automation
is E6.

## 2026-07-28 -- E2

**Done:** Song upload + YouTube import (shared sniff/probe/transcode/dedup
pipeline), Redis Streams analysis queue with Postgres-computed FIFO
position (ADR-0008), `429` on queue-full/rate-limit, WebSocket position
channel, cancel-while-queued, retry-when-failed (FR-26, unit-tested,
unreachable until E3). go-api runtime moved to Alpine for ffmpeg/yt-dlp
(ADR-0007). `web/` SPA now covers all of it: auth, add-a-song,
MediaRecorder recording, live queue status. CI gained web lint/test/
security/build jobs.

**Next:** E3 -- ML pipeline, which closes retry's remaining gap (a worker
that can actually produce a `failed` job) and lets audio deletion switch
from the interim age-based sweep to real processing completion.

**Blockers:** none.

**Risks:** `web/` isn't wired into Caddy/compose for production -- still a
Vite dev server pointed at go-api directly; that's deploy/CD work. Google
sign-in has no UI yet. YouTube import untested against the real service (no
live network calls in tests, by design) -- worth a manual smoke test.

## 2026-07-28 -- E3

**Done:** Python ML worker, 10 stages (Demucs, Whisper, DTW align, pitch/
rhythm/vibrato/dynamics/timbre/breath), each its own child process for a
real per-stage timeout and Demucs/Whisper never resident together
(ADR-0012). Redis Streams consumer with reclaim/give-up (spec 10.1),
Postgres repositories, Redis Pub/Sub relay into `go-api`'s WS channel
(ADR-0010). Retry (FR-26) reachable end-to-end for the first time. 68
Python tests + 8 new Go tests, all green; CI gained worker jobs. 3 new
ADRs, `docs/ML_PIPELINE.md`.

**Next:** E4 -- stage 11 (weighted `overall_score`, `scoring_version`), the
text report, piano-roll UI.

**Blockers:** none.

**Risks:** every empirical threshold (alignment ceiling, vibrato/breath
detection, quiet-reference floor) is a starting value, not one calibrated
against real singing (spec 19 already expects this). Never run against
real Demucs/Whisper inference end-to-end here (weight-download cost);
tests fake those two stages by design (spec 15.2) -- worth a manual smoke
test on real hardware before E4.

## 2026-07-29 -- E4

**Done:** Stage 11 (`aggregate`) weighted-sums the six aspect scores into
`overall_score` and builds the FR-32 text report from the same stage
data, both persisted alongside `scoring_version`. Pitch stage now also
resamples the reference curve onto the user's time grid and precomputes
per-frame cents deviation + an off-pitch flag (FR-31). `go-api` exposes
all of it (aspect scores, report, piano-roll) on the existing analysis
endpoints. `web/` shows the score breakdown and a canvas piano-roll whose
cursor tracks the recording's own playback (FR-33), played back from the
client-side Blob since the server deletes the file within minutes
(FR-43). New worker/Go/web tests all green; CI unchanged (still no CD).

**Next:** E5 -- history with pagination (FR-34), the progress-over-time
chart (FR-35), and the adaptive/mobile UI + WCAG AA pass.

**Blockers:** none.

**Risks:** the feedback-tier thresholds and the off-pitch cents threshold
are starting values, uncalibrated against real singers, same caveat as
E3's own thresholds. The report text is English-only and does not route
through the i18n system yet (FR-41) -- deferred, see
`docs/ML_PIPELINE.md`. Spec 6.9's non-vocal-energy warning still has no
owning stage.

## 2026-07-29 -- E5

**Done:** Worker upserts a `progress_snapshots` row (unique on
`analysis_id`) right after stage 11's `overall_score`, so a retry updates
the point instead of duplicating it. `go-api` exposes it read-only via
`GET /progress`. `web/` gained a Progress screen -- stat tiles, an
accessible SVG line chart (`role="img"` + a visible session table as its
real data source), and a top-level Analyze/Progress nav
(`SegmentedControl`, extracted from two prior duplicated radiogroups) plus
a skip-to-content link. New worker/Go/web tests all green; CI unchanged
(still no CD).

**Next:** E6 -- load testing, security review, production deploy with
rollback. FR-34 (paginated `GET /analyses` history) is still open; folding
it into E6 or a follow-up PR needs a call from the tech lead.

**Blockers:** none.

**Risks:** the Progress screen's session table stands in for FR-34's
history view but isn't one -- no song title, no pagination, capped at
`progressPointsCap` (1000) points server-side. Never manually verified in
a browser this session (worked CI-only, no dev server per this stage's
instructions) -- worth a manual smoke test before calling E5 fully done.

## 2026-07-29 -- E6

**Done:** Load testing (`api/cmd/loadtest`, real HTTP against the dev
stack -- 20 accepted, the rest correctly `429 QUEUE_FULL`, server healthy
throughout, `docs/LOAD_TESTING.md`), which caught a real bug: the queue's
admission check could overshoot `QUEUE_MAX_LENGTH` under a genuine
concurrent burst (`Length()` then `Enqueue()` as two separate Redis
calls). Fixed with an atomic Redis `EVAL`
(`internal/queue.Producer.EnqueueIfUnderLimit`), proven against a real
Redis instance under concurrent load, not just an in-process fake.
Full spec section 11 review, evidence-checked line by line
(`docs/SECURITY.md`); found and closed one real gap (VPS hardening --
UFW/SSH/fail2ban/Cloudflare -- had no written procedure anywhere, now in
`docs/RUNBOOK.md`). `deploy/deploy.sh`: checkout -> build+up -> poll
healthcheck -> automatic rollback on failure (spec 16.2), rehearsed
locally (success path and a forced failure, both correct) since this
project has no provisioned VPS or staging server. Also fixed
`docker-compose.dev.yml`: it silently required manually-set
`GOOGLE_CLIENT_ID`/`SECRET` despite docs claiming otherwise.

## 2026-07-30 -- post-E6 audit

**Done:** Full tech.md compliance pass plus a real end-to-end analysis run
(never actually exercised before this), which found the ML pipeline could
not complete one: `worker/Dockerfile`'s dev stage never installed its own
package (missing `.dockerignore` let a stale host `.venv` overwrite the
image's); `separate_reference`'s stem-path closure and `transcribe`'s/
`pitch`'s injected `SongRepository` cannot survive `PipelineRunner`'s
spawn-based subprocess pickling (moved the song-cache write to
`AnalysisJobHandler`); `DemucsSeparator` passed Demucs' `repo=`, which
disables its own download fallback entirely; a failed-but-retryable job's
`work_dir` was deleted unconditionally, so retry always crashed re-opening
files that were already gone; a Redis `NOGROUP` response crashed the whole
consumer instead of recreating the group; `dtw-python`'s bare `ValueError`
on an unbridgeable length gap fell through as a retried `INTERNAL` instead
of `ALIGNMENT_FAILED`. Also: spec 6.9's recording-condition heuristic
(previously unimplemented), per-stage timer/index/duration surfaced
end-to-end (migration -> worker -> `GET /analyses/{id}` -> `QueueStatus`),
YouTube import verified for real (works once `FEATURE_YOUTUBE_IMPORT` is
on -- now defaulted on in the dev compose stack only), and a borderless
disabled-button contrast fix (`Button.tsx`).

**Next:** FR-34 (paginated `GET /analyses` history) is still the one open
functional requirement from E2/E5.

**Blockers:** none.

**Risks:** None of the above had a golden-fixture regression test before
this pass (spec 15.1's ML regression tier is still not built), so nothing
caught nine real bugs in a pipeline that "passed" every existing unit
test. The recording-condition heuristic's two thresholds are unvalidated
starting points, same caveat as every other scoring constant (spec 19).

**Next:** FR-34 (paginated `GET /analyses` history) is still the one open
functional requirement from E2/E5; otherwise the spec's stage list ends
at E6 -- remaining work is whatever the tech lead prioritizes from the
"Known limitations" entries accumulated across CHANGELOG.md.

**Blockers:** none.

**Risks:** Caddy's CSP/security headers don't yet cover a
production-served `web/` (still not wired into `Caddy`/compose, tracked
since E2) -- out of scope for this CI-only stage, but worth closing before
a real public launch. `deploy/deploy.sh` has never run against a real VPS
or a genuinely different git tag under production traffic, only a local
rehearsal with a deliberately-broken migration; the first real production
deploy is still the actual first test of the full sequence end to end.

## 2026-07-30 -- transcribe TIMEOUT investigation

**Done:** Reported bug ("Error: TIMEOUT" analyzing a song) reproduced with
two real files (SadSvit -- "Небо", the original, as reference; a fan cover
by Августа as the recording) through `deploy/docker-compose.dev.yml`.
Worker logs showed `transcribe` (Whisper `small`, CPU, `word_timestamps=
True`) landing right on top of its 180s budget -- 5 of 7 real attempts hit
`TIMEOUT` outright, the one clean success measured 176.7s. This is exactly
spec 19's risk table's own anticipated risk ("Demucs/Whisper на CPU
повільніші за цільові 3 хв"), never actually measured on real hardware
before (E3's own acceptance criterion) because no prior session ran a real
song through `transcribe` enough times to notice it sits on the edge
rather than under it. Applied the risk table's own first prescribed
fallback: `WHISPER_MODEL` now defaults to `base` (ADR-0014), re-measured
clean at 143.5s on the same song -- real margin instead of a coin flip.
Separately, the same song pair reaches `ALIGNMENT_FAILED` once `transcribe`
completes -- a fan cover genuinely diverges in tempo/arrangement from the
original past DTW's bounded window, spec 6.8's correct classification, not
a bug.

**Next:** No functional work queued from this; FR-34 remains the one open
item from E2/E5.

**Blockers:** none.

**Risks:** `base`'s 143.5s measurement is one data point on one dev
machine with no CPU limit set (12 vCPUs visible, torch defaulting to 6
threads) against one ~225s song -- not yet validated against the
production VPS's actual 4 vCPU target (spec 5.1/NFR-04), which has never
been provisioned. If real margin there turns out smaller, spec 19's
remaining fallback (transcribe only the chorus) is still on the table
(ADR-0014, alternatives considered).

## 2026-07-30 -- M1: single-pipeline performance pass

**Done:** All seven M1 items (spec 18) landed on `perf/m1-pipeline-performance`,
one commit per change: explicit `WORKER_CPU_THREADS`/BLAS thread config
(spec 6.11), a shared MFCC/RMS/onset feature cache removing align+timbre's
and dynamics+breath's duplicate `librosa` calls (spec 6.9), an energy-based
VAD gate on pitch detection (spec 6.5, interim -- ADR-0023), an own
two-level banded DTW replacing `dtw-python` (spec 6.7, ADR-0017; caught and
fixed a real tail-extrapolation bug in the coarse-to-fine projection along
the way), parallel execution of the five independent aspect stages (spec
6.10), `faster-whisper` replacing `openai-whisper` (ADR-0021), and dense
pitch curves stored as `bytea` instead of JSONB, including pruning them
back out of `stages_json` once durably saved (spec 7.3, ADR-0022). ADR-0015
records the standing decision this milestone is built on: optimize via
runtimes/algorithms, not a Rust/C rewrite.

Measured before/after on a real ~207s song (not a synthetic fixture, per
spec 18's acceptance criterion), `docs/PERFORMANCE.md`: total warm-path
wall time 64.4s -> 44.2s (**-31%**). Biggest single contributors:
`transcribe` -51% (`faster-whisper`), `pitch` -23% (VAD gate), and the five
aspect stages combined -93% (shared cache: 985ms -> 70ms). T8/T9/T13
(spec 15.2) all green: banded-memory DTW on a 20k-frame synthetic sequence,
honest `ALIGNMENT_FAILED`/`ALIGNMENT_TOO_LARGE` on incompatible/oversized
input, and parallel-vs-sequential aspect execution scoring identically.
109 worker unit tests + 9 integration tests (against a real migrated
Postgres/Redis) green; Go unit + integration tests green; no existing test
broken.

**Next:** M2 (cold/warm path split, spec 6.2/10) is next per spec 18's
fixed ordering. `pyworld` (named alongside `faster-whisper` in spec 6.6)
and Silero VAD (spec 6.5/6.6) are both explicitly deferred past M1 --
ADR-0015 and ADR-0023 record why and when to revisit.

**Blockers:** none.

**Risks:** Measured on the same 12 vCPU/31 GB dev machine as the
ADR-0014 investigation, not yet the 4 vCPU/8 GB production VPS target
(still not provisioned, spec 16.3) -- the before/after *comparison* is
valid (same machine, same conditions), the absolute numbers are not yet
validated against the real target hardware. `ALIGN_MAX_NORMALIZED_DISTANCE`'s
new value (70.0, recalibrated for the own DTW's cost scale) is an empirical
starting point against this repo's synthetic test fixtures, same
calibration caveat as every other scoring threshold (spec 19) -- worth
revisiting once golden fixtures exist.
