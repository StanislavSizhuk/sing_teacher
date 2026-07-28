# ADR-0009: No client-side router in web/ yet

- Status: Accepted
- Date: 2026-07-28

## Context

`web/`'s E2 screens (register/verify/login, add a song, record, queue
status) form one linear flow with no need for a bookmarkable or
deep-linkable URL yet. `react-router-dom`, the default choice for this, was
evaluated during scaffolding: every published `7.12.0+` release (up to and
including the current `7.18.1`) carries an open high-severity advisory
(GHSA-qwww-vcr4-c8h2, an RSC-mode CSRF bypass), and every pre-`7.12.0`
release instead carries a stack of older high-severity issues (open
redirect via backslash in `<Link>`/`useNavigate`, DoS via inefficient route
matching, stored XSS via an unescaped `Location` header, and others) that do
apply to plain client-side routing, not just RSC/framework mode. There is no
published version of the 7.x line that `npm audit --audit-level=high`
passes clean, and CI runs that check on every PR (spec 16.1).

## Decision

Ship E2 without a router: `App.tsx` switches between screens with plain
`useState`, and `AuthScreen` does the same for its own register/verify/login
sub-flow. No routing library is a dependency.

## Consequences

Gets easier: no dependency carrying a known CVE (however unreachable in
practice, given this app never enables RSC/framework mode) sitting in
`package-lock.json`; one fewer thing to configure/learn for a four-screen
flow. Gets harder: no URL reflects app state, so there's no deep link to a
specific song/analysis, no back-button navigation between screens, and no
`useSearchParams`-style state persistence across a reload -- `restoreSession`
on mount is the only state that survives a refresh right now. This will
need revisiting once E4 (piano-roll/report) or E5 (history, progress
charts) add screens that genuinely want their own URL.

## Alternatives considered

- `react-router-dom@7.11.0` (just below the vulnerable range) -- rejected:
  `npm audit` flags it for several older high-severity issues instead (see
  Context), no better than the current version from a CI-gate standpoint.
- A different router (`@tanstack/react-router`, `wouter`) -- not evaluated
  in depth; deferred rather than adding a second library choice under time
  pressure for a stage that doesn't yet need routing at all. Revisit when a
  real multi-URL requirement exists.
- Pin `react-router-dom` and suppress the audit finding -- rejected: spec
  11.6 treats CI security findings as blocking, not something to silence
  per-project.
