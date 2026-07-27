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

## Data
- [ ] Schema change ships as a migration, backward compatible one release
- [ ] Indexes cover the new query patterns

## Process
- [ ] Tests cover the new logic and the reported bug
- [ ] openapi.yaml and affected docs updated in this PR
- [ ] ADR present for architectural decisions
- [ ] Commits follow the convention, single author, no attribution trailers
