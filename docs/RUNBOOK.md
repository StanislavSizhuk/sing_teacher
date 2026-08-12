# Runbook

Deploy, rollback, backup restore, and an incident log (symptom → cause →
action → prevention), updated after every incident (spec 14.1, 17.1).

## Server setup (one-time, before the first deploy)

Spec 11.1's perimeter controls outside `docker compose` itself -- there is
no code that can enforce these, so they are a manual checklist for whoever
provisions the VPS the first time:

1. **Cloudflare** in front of the VPS: DNS-only record switched to
   "proxied" for the app's domain. Gives DNS proxying, basic DDoS
   absorption, and hides the origin IP -- it sits in front of the compose
   stack, not inside it, so it does not conflict with "everything on one
   VPS".
2. **UFW, deny by default:**
   ```bash
   sudo ufw default deny incoming
   sudo ufw default allow outgoing
   sudo ufw allow OpenSSH
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```
   Only 80/443 (Caddy) and SSH are ever exposed -- Postgres and Redis are
   never published to the host in the first place (`deploy/docker-compose.yml`
   has no `ports:` for either), so there is nothing to block for them
   specifically.
3. **SSH hardening** (`/etc/ssh/sshd_config`, then `systemctl restart sshd`):
   ```
   PasswordAuthentication no
   PermitRootLogin no
   ```
   Key-based auth only, deploy user is not `root`.
4. **fail2ban** against SSH brute force:
   ```bash
   sudo apt-get install -y fail2ban
   sudo systemctl enable --now fail2ban
   ```
   The stock `sshd` jail that ships with fail2ban is enough; nothing else
   in this stack listens on a host-exposed port for fail2ban to watch.

Redo this checklist for every new VPS; nothing here is part of
`docker compose up` because none of it is portable across hosts.

## Deploy (with automatic rollback)

Tag the release on `main` first (spec 13.5: SemVer, e.g. `v0.6.0`,
`CHANGELOG.md` updated in the same PR), then, on the VPS as the deploy user:

```bash
git fetch --tags
deploy/deploy.sh v0.6.0
```

`deploy/deploy.sh` implements spec 16.2's sequence end to end:

1. Checks out the given ref (refuses if the working tree isn't clean).
2. `docker compose -f deploy/docker-compose.yml up -d --build`. `go-api`
   applies any pending goose migrations itself on boot, before it starts
   accepting requests -- there is no separate migrate step.
3. Polls `go-api`'s own container `HEALTHCHECK` (gated on `/readyz`, which
   is gated on migrations having applied) for up to 60s.
4. If it never reports healthy, checks out whatever ref was running before
   this deploy, rebuilds, and polls again. Exit code distinguishes a clean
   rollback (`1`) from a rollback that *also* failed to come up healthy
   (`2`, needs a human -- see "Incidents" below).

Watch it live in another terminal:
`docker compose -f deploy/docker-compose.yml logs -f go-api`.

Migrations are written expand/contract (backward compatible for one release,
spec 7/16.2), so rolling the image back one release never leaves the schema
in a state the older code can't read -- this is what makes step 4's
automatic rollback safe to do without a human checking the schema first.

**Verified:** rehearsed locally (a fresh clone stood in for the VPS, since
this project has no provisioned production server or staging environment
yet, spec 16.3) -- both the success path and a forced failure (an
intentionally broken ref) were exercised; the failure case correctly rolled
back and exited `1`, and `go-api` came back up serving the previous ref.
No CD automation triggers this script; it is run by hand, matching this
stage's CI-only scope (spec 16.1 already covers automated lint/test/
security/build on every PR -- deploy stays a deliberate, manual action).

**`python-worker` can take up to ~10 minutes to stop.** `should_stop` is
only checked between pipeline stages (`worker/src/vocalcoach/pipeline/
runner.py`), never during one, so `deploy/docker-compose.yml` gives it
`stop_grace_period: 630s` -- long enough to cover the slowest single stage
(`SEPARATE_RECORDING_TIMEOUT_SECONDS`/`SEPARATE_REFERENCE_TIMEOUT_SECONDS`,
both 600s) plus cleanup margin. `docker compose up -d --build` only waits
as long as the container actually takes to exit, so most deploys are
unaffected -- only one that lands mid-Demucs runs long. Setting this too
low brings back the exact failure mode it exists to prevent: Docker
SIGKILLs the worker before the running stage finishes and `should_stop`
is ever checked, losing up to ~10 minutes of work and forcing a full
stage re-run on restart.

## Rollback (manual)

If you need to roll back without re-running the full script (e.g. days
later, not right after a failed deploy):

```bash
git checkout <previous-tag>
docker compose -f deploy/docker-compose.yml up -d --build
```

Same expand/contract guarantee as above applies.

## Restore from backup

The `backup` service writes a nightly `pg_dump | gzip` to `./backups`
(14-day local retention). To restore:

```bash
docker compose -f deploy/docker-compose.yml stop go-api
gunzip -c backups/<file>.sql.gz | docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose -f deploy/docker-compose.yml start go-api
```

Per spec 17.1, do a full practice restore once a month on a local machine
(not the production DB) and record the result below -- a backup that has
never been restored is not a backup.

| Date | Result | Notes |
|---|---|---|
| -- | -- | first restore drill not yet performed (stage E1, no production data yet) |

## Rotating a leaked secret

1. Generate a new value, update `.env` on the server (permissions `600`).
2. `docker compose -f deploy/docker-compose.yml up -d` (recreates the
   affected containers with the new value).
3. If `JWT_SECRET` leaked: every previously issued access token is now
   invalid the moment the new secret is live (by design -- old tokens fail
   signature verification). Also revoke all refresh-token families, since a
   leaked JWT secret is often a sign the whole host was compromised:
   `docker compose -f deploy/docker-compose.yml exec redis redis-cli -a "$REDIS_PASSWORD" --scan --pattern 'auth:refresh*' | xargs -r docker compose -f deploy/docker-compose.yml exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL`
4. If `POSTGRES_PASSWORD`/`REDIS_PASSWORD` leaked: rotate in `.env`, then
   `ALTER USER ... PASSWORD` / `CONFIG SET requirepass` before recreating
   containers, so there's no window where the old and new credentials both
   need to work.

## Incidents

None yet -- stage E1 has not been operated in production.

### 2026-08-01 -- song stuck at "waiting for song to be ready" (dev environment)

**Symptom:** a song's cold-path prep and every analysis waiting on it sat on
"waiting for song to be ready" for several minutes, then flipped to
`failed` / `TIMEOUT` -- reproducibly, on every retry, regardless of which
pitch engine or how generous the timeout.

**Cause:** two independent issues in the dev compose stack:

1. `prep_reference_pitch` kept "exceeding" its `PREP_REFERENCE_PITCH_TIMEOUT_SECONDS`
   budget -- at 120s, then still at 180s and 300s once those were tried as
   a fix. The real bug was in `PipelineRunner._join_subprocess`
   (`worker/src/vocalcoach/pipeline/runner.py`): it called
   `process.join(timeout)` *before* draining `result_queue`. A
   `multiprocessing.Queue` feeds pickled data into a fixed-size OS pipe
   (~64KB on Linux) from a background thread in the child; this stage's
   result is a full song's pitch curve at a 10ms hop, easily 300-400KB
   JSON-encoded. The child's `put()` blocked on the full pipe, the parent
   was blocked in `join()` waiting for an exit that could never happen
   before that `put()` returned -- a textbook multiprocessing deadlock,
   released only by the timeout's `terminate()`/`kill()`, which is why
   every attempt "timed out" at exactly its configured budget no matter
   the value: nothing was ever slow, the pipe was just never read. Switching
   `PITCH_ENGINE` to `pyin` and raising the timeout to 180s/300s were both
   dead ends chased before finding this -- reverted once the real fix
   landed. Fixed by reading the queue first
   (`result_queue.get(timeout=...)`), which drains the pipe as data
   arrives, then joining (now unbounded, but safe -- the child exits
   promptly once its write unblocks).
2. `transcribe` was separately crashing with `ModuleNotFoundError: No
   module named 'faster_whisper'` (optional stage, skipped, not the
   blocker): the `python-worker` image had been built before the commit
   that swapped `openai-whisper` for `faster-whisper`, and
   `deploy/docker-compose.dev.yml`'s anonymous `/src/.venv` volume carries
   the old venv forward across a plain `up --build` unless anonymous
   volumes are explicitly renewed.

**Action:** fixed `_join_subprocess`'s read-then-join order (the actual
fix); rebuilt the worker image with
`docker compose -f deploy/docker-compose.dev.yml up -d --build --renew-anon-volumes python-worker`
to pick up `faster-whisper`. `PITCH_ENGINE` and the pitch timeouts are
back at their original values (`crepe`, 120s/180s) -- they were never the
problem.

**Prevention:** after any change to `worker/pyproject.toml` /
`worker/uv.lock`, rebuild with `--renew-anon-volumes` -- a plain `--build`
alone reuses the old anonymous `.venv` volume and silently keeps stale
dependencies. When a subprocess-isolated stage "times out" at exactly its
configured budget across multiple different budget values, suspect a
join-before-drain deadlock before suspecting real compute time --
especially for any stage whose result carries a dense array (pitch curves,
piano-roll data) rather than a few scalars.

### 2026-08-02 -- retry showed an hours-long wait; a migration hung for 6 minutes

**Symptom (1):** retrying a failed analysis rendered "Waiting 525m 52s" in
`QueueStatus.tsx`'s new live wait timer, seconds after clicking Retry.

**Cause (1):** the timer read `analysis.createdAt`, but Retry (FR-26)
reuses the same row rather than creating a new one, so `created_at` stays
at the *original* submission -- hours earlier, in this case. Fixed by
adding `analyses.queued_at` (migration `00012_analyses_queued_at.sql`):
equal to `created_at` for a fresh Enqueue, reset to `now()` by
`Retry`/`RetryToWaitingForReference`. The client now reads `queued_at`.

**Symptom (2):** applying that migration hung for 6 minutes --
`docker compose exec postgres psql ...` calls unrelated to the `analyses`
table (even `SELECT 1`) returned instantly, but anything touching
`analyses` hung too, and `go-api`'s own log sat at "running..." with no
"migrations applied" line.

**Cause (2):** `pg_stat_activity` showed one connection `idle in
transaction` for hours, running
`SELECT song_id FROM analyses WHERE status = 'waiting_for_reference' ...`
-- `PostgresAnalysisRepository.oldest_waiting_song_id` (and both
`get_by_id` methods), `worker/src/vocalcoach/repositories/postgres.py`.
psycopg opens an implicit transaction on the first statement of a session
even for a plain read; none of these three methods ever called `commit()`
or `rollback()`. `Scheduler` calls `oldest_waiting_song_id` before every
`songs:prep` tick on one long-lived connection -- the very first tick with
nothing waiting left that connection `idle in transaction` indefinitely,
holding a lock that blocked every later `ALTER TABLE analyses`, including
this migration and its own predecessors (`pg_stat_activity` had ten-plus
queued `ALTER TABLE` attempts piled up behind it from `air`'s repeated
hot-reload retries). Unblocked with `SELECT pg_terminate_backend(<pid>)`;
fixed by adding `self._conn.rollback()` after each of the three reads.

**Prevention:** every method on these repositories must end its
transaction, reads included -- `commit()` for a write, `rollback()` for a
read (nothing to persist, and `rollback()` reads more honestly than
`commit()` for a statement that changed nothing). If a `docker compose
exec postgres psql` hangs on one table but `SELECT 1` doesn't, check
`pg_stat_activity` for `idle in transaction` before assuming the query
itself is slow.

### 2026-08-02 -- every reference vocal stem was silently 2x its real length

**Symptom:** every warm-path analysis against any song failed
`ALIGNMENT_FAILED` -- "the `N`-frame reference is unreachable from the
`N/2`-frame recording" -- even for a recording that was, byte for byte,
the same song used as the reference.

**Cause:** `DemucsSeparator.separate_vocals`
(`worker/src/vocalcoach/pipeline/registry.py`) returned Demucs' separated
stem untouched. `separate_tensor`'s own docstring says the input "will be
resampled if it doesn't match the model" -- htdemucs' native rate is
44.1kHz, and it never resamples back down before returning. This
pipeline runs at `PIPELINE_SAMPLE_RATE_HZ = 22050`, so the returned
tensor had exactly 2x the samples the caller assumed, silently violating
`VocalSeparator`'s own documented contract ("same sample rate ... as
mixture"). `separate_reference.py` labeled the WAV it wrote with the
*original* 22050 regardless, so `song-stem-<id>.wav`'s header and its
real sample count disagreed by 2x: reading it back reported double the
song's true duration (a real 165s song read as 330s). Every later
consumer of that stem -- P4's reference pitch curve, the warm path's
`features`/`align` frame counts -- inherited that same 2x inflation,
which is what made alignment against a correctly-sized recording
impossible outright. Likely present since `separate_reference` was first
written; this is probably why the project's ML pipeline had never
completed a real run before 2026-07-30 (see the post-E6 pipeline audit).

Fixed by resampling the stem back to `sample_rate_hz` with
`demucs.audio.convert_audio` (the same helper Demucs itself uses for the
input side) before returning it.

**Action:** `worker/tests/test_registry.py` gained
`test_demucs_separator_returns_input_sample_rate_not_the_models`, which
monkeypatches Demucs' own API (no real model/weights needed, spec 15.2)
to return audio at a different rate than requested and asserts the
output length matches the input's rate -- confirmed to fail against the
pre-fix code. Verified live end to end too: re-separating a real 165s
song's stem after the fix reads back at 165.17s, not 330.34s.

**Prevention:** when a third-party API's docstring says it "will
resample" one side of a call, check whether it resamples back before
returning -- and prefer asserting the *contract* (same rate/length as the
input) in a test with a fake standing in for the real dependency, not
just the happy path.

### 2026-08-02 -- `web`'s `npm run generate:api` had been regenerating from a stale copy for days

**Symptom:** `npm run lint` failed with
`Unsafe assignment of an error typed value` on `queued_at` -- a field
`api/openapi.yaml` has had since migration `00012`. `tsc --noEmit` had
passed on the same line minutes earlier.

**Cause:** `deploy/docker-compose.dev.yml`'s `web` service never mounted
`../api` at all. `npm run generate:api` runs `openapi-typescript
../api/openapi.yaml -o src/api/schema.gen.ts` relative to `/src` inside
the container, so `../api/openapi.yaml` resolved to `/api/openapi.yaml` --
a file that existed only because someone had `docker cp`'d it in by hand
at some earlier point (dated 2026-07-30 in the container's writable
layer) as a one-off workaround, then never updated again. Every
`generate:api` run since silently "succeeded" against that stale copy,
so `schema.gen.ts` had been drifting further behind `openapi.yaml` for
days -- missing `mode`, `confidence`, `warnings`, `weights_profile`,
`waiting_for_reference`, and more, none of it caught because CI's own
drift check presumably runs `generate:api` inside the same broken
container image family (untested against the live host file either).
`tsc --noEmit` didn't catch the missing `queued_at` field because
whatever looseness `openapi-typescript`'s older generated shape had let
`data.queued_at` resolve to an implicit `any` rather than a hard
property-does-not-exist error; only eslint's `no-unsafe-assignment` rule
(assigning that `any` to a `string`-typed field) surfaced it.

**Action:** added `../api/openapi.yaml:/api/openapi.yaml:ro` to `web`'s
volumes in `deploy/docker-compose.dev.yml`; regenerated
`schema.gen.ts` for real (a ~2,300-line diff, all of it the file catching
up to the real spec -- not reformatting).

**Prevention:** `npm run lint` (not just `tsc --noEmit`) needs to be part
of the routine check after any API contract change -- it caught a real,
days-old client/server drift that the type checker alone missed. If a
generated-file diff is suspiciously large, verify what actually changed
(symbol counts, a known recently-added field) before assuming it's just
reformatting and reverting it.

### 2026-08-02 -- the fix above didn't take effect until the container was recreated

**Symptom:** same session, later the same day: `npm run generate:api`
inside the already-running `web` container silently regenerated
`schema.gen.ts` *missing* a field (`locale`) just added to
`openapi.yaml` minutes earlier -- and, worse, reverted several other
already-landed fields (`webm`, `LENGTH_MISMATCH_PARTIAL_ANALYSIS`) back
out, a ~1,234-line diff that looked like unrelated churn at a glance.

**Cause:** the mount added by the fix above was real and correct in
`deploy/docker-compose.dev.yml`, but the `web` container already running
at the time had been created *before* that compose edit landed --
`docker compose up` does not retroactively recreate a still-running
container just because its `volumes:` list changed in the file on disk
mid-session. It kept serving whatever pre-mount snapshot of
`/api/openapi.yaml` it started with (confirmed via `md5sum` differing
between host and container, and the container's copy missing content
added days earlier), so every `generate:api` run since had been quietly
regressing `schema.gen.ts` back to that snapshot instead of catching it
up.

**Action:** `docker compose -f deploy/docker-compose.dev.yml up -d
--force-recreate web`, confirmed via `md5sum /api/openapi.yaml` matching
the host before trusting `generate:api` again.

**Prevention:** a volume/mount change in a compose file needs
`--force-recreate` (or `down`+`up`) on the affected service to actually
take effect on a container that predates the edit -- `up -d` alone is not
enough, and nothing about that container's logs or health check hints
that it's running stale mounts. Before trusting any `generate:api` output
after touching `openapi.yaml`, verify the container's own view of the
file matches the host (`md5sum`) rather than assuming the mount is live.

### 2026-08-02 -- `go-api`'s `air` live-reload silently served a stale binary after a `git stash`

**Symptom:** a new request field (`locale`, ADR-0031) was confirmed sent
correctly by the browser (`FormData` inspected directly) and confirmed
handled correctly by `dto_analyses.go`/`handlers_analyses.go` (code
review, `go vet`, `go test` all clean), yet every analysis created
through the live dev stack still landed in Postgres with the column's
default (`locale = 'en'`) regardless of what was sent. `air`'s own logs
showed a normal `building...` / `migrations applied` cycle completing
successfully the last time any `.go` file changed, with no error and no
further rebuild activity since.

**Cause:** not fully isolated, but strongly correlated: earlier in the
same session, `git stash` / `git stash pop` was run on the host to check
whether an unrelated test failure pre-existed on a clean tree. Since
`web`'s bind mount (`../api:/src`) means the host and container share
the same files, that stash briefly reverted every uncommitted `.go`
change (including the `locale` wiring) on disk and then restored it a
few seconds later -- and `air`'s file-watcher logged no rebuild at all
for either half of that flap, meaning whatever binary was running before
the stash kept running through it, unverified whether that binary still
matched the current (post-`stash pop`) file contents. A plain container
restart (`docker compose restart go-api`) immediately fixed it, forcing
a fresh `air` startup and rebuild from the current on-disk state.

**Action:** `docker compose -f deploy/docker-compose.dev.yml restart
go-api`; re-tested and confirmed `locale` now persisted correctly.

**Prevention:** treat `air` (or any dev-loop file watcher) as
untrustworthy after any bulk/out-of-band change to its watched
directory -- `git stash`, `git checkout` of another branch, `git reset`,
or anything else that rewrites many files near-simultaneously outside
the editor's normal one-file-at-a-time save pattern. When behavior
doesn't match code you can see on disk and there's no compiler error to
explain it, restart the service before spending more time reading the
code again -- a live-reload staleness check is cheaper than a second
full code review.

### 2026-08-02 -- `mixed`-mode analyses repeatedly failed `ALIGNMENT_FAILED`, even against the identical song as reference

**Symptom:** a `mixed`-mode analysis raised `ALIGNMENT_FAILED` on a real
165s recording, repeatedly, including the degenerate case of using the
exact same track as both the reference and the "recording" -- a case that
should align almost perfectly.

**Cause:** `mixed`'s only pitch source for the user's own recording was
`dsp/melody.py::extract_melody` (ADR-0025), a DSP salience heuristic
reading F0 directly off the still-mixed audio -- the recording never went
through Demucs (ADR-0003 kept it reference-only). Measured directly:
`extract_melody` reported 87.5% of this recording "voiced," including
confidently through purely instrumental sections, while the reference's
real Demucs-separated vocal stem for the same song was genuinely only
~60% voiced (~63s of real instrumental gaps across 6 breaks, RMS -45 to
-60 dB relative to peak there vs -4.6 dB during real vocal content --
confirmed as genuine gaps, not a detection artifact). The two pitch
curves' silence structure disagreed enough that DTW's warping path
saturated at the band edges with high variance even at a generous ±10s
band, regardless of ADR-0033's pitch-contour embedding (tested, ruled out
as a fix on its own -- the mismatch was upstream of what alignment's
distance metric could paper over).

**Action:** ADR-0034: `mixed` now separates the recording with Demucs too
(`SeparateRecordingStage`, the same `VocalSeparator` the reference already
used), through one resolver (`pipeline/voice_source.py::voice_audio_path`)
so `features`/`align` always agree on which audio they're reading.
`dsp/melody.py`/`MelodyPitchStage` deleted outright -- after ADR-0033
moved F0 extraction into `align`, the `mixed`-only scoring stage had
already become byte-identical to `PitchStage`, which now covers both
modes. `recording_condition` was fixed to keep reading the *raw*
pre-separation recording (a stem would defeat its own accompaniment
check). Verified directly against the real recording/reference pair that
produced this incident: `align` now succeeds with `normalized_distance =
0.0238` (ceiling `0.45`), `length_mismatch = false`, where it previously
raised `ALIGNMENT_FAILED` every time.

**Prevention:** when two sides of a comparison are meant to be
structurally comparable (here: two pitch curves, one feeding DTW against
the other), check that both are produced by the *same kind* of processing
before tuning the comparison's own thresholds -- ADR-0033's pitch-contour
change was a real improvement but couldn't fix a mismatch that started
one stage earlier, in what produced each curve to begin with. Also: `docs/
PERFORMANCE.md`'s NFR-01c estimate rested on `extract_melody` staying
cheap because it skipped separation entirely; a decision made purely for
latency, without validating the accuracy trade-off against real
recordings, cost more (a real, repeated production-shaped failure) than
the latency it saved.

### 2026-08-12 -- `python-worker` crash-looped every restart, filling worker and Postgres logs (dev environment)

**Symptom:** `deploy-python-worker-1` restarted roughly once a minute,
printing a full traceback each time; its own log and `deploy-postgres-1`'s
grew to 6.3G and 3.4G respectively from the resulting flood of fresh
connections and failed queries.

**Cause:** a stray Redis Streams entry on `analyses:run` (`job_id =
"job-resume"`) sat in the `analyses:workers` consumer group's pending list
past `PENDING_CLAIM_MIN_IDLE` with `times_delivered` already over
`MAX_CLAIM_ATTEMPTS` -- each container restart's own startup
`reclaim_stuck_jobs()` counted as another delivery, so it kept crossing the
threshold again immediately. The producer never writes anything but a real
`uuid.UUID.String()` (`api/internal/queue/producer.go:87,108`); this entry
most likely came from manual `redis-cli XADD` testing of the reclaim path,
left behind afterwards. `Consumer.reclaim_stuck_jobs`
(`worker/src/vocalcoach/queue/consumer.py`) correctly picked it for
give-up and called `_give_up`, which called `AnalysisJobHandler.
mark_permanently_failed` -> `PostgresAnalysisRepository.mark_failed`,
which failed with `psycopg.errors.InvalidTextRepresentation: invalid
input syntax for type uuid: "job-resume"` (`analyses.id` is a `uuid`
column). `_give_up` had no exception handling around that call, so the
error propagated out of `reclaim_stuck_jobs`, out of `run_forever`
(called at startup, before the main loop even begins), and crashed the
whole process -- restart policy relaunched the container immediately,
which hit the same still-pending entry in its own startup reclaim sweep
within seconds, repeating forever.

Fixing that alone surfaced a second, related bug: no method on
`PostgresAnalysisRepository`/`PostgresSongRepository` (`worker/src/
vocalcoach/repositories/postgres.py`) wrapped its `cur.execute()` +
`self._conn.commit()` in a `try`/`except` -- when `execute()` itself
raised, `commit()` was skipped, leaving that call's implicit transaction
aborted on the worker's one long-lived, per-process connection. The very
next query on that connection (`oldest_waiting_song_id`, called every
scheduler tick) then failed too, with `psycopg.errors.
InFailedSqlTransaction`, uncaught, crashing the process a second time per
restart. The two `get_by_id` reads had the same gap: their
`self._conn.rollback()` calls (added for the 2026-08-02 "migration hung
for 6 minutes" incident above) sat *after* the cursor block, so they were
skipped too whenever `execute()` itself raised, not just on the success
path they were written for.

**Action:** `Consumer._give_up` now catches any exception from
`mark_permanently_failed`, logs it, and still removes the stream entry
(`XACK`+`XDEL`) -- a job that cannot even be recorded as failed must not
block cleanup, or it retries forever, exactly what spec 10.1's give-up
path exists to prevent. Every method in `postgres.py` now wraps its
cursor block in `self._conn.transaction()` (writes) or
`self._conn.transaction(force_rollback=True)` (reads) instead of a bare
`commit()`/`rollback()` call placed after the `with cur` block --
psycopg3's transaction context manager commits or rolls back on a clean
exit *and* on any exception raised inside it, so a failed query can no
longer leave the shared connection aborted for the rest of the process's
life. Confirmed live against the running dev `python-worker`: after the
fix landed, the poison entry was logged, its failure caught, and the
entry removed on the very next reclaim; the container has stayed up
since, with `analyses:run` empty.

**Prevention:** any repository method that ends with a bare `commit()`/
`rollback()` call *after* its cursor block skips that call on the
exception path -- use `conn.transaction()` (`force_rollback=True` for a
read) instead, so the transaction always closes regardless of outcome.
Any consumer-side "give up on this job" path must treat recording that
give-up as best-effort: if a job cannot even be marked failed, it must
still be removed from the queue rather than left to block forever or
crash-loop the whole worker.
