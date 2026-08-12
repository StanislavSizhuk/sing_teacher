# ADR-0037: add a PO Token provider sidecar for YouTube's bot-check

- Status: Accepted
- Date: 2026-08-12
- Supersedes: ADR-0036

## Context

ADR-0036 (same day) accepted YouTube's PO Token bot-check as a permanent,
unfixed limitation of FR-11: `AddFromYouTube` fails intermittently with a
generic, retriable error, and a user is expected to retry. That ADR
explicitly evaluated and rejected a PO-Token-provider sidecar
(`bgutil-ytdlp-pot-provider`) as disproportionate for "a personal,
non-commercial, single-VPS project" -- but also left the door open: "if
this failure rate becomes disruptive enough in practice to reconsider, ...
revisit with a fresh ADR."

That reconsideration has happened: the product decision now is that
best-effort/retry-and-hope is not an acceptable experience for FR-11, and
the sidecar's added maintenance surface is worth taking on. This ADR
supersedes ADR-0036's posture, not its facts -- the mechanism, the
failure's cause, and the alternatives it evaluated are all still accurate
and are not re-litigated here.

### Verification done before committing to this

`bgutil-ytdlp-pot-provider` v1.3.1 (server + yt-dlp plugin, both current as
of this writing) was run end to end against the real service, not just
read about:

- The server image (`brainicism/bgutil-ytdlp-pot-provider:1.3.1`, pinned
  digest below) starts and serves `GET /ping` (`{"server_uptime":...,
  "version":"1.3.1"}`, HTTP 200) under the exact hardening this ADR applies
  in compose: `--read-only --tmpfs /tmp --cap-drop ALL
  --security-opt no-new-privileges`, no root needed (the image's own
  Dockerfile already runs as a non-root `node` user).
- `POST /get_pot` returns a real `poToken`/`contentBinding` pair.
- Inside a container built from the exact `alpine:3.21` base and package
  pins this ADR adds to `api/Dockerfile` (`ffmpeg`, `python3`, `yt-dlp`,
  `bgutil-ytdlp-pot-provider`, all pinned), `yt-dlp -v` confirms the plugin
  registers the provider: `PO Token Providers: bgutil:http-1.3.1
  (external), ...`.
- A real metadata fetch (`--dump-single-json --skip-download`) against a
  real, ordinary public video succeeded with `--extractor-args
  "youtubepot-bgutilhttp:base_url=..."` pointed at the running sidecar.

Also confirmed, and worth being honest about rather than overclaiming a
full fix: the same ordinary video that failed in ADR-0036's own repro
(`Wx7vo__48oE`) still failed with `Sign in to confirm you're not a bot`
in this testing, both with and without the sidecar, preceded by `HTTP
Error 429: Too Many Requests` on the initial webpage fetch -- before a PO
token is even relevant. A very high-traffic video (`dQw4w9WgXcQ`) succeeded
reliably with the sidecar wired in. This matches, rather than contradicts,
ADR-0036's own characterization: YouTube's enforcement is IP-reputation-
and traffic-pattern-based, stricter for an "ordinary" video than a
heavily-cached one, and a valid PO token raises the success rate without
being a hard override of a coarser, IP-level rate limit sitting in front
of it. `bgutil-ytdlp-pot-provider`'s own docs frame it the same way: "a
real fix for most videos," not all.

Also discovered in the course of this verification: ADR-0036's text
states a JS runtime ("`deno`, already present in the runtime image") --
that is not accurate against the current, checked-in `api/Dockerfile`;
`deno` is not installed anywhere in it. yt-dlp still degrades gracefully
without one (falls back to a client that needs no JS challenge solving,
with a warning), so this did not block the verification above, but it
means one plank of ADR-0036's own context was already stale. Left as a
known gap rather than fixed here -- Alpine 3.21's `apk` `deno` package
(2.0.6) is already below what current yt-dlp's challenge solver expects
("unsupported" in this same testing), so fixing it properly is its own
piece of work with its own pin to get right, out of scope for this ADR.

## Decision

Add `bgutil-ytdlp-pot-provider` as a new sidecar service, `pot-provider`,
in both `deploy/docker-compose.yml` and `deploy/docker-compose.dev.yml`:

- **Image**: `brainicism/bgutil-ytdlp-pot-provider:1.3.1`, pinned by tag
  and digest
  (`sha256:1aaa43a0ca72dfca6a6d2129a0fb4a23465c25adb1b043f8aff829a20825646b`,
  the manifest-list digest for `1.3.1`'s default Node-runtime build) --
  same pinning rule spec 5.3 already applies to `postgres`/`redis`.
- **No published port**: only `go-api` reaches it, over the compose
  network, matching Postgres/Redis's own "never published to the host"
  rule.
- **Hardened the same way `go-api` is**: `read_only: true`, `cap_drop:
  [ALL]`, `no-new-privileges`, `tmpfs: [/tmp]`. The upstream image already
  runs as a non-root user, so no `user:` override is needed (unlike
  `redis`, which does need one).
- **Healthcheck** hits `GET /ping` via `node -e "fetch(...)"` -- the same
  pattern `python-worker`'s healthcheck already uses its own runtime for
  instead of shelling out to a tool that may not be in the image (this
  image's `node:*-slim` base ships neither `wget` nor `curl`).
- `go-api` gets `depends_on: pot-provider: condition: service_healthy`,
  the same pattern already used for `postgres`/`redis`, so `go-api` never
  starts racing a sidecar that isn't ready yet.

`api/Dockerfile`'s runtime stage installs the yt-dlp plugin
(`bgutil-ytdlp-pot-provider==1.3.1`, PyPI, pinned) via the same `pip`
install ADR-0035 already uses for `yt-dlp` itself -- pure Python, no
compiler needed, `py3-pip` still removed afterward. The dev stage
(`apk add yt-dlp`) is left as-is: `apk`'s `yt-dlp` has no `pip`/
`site-packages` to install the plugin into, the same asymmetry ADR-0035
already accepted for that stage. `go-api`'s dev config still points at the
dev `pot-provider` service (so the compose wiring and `/ping` are
exercisable locally), but `apk`'s `yt-dlp` just ignores the unrecognized
`--extractor-args` key rather than using it -- dev keeps ADR-0036's
best-effort behavior, only production actually benefits from the fix.

`internal/youtube.Client` gains a `potProviderBaseURL` constructor
parameter; when set, both `Metadata` and `Download` add
`--extractor-args "youtubepot-bgutilhttp:base_url=<url>"` to the yt-dlp
call. The URL comes from `YOUTUBE_POT_PROVIDER_URL`
(`internal/config`), defaulted to `http://pot-provider:4416` -- the
compose service's own DNS name and the image's own default port -- so no
deployment needs to set it explicitly; only a deployment running the
sidecar somewhere else would override it.

**No new feature flag.** `FEATURE_YOUTUBE_IMPORT` stays the only gate.
Two flags governing overlapping behavior (import on/off, and provider
on/off) would violate this repo's own DRY rule for no real benefit: the
compose service is unconditional infrastructure once this ships (like
Postgres/Redis, not like an opt-in extra), and Go only ever calls yt-dlp
when `FEATURE_YOUTUBE_IMPORT` is already `true` -- an unused second flag
would just be a second place a "was this feature actually on" question
could be asked and answered differently.

## Consequences

- YouTube import (FR-11) succeeds meaningfully more often, verified
  end-to-end above -- not a claim taken on faith the way ADR-0036 declined
  to build this specifically because it hadn't been.
- Still not 100%: an IP-reputation/traffic-pattern layer in front of the
  PO Token check remains, unaffected by having a valid token, and the
  `Sign in to confirm you're not a bot` error path in
  `AddFromYouTube`/`transport/http/problem.go` is unchanged -- a user can
  still see it, just less often. No SLA is being claimed here.
- New standing service and its own update cadence, racing YouTube's
  BotGuard changes the same way `yt-dlp` itself does (ADR-0035) --
  `bgutil-ytdlp-pot-provider`'s image tag and the `pip`-installed plugin
  version both need bumping together when YouTube's side changes enough to
  break token generation, not just when `yt-dlp` itself needs a bump.
  Tracked as a new prevention note in `docs/RUNBOOK.md`, same shape as
  ADR-0035's.
- `go-api`'s startup now also waits on `pot-provider`'s healthcheck --
  another dependency in the boot path, same trade-off already accepted for
  Postgres/Redis.
- Reopens the ToS-exposure question ADR-0028/ADR-0036 both flagged:
  standing infrastructure whose only job is passing an anti-bot check is a
  step further than "importing a song now and then." Accepted as part of
  this decision, not newly discovered by it.
- `docs/SECURITY.md` 11.4's checklist item claiming "no infrastructure is
  built to defeat YouTube's own anti-bot enforcement" is now false and is
  updated in this same PR (spec: docs change with the code that
  invalidates them).

## Alternatives considered

- **Cookies from a real, logged-in YouTube account** -- still rejected,
  for the exact reason ADR-0036 gave: the most reliable option, but
  requires provisioning and maintaining a real Google account's session as
  a standing server-side secret, with a leak exposing an actual account
  rather than just this deployment. Nothing about reconsidering the
  sidecar changes that trade-off; the sidecar generates tokens without
  holding anyone's account credentials, which is exactly why it is the one
  being adopted here and cookies are not.
- **A separate `FEATURE_YOUTUBE_POT_PROVIDER` flag, independent of
  `FEATURE_YOUTUBE_IMPORT`** -- rejected as explained in the Decision
  section: the compose dependency is unconditional either way, so a second
  flag would only add a second, redundant on/off switch for behavior
  already fully gated by the first one.
- **Keep ADR-0036's posture, do nothing** -- this is what is being
  superseded. Rejected now because the retry-and-hope experience proved
  worse in practice than the added maintenance surface this ADR accepts.
