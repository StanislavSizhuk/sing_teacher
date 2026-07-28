# CLAUDE.md

Working rules for this repository. Extracted from `tech.md`
(sections 5, 7, 11, 12, 13, 14, 15). The spec wins on any conflict; section
numbers are given so the source can be checked.

---

## Project

AI Vocal Coach — a web app that compares a user's singing to the original
vocal and reports pitch, rhythm, vibrato, breathing, dynamics and timbre.
Analysis is offline (not real-time). The whole stack runs on a single VPS
via `docker compose up`.

Stack: Go (API) + Python (ML worker) + React/TS (web) + PostgreSQL + Redis + Caddy.

Key assumption: the user sings a cappella, in headphones. Only the reference
track goes through source separation (spec 2.3).

---

## Non-negotiable principles

1. **Security first** — when security conflicts with convenience, security wins.
2. **DRY** — a rule that exists in two places is a bug waiting to happen.
3. **OOP** — behaviour lives in objects with one responsibility; dependencies
   are injected through constructors and typed as interfaces/abstractions.

Violating any of these is grounds to reject a change.

---

## Layout

| Path | Responsibility |
|---|---|
| `api/` | Go: REST + WebSocket, auth, queue producer |
| `api/internal/domain/` | entities and interfaces; knows nothing about HTTP, SQL, Redis |
| `api/internal/service/` | business logic; depends on interfaces only |
| `api/internal/repository/` | Postgres implementations |
| `api/internal/transport/` | handlers, middleware, DTOs, WS hub |
| `api/migrations/` | goose SQL migrations |
| `api/openapi.yaml` | API contract — single source of truth |
| `worker/src/vocalcoach/pipeline/` | ML stages, one file per stage |
| `web/src/features/` | React features (auth, songs, analysis, progress) |
| `web/src/api/` | client generated from `openapi.yaml` |
| `deploy/` | compose files, Caddyfile |
| `docs/` | ARCHITECTURE, ML_PIPELINE, RUNBOOK, SECURITY, adr/ |

Dependency direction: `transport → service → repository`. Never inward-out.

---

## Language and comments

- Identifiers, comments, error messages, repo docs: **English**.
- Ukrainian only in i18n files (end-user text).
- Comments explain **why**, never **what**. Short and to the point.
- Godoc on every exported Go symbol. Google-style docstrings on public Python
  classes/functions — state input, output and units (Hz, cents, seconds).
  TSDoc on shared hooks and types.
- Comment inside a function only where the reasoning is non-obvious: octave-error
  workarounds, empirical silence thresholds, memory trade-offs.
- Banned: commented-out code, `TODO` without an issue reference, generated
  explanatory blocks.

---

## Go

- Interfaces are declared by the consumer (`service`), implemented in `repository`.
- `context.Context` is the first parameter of anything touching network or DB.
- Sentinel errors in `domain`; wrap with `fmt.Errorf("...: %w", err)`; map to
  HTTP status only in `transport`.
- No business logic in handlers: decode DTO → call service → encode response.
- No `panic` on the request path. `recover` middleware is mandatory.
- No global state.
- Lint gate: `gofmt`, `go vet`, `golangci-lint` (errcheck, staticcheck, revive,
  gosec, ineffassign, bodyclose, sqlclosecheck).

## Python (ML worker)

- Every stage subclasses `PipelineStage` and implements `run(context) -> StageResult`.
  Adding a stage must not require editing the runner.
- `PipelineRunner` only orchestrates: order, timeouts, retries, progress
  persistence, logging. It contains no DSP.
- Stages are **idempotent** and resumable — a retry starts from the first
  unfinished stage, not from zero (spec 6.1, 6.8).
- DB access through repositories, never raw SQL inside a stage.
- All DTOs are pydantic models. No bare dicts between layers.
- Full type coverage, `mypy --strict`, no silent ignores.
- Determinism: fixed seeds, pinned model versions, same input → same score.
- Memory (spec 6.5): Demucs and Whisper must never be resident at the same time.
  Load lazily via `ModelRegistry`, `release()` after the stage, run heavy stages
  in a child process so the OS actually reclaims memory.
- Lint gate: `ruff`, `mypy --strict`, `pytest`.

## React / TypeScript

- `strict: true`. No `any` without a comment justifying it.
- Structure by feature, not by file type.
- One network layer: the generated OpenAPI client plus an error/refresh wrapper.
  Direct `fetch` in components is forbidden.
- Server state via TanStack Query; local state via `useState`/`useReducer`.
  A global store requires an ADR.
- Presentational components stay side-effect free; logic goes into hooks.
- Tailwind design tokens only — no arbitrary values like `w-[437px]`.
- Accessibility: semantic markup, keyboard support, `aria-*` on the player and
  piano-roll, contrast ≥ WCAG AA.

---

## Security (hard rules, spec 11)

- Validate every external input at the boundary, before business logic.
- Parameterised SQL only. String-concatenated SQL is forbidden.
- External binaries (`ffmpeg`, `yt-dlp`) are invoked with an **argument list**,
  never a shell string, always with a timeout and memory cap.
- Uploads: check magic bytes, not the extension; enforce 15 MB / 6 min; re-encode
  to canonical WAV with ffmpeg before any processing.
- User-supplied filenames are never used as paths. The server generates a UUID path.
- Audio files are deleted no later than 5 minutes after processing ends.
- Never log passwords, tokens, verification codes, full emails (mask as
  `s***@gmail.com`) or audio payloads.
- Secrets live in `.env` only. Nothing secret enters the code or git history.
- Auth: argon2id passwords, 15-min access JWT, rotating refresh token in an
  httpOnly cookie, refresh reuse revokes the whole token family.
- Authorisation is checked on every resource: a user touches only their own data.
- Containers run non-root, `cap_drop: ALL`, `no-new-privileges`. Postgres and
  Redis ports are never published to the host.
- Images are pinned by tag **and** digest. `latest` is forbidden.

---

## Data and config

- Schema changes only through goose migrations, each with a working `down`.
  Manual production DDL is forbidden.
- Migrations stay backward compatible for one release (expand/contract) so an
  image rollback does not break the DB.
- All timestamps are `timestamptz` in UTC.
- Config comes from env, is validated at startup, and the app fails fast with a
  clear message when it is wrong.
- No magic numbers: thresholds, limits and scoring weights are named constants
  or config. Scoring weights are stored with the analysis (`scoring_version`)
  so old results stay reproducible.

---

## Testing (spec 15)

- Go services: table-driven unit tests with mocked repositories.
- Python: each stage tested on synthetic signals (generated sine with known pitch).
- Integration tests run against real Postgres and Redis.
- ML regression: golden fixtures, 5–10 s clips, tolerance ±3 points.
- Real songs and audio fixtures > 1 MB never enter the repo.
- Every fixed bug gets a reproducing test first.
- Targets: Go service layer ≥ 70%, pipeline stages ≥ 60%. Guidance, not a goal.

---

## Git and commits (spec 13)

Author identity, set locally in the repo:

```bash
git config user.name  "stas"
git config user.email "sizhukstanislav@gmail.com"
```

`git log --format='%an <%ae>' | sort -u` must return exactly one line.

Message format — Conventional Commits, English, imperative, lowercase subject,
no trailing period, ≤ 72 chars:

```
<type>(<scope>): <subject>

Why this change is needed (only when non-obvious).

Refs: FR-XX
```

- type: `feat fix refactor perf test docs build ci chore`
- scope: `api worker web db deploy docs`
- One commit = one logical change; `main` stays working after each one.

**Forbidden in commit messages, code and PR descriptions:**

- Any assistant attribution: `Co-authored-by:` with third-party names,
  `Generated with`, `AI-assisted`, bot emoji markers, links to chats.
- IDE plugins adding co-authors automatically — check editor settings.
- 20-line machine-written changelogs. History reads as an engineer's work log.
- Committing `.env`, dumps, model weights, audio fixtures > 1 MB, build artifacts.

The `commit-msg` hook enforces this (`git config core.hooksPath .githooks`) and
CI mirrors the check.

Branches: `main` is protected, work happens in short-lived `feat/FR-22-…` or
`fix/…` branches, one requirement per PR, diff ≤ ~400 lines, squash merge.

---

## Documentation

Docs are updated in the **same PR** as the code that changes them — never later.

- Contract change → `api/openapi.yaml`.
- New/changed service or boundary → `docs/ARCHITECTURE.md`.
- Stage, parameter or scoring weight change → `docs/ML_PIPELINE.md`.
- Architectural decision (library, schema, contract, deviation from the spec) →
  a new `docs/adr/NNNN-*.md`, written **before** implementing it.
- Incident → `docs/RUNBOOK.md`: symptom → cause → action → prevention.
- Setup steps changed → `README.md`.

---

## Definition of Done

- [ ] Code follows the rules above; all linters green.
- [ ] Tests cover the new logic and pass.
- [ ] `openapi.yaml` updated if the contract changed.
- [ ] Affected docs updated in this PR; ADR written if the decision is architectural.
- [ ] Commits follow the convention; single author; no attribution trailers.
- [ ] No new security warnings in CI.
- [ ] Verified manually on `docker compose -f deploy/docker-compose.dev.yml up`.

---

## Hard NOs

- Secrets or PII in code, logs, tests or git history.
- Swallowed errors (`except: pass`, `_ = err`).
- Business rules duplicated across Go, Python and TypeScript.
- Shell string interpolation or concatenated SQL.
- Schema edits bypassing migrations.
- Dead files and "just in case" commented-out code.
- Scope beyond spec section 2.1 without an ADR.

---

## Reference limits

| Setting | Value |
|---|---|
| Upload size / duration | 15 MB / 6 min |
| Analysis wall-time target | ≤ 3 min for a 3-min song on 4 vCPU |
| Worker peak RAM | ≤ 6 GB |
| Queue length | 20, then `429 QUEUE_FULL` |
| Per-user rate limit | 10 analyses/hour |
| Audio retention | 5 min |
| Access / refresh token TTL | 15 min / 30 days |
| Scoring weights | pitch .35, rhythm .20, breath .15, dynamics .10, vibrato .10, timbre .10 |
