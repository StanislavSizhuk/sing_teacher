# ADR-0029: Custom i18n store instead of react-i18next

- Status: Accepted
- Date: 2026-08-02

## Context

`web/` was English-only. The app needs a second language (Ukrainian) and a
switcher between them. The standard choice for this in a React app is
`react-i18next` (plus `i18next`) -- string-key lookup, ICU/plural-rule
support, locale detection, lazy-loaded namespaces.

This project has a demonstrated bias against adding dependencies without a
concrete need: ADR-0009 rejected `react-router-dom` outright over its CVE
history for a routing need E2 didn't actually have yet, favoring plain
`useState` view-switching instead. The same reasoning applies here.
`react-i18next`'s feature set (namespaces, backend loaders, suspense
integration, interpolation via a string-templating micro-language) solves
problems this app doesn't have: two static, bundled dictionaries, no
lazy-loading, no server-side rendering. The one genuinely hard part --
correct plural forms, since Ukrainian has three count-based categories
(one/few/many) where English has two -- is solved by the platform itself:
`Intl.PluralRules`, supported in every browser this app targets, needs no
library at all.

`web/` also already has an established pattern for state that needs to be
read outside the React tree and shared across the whole app without a
Context provider: `api/sessionStore.ts` (a module-level variable, a
`Set<Listener>`, `get`/`set`/`subscribe` functions) plus
`features/auth/useSession.ts` (`useSyncExternalStore` wrapping it) --
called out in `ARCHITECTURE.md` as "the one exception spec 12.4 allows
without an ADR". A language switcher is the same shape of problem (global,
cross-cutting, not one feature's local state) and fits the same solution
exactly.

## Decision

No new dependency. `web/src/i18n/`:

- `language.ts` -- the store: a module-level `Language` ('en' | 'uk'),
  persisted to `localStorage`, defaulting to `navigator.language` if
  nothing is stored yet. Same `get`/`set`/`subscribe` shape as
  `sessionStore.ts`.
- `useLanguage.ts` -- `useSyncExternalStore` wrapper, mirroring
  `useSession.ts`.
- `plural.ts` -- `pluralize(locale, count, forms)`, a thin wrapper over
  `Intl.PluralRules(locale).select(count)`.
- `translations/en.ts` -- the canonical dictionary: a nested object literal,
  static strings as plain values, templated ones as functions
  (`t.queueStatus.numberInQueue(5)`). Its type (`Translations = typeof en`)
  is the contract every other language must satisfy.
- `translations/uk.ts` -- `export const uk: Translations = {...}`. Because
  it's typed against `Translations`, a missing key, an extra key, or a
  function with the wrong parameter shape is a compile error, not a
  runtime fallback to English or a blank string discovered by a user.
- `useTranslation()` -- returns the current language's whole dictionary
  object. Callers read `t.app.title` (static) or call `t.errorAlert.retryAfter(5)`
  (templated) directly -- no string keys, so no typo can silently miss a
  translation the way `t('errorAlert.retyrAfter')` would with a
  string-keyed lookup.

Every component that had a hardcoded English string now takes its text
from `useTranslation()` instead (CLAUDE.md: "Ukrainian only in i18n
files"). The three strings that already hand-rolled English plural logic
(`n === 1 ? '' : 's'`) -- the key-shift-semitones sentence, the
off-pitch-notes piano-roll `aria-label`, and the session-count progress-chart
`aria-label` -- now route through `pluralize`, each language supplying its
own `{one, few, many, other}` forms.

## Consequences

Gets easier: zero new lines in `package-lock.json`, nothing to `npm audit`
for this feature, full type-checking on every translation key including
templated ones (a language file with a wrong function signature fails
`tsc`, not a runtime `t is not a function`). Consistent with how the rest
of the app already handles global-but-not-Context state.

Gets harder: no ICU message format, no automatic namespace splitting, no
translation-management-platform integration (Crowdin/Lokalise-style
round-tripping) if the project ever wants professional translators editing
strings directly -- at that scale, revisit `react-i18next` (or a
key-value + `.po`/`.json` extraction pipeline) rather than continuing to
hand-edit `en.ts`/`uk.ts`. Rich inline text (a sentence with an embedded
`<strong>`) has no templating primitive here; the one case that needed it
(`AnalysisReport.tsx`'s mode-reconciliation sentence) is split into
prefix/middle/suffix string fragments assembled around JSX elements by the
caller, not a single translated string -- fine at this scale, would not
scale past a handful of such sentences.

## Alternatives considered

- `react-i18next` -- rejected for now, same reasoning as ADR-0009: solves
  problems (namespaces, lazy loading, SSR) this app doesn't have, for two
  bundled dictionaries and ~130 total strings. Revisit if a third language,
  professional translators, or per-route lazy-loaded copy ever becomes a
  real requirement.
- A flat `t('some.dotted.key')` string-based lookup (own implementation,
  still no dependency) -- rejected in favor of the nested-object-as-dictionary
  shape specifically for the type safety: a string key is just a string to
  the compiler, so a typo or a renamed key is a runtime miss (silently
  falls back or renders `undefined`) instead of a build failure.
