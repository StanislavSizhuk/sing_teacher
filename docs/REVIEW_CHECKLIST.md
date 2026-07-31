## Security
- [ ] Input validated at the boundary; no raw user data in paths or SQL
- [ ] No secrets, tokens or PII in code, logs or tests
- [ ] External binaries called with argument lists, with timeouts
- [ ] AuthZ checked: the user can only touch their own resources

## Design
- [ ] Dependencies injected, code depends on interfaces
- [ ] No duplicated business rules across layers or languages
- [ ] One responsibility per type; no logic in handlers
- [ ] Errors wrapped with context, never swallowed
- [ ] Stage declares its `modes` (M3, spec 12.3); no `if mode ==` branching
      inside a stage to decide whether it runs at all -- that decision
      belongs to `PipelineRunner`/`stage.modes`, not the stage's own `run()`

## Data
- [ ] Schema change ships as a migration, backward compatible one release
- [ ] Indexes cover the new query patterns

## Correctness of scoring (M3, spec 6.14/6.15)
- [ ] Unavailable aspects are `null` with a reason, never `0`
- [ ] Weights profile (`clean_v1`/`mixed_v1`) stored with the analysis
- [ ] Key shift applied only under the documented conditions (spec 6.8)

## Process
- [ ] Tests cover the new logic and the reported bug
- [ ] openapi.yaml and affected docs updated in this PR
- [ ] ADR present for architectural decisions
- [ ] Commits follow the convention, single author, no attribution trailers
