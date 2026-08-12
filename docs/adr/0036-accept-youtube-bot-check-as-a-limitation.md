# ADR-0036: accept YouTube's bot-check as a known limitation, don't fight it

- Status: Superseded by ADR-0037
- Date: 2026-08-12
- 2026-08-12: superseded the same day -- the PO-Token-provider sidecar
  rejected below as disproportionate is now accepted, ADR-0037. The
  reasoning below (the mechanism, the failure's cause, ADR-0028's ToS
  posture) is unchanged and not re-litigated there.

## Context

Even with `yt-dlp` current (ADR-0035 fixed the stale-pin failure mode) and a
working JS runtime (`deno`, already present in the runtime image), YouTube
import (FR-11) now fails intermittently with `Sign in to confirm you're not
a bot`. Reproduced directly against a real link
(`https://www.youtube.com/watch?v=Wx7vo__48oE`), run repeatedly from inside
the actual `go-api` container:

```
WARNING: HTTP Error 429: Too Many Requests
WARNING: Unable to fetch GVS PO Token for web_safari client: Missing
required Visitor Data. You may need to pass Visitor Data with
--extractor-args "youtube:visitor_data=XXX"
ERROR: Sign in to confirm you're not a bot. ...
```

The same request against the same video succeeded once, then failed twice
in a row on the next two attempts. This is YouTube's PO Token
(proof-of-origin token) rollout: a per-request anti-bot check, independent
of `yt-dlp`'s version, that treats unauthenticated, non-browser requests as
suspect more often once an IP has made a burst of them. Extremely
high-traffic, heavily cached videos (`dQw4w9WgXcQ`, `jNQXAC9IVRw`) succeed
far more reliably than an arbitrary video, because YouTube's serving
infrastructure and `yt-dlp`'s fallback clients treat them more leniently --
the failure rate scales with how "ordinary" the requested video is, not
with anything this codebase controls.

Unlike ADR-0035's stale-pin bug, there is no version to bump and no
one-time fix here: this is an adversarial, continuously evolving check on
YouTube's side, and it targets exactly the shape of traffic this
feature produces (server-side, unauthenticated, no real browser).

## Decision

Ship no additional code or infrastructure against this. `AddFromYouTube`
already surfaces any `yt-dlp` failure as a generic, retriable error (spec
11.3's error-mapping design: internal detail never leaks to the client);
that is left as-is. A user who hits this is expected to retry -- often
immediately, per the observed flip-flop between attempts -- or with a
different video.

This is the same posture ADR-0028 already committed to for this feature:
"personal, non-commercial... the existing per-use UI disclaimer already
carries that weight," with a stronger mechanism explicitly rejected there
as "disproportionate: this is a personal, non-commercial, single-VPS
project, not a multi-tenant product." Building infrastructure specifically
to defeat YouTube's bot detection is a step further than "importing a song
now and then" -- it moves this feature from tolerated personal use toward
deliberate circumvention, which cuts against the same ToS/legal posture
ADR-0028 already flagged as the reason this feature is opt-out-able at all
(spec 11.4).

## Consequences

- YouTube import stays best-effort: most requests succeed, an unpredictable
  fraction fail with a generic error the user can retry. No SLA, no retry
  budget, no queueing changes -- FR-11/FR-12's existing behavior is
  unchanged.
- No new service, no new secret (a PO-token provider or real-account
  cookies would both need one), no new maintenance surface beyond what
  ADR-0035 already accepted for `yt-dlp` itself.
- If this failure rate becomes disruptive enough in practice to reconsider,
  the two paths already evaluated and rejected here (a PO-token provider
  sidecar, or cookies from a real logged-in account) remain available --
  revisit with a fresh ADR if that happens, rather than reopening this one.

## Alternatives considered

- **PO Token provider sidecar** (e.g. `bgutil-ytdlp-pot-provider`) --
  rejected for now: a real fix for most videos, but adds a second
  long-running service next to `go-api` whose whole job is generating
  tokens to pass an anti-bot check, plus its own update cadence racing
  YouTube the same way `yt-dlp` itself does (ADR-0035). Disproportionate
  for a single-VPS personal project per ADR-0028's own reasoning, unless
  the failure rate proves worse in practice than what this ADR accepts.
- **Cookies from a real, logged-in YouTube account** (`--cookies`) --
  rejected: the most reliable option, but requires provisioning and
  maintaining a real Google account's session as a standing server-side
  secret, with materially higher ToS exposure than passive personal use --
  the opposite direction from ADR-0028's already-cautious posture, and a
  cookie leak would expose an actual account, not just this deployment.
- **Retry with exponential backoff inside `youtube.Client`** -- rejected:
  the observed failure is not a transient network blip that a short retry
  loop reliably clears (two failures were observed back-to-back
  immediately after a success); a longer, server-side retry would mostly
  just delay the same generic error while holding a request open, not
  meaningfully improve the success rate.
