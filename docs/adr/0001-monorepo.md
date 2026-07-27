# ADR-0001: Monorepo instead of three repositories

- Status: Accepted
- Date: 2026-07-27

## Context

The Go API, Python ML worker and React frontend all evolve around one shared
contract (`api/openapi.yaml`) and are built by a single developer. A change
that touches the contract usually touches at least two of the three
components in the same logical unit of work.

## Decision

One repository holds `api/`, `worker/`, `web/`, `deploy/` and `docs/`.

## Consequences

A single PR can change the contract and its implementation (and generated
client) atomically, and CI gates on the whole system at once. The cost is a
larger repository where Go/Python/Node tooling must coexist without
clobbering each other's caches or artifacts -- handled entirely through
`.gitignore`, not process.

## Alternatives considered

- Three repositories (api/worker/web) with the contract published as a
  versioned package -- rejected: release/versioning overhead disproportionate
  to a one-person team, and risks the exact contract drift section 12.1's DRY
  rule forbids.
- Repo-per-service with git submodules -- rejected: submodules are a
  well-known source of solo-developer friction (stale pointers) for no
  benefit here.
