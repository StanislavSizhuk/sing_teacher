# ADR-0028: enable YouTube import by default

- Status: Accepted
- Date: 2026-08-01

## Context

`FEATURE_YOUTUBE_IMPORT` has existed since E2, fully implemented end to
end -- `POST /songs` with `source_type=youtube`, `yt-dlp` duration-checked
before download, an exact-match host allowlist
(`internal/youtube/url.go`), and `web/`'s YouTube tab with a
personal/non-commercial disclaimer shown before the URL field every time
that tab is selected (spec 11.4). Spec 11.4 shipped it defaulted `false`
in `.env.example`/production, reviewed and re-verified as a deliberate
security/legal posture at both E6 and M5 (`docs/SECURITY.md`): downloading
from YouTube for personal, non-commercial use is generally tolerated but
technically violates YouTube's ToS and can touch a song's copyright, so
the feature shipped off until a product decision was made to actually
expose it to users.

That decision is now made: users need to be able to pick a song straight
from YouTube to analyze, not upload their own file every time -- the
primary product reason song selection from YouTube exists at all (spec
2.1). Leaving it off by default meant it was effectively dead code for
every real deployment that copies `.env.example` as a starting point.

## Decision

`FEATURE_YOUTUBE_IMPORT` now defaults `true` in `.env.example` (and this
developer's own `.env`, gitignored). No code changed -- every control spec
11.4 already required stays exactly as built:

- The disclaimer in `web/`'s YouTube tab, shown before the URL field every
  time that tab is selected, not just once.
- The exact-match host allowlist (`youtube.com`, `www.youtube.com`,
  `m.youtube.com`, `music.youtube.com`, `youtu.be`) --
  `youtube.com.evil.example` is still rejected, `yt-dlp` still cannot be
  used as a generic URL-fetch oracle.
- Duration checked via `yt-dlp --skip-download` before any bytes download
  (FR-12); the same sniff/probe/ffmpeg-transcode pipeline uploads go
  through applies identically to what `yt-dlp` extracts.
- The flag itself: a deployment that has a specific reason to disable it
  (a stricter legal posture, a takedown request) still can, by setting
  `FEATURE_YOUTUBE_IMPORT=false` in its own `.env`.

## Consequences

- Every fresh deployment starting from `.env.example` now exposes YouTube
  import out of the box, with the disclaimer as the remaining, and only,
  user-facing mitigation for spec 11.4's ToS/copyright caveat -- the
  feature flag is no longer a safety net, just an operator escape hatch.
- `docs/SECURITY.md`'s 11.4 checklist and the E2 "File upload and YouTube
  import" section are updated to reflect the new default rather than
  describe a posture that no longer matches `.env.example`.
- No new attack surface: this is a default flip, not new code. Every
  control 11.4 requires was already implemented and already reviewed.

## Alternatives considered

- **Leave the default off, document that operators can turn it on** --
  rejected: this is exactly the status quo that made the feature
  effectively unused; the product need (spec 2.1) is for this to work out
  of the box, not for every operator to discover and flip an env var.
- **Add a runtime, per-deployment legal-acceptance gate (e.g., a
  first-boot prompt)** -- rejected as disproportionate: this is a
  personal, non-commercial, single-VPS project (spec 1), not a multi-tenant
  product where a stronger consent mechanism would be warranted; the
  existing per-use UI disclaimer already carries that weight.
