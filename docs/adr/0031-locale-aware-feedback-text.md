# ADR-0031: Locale-aware feedback text generated in the worker, not the client

- Status: Accepted
- Date: 2026-08-02

## Context

`feedback_text` (FR-32, `worker/src/vocalcoach/pipeline/report.py`) is
English prose built once in the worker and stored verbatim
(`analyses.feedback_text`) -- the Go API and web client both pass it
through untouched. Every other user-facing string in `web/` already comes
from `useTranslation()` (ADR-0029), so a Ukrainian-language session still
sees this one block of text in English, with no way to localize it after
the fact: the numbers it's built from (mean cents off, ms offset, matched
pause counts, vibrato rate/depth deltas, correlation) never leave the
worker as separate fields, only baked into already-formatted English
sentences.

Two ways to fix this:

1. Move report generation to the client: expose the raw per-aspect
   numbers (already computed, just not returned) plus the tier/threshold
   decision as new `Analysis` fields, and have `web/` template the prose
   itself via `useTranslation()`, the same way it already does for
   `warnings`/`unavailableAspects`.
2. Keep generation server-side, in the worker, and make it locale-aware:
   thread the caller's chosen language in at analysis time, same as
   `mode` and `allow_transposition` already are (FR-27/FR-31), and have
   `report.py` pick per-locale phrase templates.

Option 1 looks consistent with the `warningText()`/`unavailableReasonText()`
precedent, but those translate a fixed, closed set of *codes* (a dozen
warning codes, one unavailable-aspect reason) -- a lookup, not a decision.
Feedback text is different: which tier applies, which of vibrato's nine
rate/depth combinations describes this take, whether breath has anything
to compare against at all -- that branching is exactly the kind of
business rule CLAUDE.md's hard rules ban duplicating across Python and
TypeScript ("Business rules duplicated across Go, Python and TypeScript").
Reimplementing `_tier()` and all six aspects' branch logic in
`AnalysisReport.tsx` would be exactly that duplication, just in a new
place, and every future change to a threshold or a wording branch would
need to land correctly in two languages *and* two runtimes to stay in
sync.

## Decision

Report generation stays entirely in `report.py`, in exactly one place, in
one language-agnostic call per aspect. What changes is that every
branch's output is now a *template lookup* keyed by locale rather than a
literal English string: each `_xxx_feedback` function still decides which
outcome applies (tier, matched-pause counts, which of vibrato's rate/depth
combinations fits) exactly as before -- that decision runs once,
regardless of locale -- and only the last step, turning that outcome into
a sentence, is now `_TEMPLATES[locale][outcome].format(**args)` instead of
an inline f-string. A `Locale = Literal["en", "uk"]` (`models/locale.py`,
a tiny dependency-free module mirroring `models/mode.py`) flows from the
web client through the same path `mode`/`allow_transposition` already
use: `useLanguage()` (ADR-0029) at `POST /analyses` → Go's `analyses.locale`
column (migration 00013) → `AnalysisRecord.locale` → the job handler
passes it into `AnalysisContext.locale` → `AggregateStage` passes it to
`build_feedback_report`. Like `mode`, it is fixed at creation time; a past
analysis opened later in a different UI language still shows the report
in whichever language was active when it was generated, the same
trade-off `mode` already accepts. Vibrato's rate/depth wording (previously
built by joining English sentence fragments like `"faster" + " in rate"`)
is restructured into nine fully-written, locale-specific sentences keyed
by a locale-agnostic combination code (`rate_faster_depth_wider`, etc.)
instead of word-substitution -- word-by-word translation across languages
with different grammar reads badly, and this was already awkward English.

A test (`worker/tests/test_report.py`) asserts every per-locale template
dict in the module has the exact same key set in `en` and `uk` -- Python
dict literals have no compile-time exhaustiveness check the way
`Translations = typeof en` gives `web/`'s translation files, so this test
is what stands in for it: a template added for one locale and forgotten
for the other fails immediately instead of surfacing as a `KeyError` on
whichever unlucky report needs that specific branch.

## Consequences

Gets easier: adding a third language only ever touches `report.py`'s
template dicts (plus `web/src/i18n/`'s own dictionaries for the rest of
the UI, unrelated) -- the branching logic that decides *what* to say
never needs touching again. The key-set-parity test catches a missed
template at test time, not in production for whichever user's analysis
happens to hit that exact branch.

Gets harder: `report.py` is now roughly twice as long (a template dict
per locale, per aspect, instead of one), and the `Locale` field has to be
threaded through every layer that already carries `mode`/
`allow_transposition` (migration, Go domain/DTO/repository/handler,
worker `AnalysisRecord`/`AnalysisContext`) -- boilerplate, but plumbing,
not a design difficulty. A past analysis's report language is locked in
at creation, so switching the UI to Ukrainian never retroactively
translates old reports; expected, matches `mode`'s existing behavior, not
called out to the user anywhere new.

## Alternatives considered

- Client-side templating from exposed raw numbers (option 1 above) --
  rejected: duplicates the tier/branch decision logic across Python and
  TypeScript, which CLAUDE.md bans outright, and doubles the number of
  places a scoring-threshold or wording change has to land correctly.
- Translate `feedback_text` on the fly per request (detect the caller's
  `Accept-Language`, machine-translate or re-render at `GET` time) --
  rejected: re-rendering needs the same raw per-aspect numbers option 1
  would have needed anyway (they're not stored, only the final English
  string is), and machine translation of a data-grounded report risks
  mistranslating the actual numbers it's built from.
- A single shared locale for the whole account instead of per-analysis --
  rejected: `mode` and `allow_transposition` are already per-analysis,
  chosen at submission time, not account settings; locale fits the exact
  same shape and the same reasoning (a user might genuinely want to
  compare a Ukrainian report against an English one from before they
  switched, not have history silently reinterpreted).
