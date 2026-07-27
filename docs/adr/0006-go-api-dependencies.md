# ADR-0006: Go API dependency choices for the E1 skeleton

- Status: Accepted
- Date: 2026-07-27

## Context

Spec 5.4 fixes the API's layer structure (`domain`/`service`/`repository`/
`transport`) but not the specific libraries. Routing, Postgres access, Redis
access, JWT, password hashing and Google OAuth/OIDC all need a concrete
choice, and switching any of them later means touching every handler or
repository that uses them.

## Decision

- Router: `go-chi/chi` -- `net/http`-compatible middleware, no framework
  routing/binding conventions to work around.
- Postgres: `jackc/pgx/v5` (`pgxpool` at runtime; the `stdlib` adapter only to
  hand goose a `*sql.DB` for migrations).
- Migrations: `pressly/goose` (spec-mandated).
- Redis: `redis/go-redis/v9`.
- Passwords and verification codes: `alexedwards/argon2id`, a thin wrapper
  over `golang.org/x/crypto/argon2` (pure Go).
- Access tokens: `golang-jwt/jwt/v5`.
- Google sign-in: `golang.org/x/oauth2` + `coreos/go-oidc/v3` for OIDC
  discovery and ID-token verification.

## Consequences

Every dependency is pure Go -- no cgo anywhere in the tree -- which is what
makes a fully static, distroless production image possible (spec 5.1's "one
static binary"). Each library does exactly one job; none replaces `net/http`
or introduces an ORM.

## Alternatives considered

- `database/sql` + `lib/pq` -- rejected: pgx has better `context.Context`
  support throughout and is faster, while its `stdlib` shim still
  interoperates with goose where needed.
- Gin/Echo -- rejected: both bring their own routing/binding conventions
  where `net/http` + chi already satisfy everything spec 12.2 asks for.
- Hand-rolled JWT or argon2 -- rejected: cryptographic primitives are exactly
  where not reinventing the implementation pays off; both chosen libraries
  are small, focused, and widely used.
