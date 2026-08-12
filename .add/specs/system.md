---
type: Spec
title: System
lens: system
project: ai-proxy (Hydroa) — the multi-tenant LLM gateway
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## Now

FastAPI + SQLAlchemy async + Postgres 16 (pgvector 0.8.x) + Redis, `uv`-managed, Python
pinned to a PATCH version. Dashboard: Next.js + Tailwind v4. Clean architecture —
dependencies point inward; every outbound integration sits behind a `typing.Protocol` port
with a fake injected via `app.state`, so tests make zero network calls.

Deployment: Docker Compose (dev/e2e) and a Helm chart; every production image pinned by
digest. Releases build FROM THE TAG, so a tag that predates a fix ships the defect.

## Decisions that bind

- **Design for failure on every IO path** (non-negotiable, CLAUDE.md): timeout, bounded
  retry (idempotent calls only), circuit breaker, and a stated rollback. An unbounded await
  is a defect even when it never fires in tests.
- **Circuit breakers are PER TENANT.** Every new provider surface so far has shipped a global
  breaker first → cross-tenant DoS; HARD-STOPPED twice. Thread the tenant key through the
  port and keep a per-tenant registry.
- **Every shutdown await has a deadline.** `cancel()` is a request, and
  `suppress(Exception)` bounds errors, not duration. One leaked lifespan parked a worker for
  10h23m and printed nothing.
- **A security control that reads a stdlib predicate inherits that stdlib's version
  semantics.** `is_reserved` on `::ffff:10.20.30.40` differs between 3.12.3 and 3.12.4+, so
  the same egress guard reached different verdicts on CI and dev. Pin the interpreter patch
  version at every end, and prefer deciding from the policy's own normalisation.
- **Retrieval recall depends on an index, not just speed.** pgvector's HNSW index covers only
  the vector, so a tenant/store filter is applied AFTER traversal unless the planner picks the
  exact path — which it does only because `ix_vector_store_chunks_store` exists. That index is
  load-bearing for correctness; `hnsw.iterative_scan = strict_order` is the insurance, and
  `ef_search` is not (measured: 1000 changed nothing). Published in
  `docs/runbooks/pgvector-deploy.md` §7.
- **A collation-provider change (musl → glibc) on an existing volume risks silent index
  corruption.** `REFRESH COLLATION VERSION` does not clear it; finish with dump/restore.
- **Exactly-once belongs to the database**, never to in-process state:
  `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id` and CAS status flips.
- **Provider identifiers are validated, never sanitised.** A mangled id promoted to a primary
  key is a catalog row nobody can dial; refuse it and log it.
- **No `${{ }}` interpolation inside a workflow `run:` body** — user-influenced values arrive
  through `env:` and are read as quoted shell variables.
- **Images are published multi-arch with `buildx --platform … --push`.** A bare `docker build`
  on Apple silicon publishes arm64 and fails at deploy with `exec format error`; a plain
  `docker push` uploads one arch. Only `imagetools inspect` proves what was published.

## Deltas
<!-- the inbox: `- [open · <date>] <lesson>` — fold upward into the sections above, then retag [folded] -->
