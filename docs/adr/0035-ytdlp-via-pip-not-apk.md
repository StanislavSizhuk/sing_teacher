# ADR-0035: install yt-dlp via pip, pinned, instead of apk

- Status: Accepted
- Date: 2026-08-12

## Context

YouTube import (FR-11) has been silently broken: `go-api`'s runtime image
(`api/Dockerfile`, set in ADR-0007) installs `yt-dlp=2025.03.31-r0` from
Alpine 3.21's `apk` community repo. Reproduced directly -- the same
`--dump-single-json --skip-download` call against the same public video
fails on a yt-dlp from that era (`ERROR: Requested format is not
available`) and succeeds unmodified on a current upstream release. YouTube
changes its extraction internals (player signature, format list) often
enough that yt-dlp ships fixes on a similar cadence; Alpine's community repo
repackages far slower, and `api/Dockerfile`'s exact version pin means it
never moves on its own between rebuilds anyway. The failure surfaces to
users only as a generic `500 INTERNAL` (`transport/http/problem.go`'s
`classify` has no sentinel for a `yt-dlp` runtime failure, by design --
internal error text is never leaked to the client), so nothing short of
reading `go-api`'s logs points at the real cause.

## Decision

`yt-dlp` is installed via `pip`, pinned to an exact upstream version
(`yt-dlp==2026.07.04` initially), not `apk`. `python3` and `py3-pip` are
still installed via `apk`, pinned the same way `ffmpeg` already is
(`python3=3.12.13-r0`, `py3-pip=24.3.1-r0`); `py3-pip` is removed again in
the same `RUN` layer right after the install so it never ships in the final
image -- only the `python3` interpreter and the `yt-dlp` package pip placed
in its site-packages remain. `yt-dlp`'s PyPI wheel is pure Python
(`py3-none-any`), confirmed by installing it into a throwaway
`alpine:3.21@sha256:48b03...` container: no compiler or `-dev` headers
needed, so this adds no build tooling to the image.

`ffmpeg` stays on `apk` -- it has no equivalent fast-moving-target problem
(no adversarial site pushing weekly format changes against it) and `apk`'s
pin-by-exact-version already satisfies spec 5.3 for it.

## Consequences

- Fixes YouTube import today: `yt-dlp==2026.07.04` was verified end to end
  (metadata fetch and a real video) inside the same base image the
  production Dockerfile uses.
- The pin is still exact and reproducible (`pip install yt-dlp==<version>`
  is immutable per PyPI's own no-reupload policy), consistent with "pinned
  by tag and digest, `latest` forbidden" (spec 5.3) applied to package
  installs -- the same principle ADR-0007 already applied to `apk`, now
  applied to `pip`.
- New operational reality, not previously documented anywhere: `yt-dlp`
  needs to be bumped far more often than any other pinned dependency in
  this repo, because the failure mode is external and adversarial (YouTube
  changing on its own schedule), not something `go-api`'s own CI can catch
  ahead of time. Tracked in `docs/RUNBOOK.md`'s incident log and its
  prevention note.
- `go.mod`/`package.json`-style automated dependency bumping (Dependabot or
  similar) does not cover `apk`/`pip` versions pinned inline in a
  `Dockerfile` `RUN` line; bumping `yt-dlp`'s pin stays a manual step until
  that gap is worth closing with its own tooling.

## Alternatives considered

- **Bump the `apk` pin to whatever Alpine 3.21 currently carries** --
  rejected as a durable fix: still bound to the community repo's own
  repackaging cadence, which this incident already showed lags upstream by
  enough to break the feature. Would need to be revisited on the same
  timescale that caused this ADR.
- **Official standalone `yt-dlp` binary release (PyInstaller-built,
  downloaded from GitHub Releases, checksum-verified)** -- rejected: those
  Linux builds link against glibc and are not guaranteed to run unmodified
  on Alpine's musl libc without `gcompat`, adding a different runtime
  dependency in place of the one this ADR removes, for no benefit over pip
  once the wheel was confirmed to need no compiler.
- **Vendor yt-dlp as a Python dependency inside `worker/` instead, since
  `worker/` is already a Python service** -- rejected: YouTube import is a
  `go-api` responsibility (FR-11, spec 5.4's `api/` layout, ADR-0007); this
  is an installation-method fix, not a service-boundary change, and moving
  the whole feature across the `api`/`worker` boundary is out of scope for
  fixing a stale binary.
