# CONVENTIONS  (living documentation — set once, kept for the whole project)

Language/framework: Python 3.12 / FastAPI (gateway) · TypeScript / Next.js 15 (dashboard)
Folders: monorepo — `apps/gateway` (src layout: `src/gateway/`, `tests/`),
         `apps/dashboard`, `infra/envoy`, `scripts/`; ADD task state in
         `.add/tasks/<slug>/TASK.md` (production code lives in `apps/`, not under `.add/`)
Naming: Python `snake_case` files/functions, `PascalCase` classes; TS components
        `PascalCase`, hooks `useCamelCase`, files `kebab-case.tsx`; modules by
        domain (`proxy/`, `tenants/`, `auth/`, `usage/`, `core/`), not by layer
Lint/format: `ruff check` + `ruff format` (line 100) + `mypy --strict`, enforced in CI (`make ci`)
Errors: machine-readable codes `ERR_<DOMAIN>_<REASON>` (string enums), returned as
        RFC 9457 problem+json — never free text
Architecture: CLEAN ARCHITECTURE per domain module — `domain/` (entities, ports
        as Protocols, domain errors; zero framework imports) ← `application/`
        (use cases orchestrating ports) ← `infrastructure/` (SQLAlchemy/argon2/
        jwt/httpx adapters implementing ports) ← `api/` (FastAPI routers, DTOs,
        dependency wiring). Dependencies point INWARD only; `core/` is the shared
        kernel (config, problem+json errors, ids, db base). Composition root =
        `main.create_app`. Stateless gateway behind Envoy; async-only request path; every
        outbound IO has timeout + bounded jittered retry (idempotent ops only —
        never retry a non-idempotent completion) + circuit breaker on OpenRouter;
        Postgres via SQLAlchemy 2 async + Alembic (additive migrations, rollback
        documented); usage ledger append-only; all tenant data `tenant_id`-scoped
Testing: red/green TDD mandatory — tests red before build; pytest + pytest-asyncio +
        httpx.ASGITransport; assert observable behavior, never internals; coverage floor 80%
        Folded from v1 (2026-06-10):
        - red must be red for the RIGHT reason — verify the failure mode (missing
          implementation, not a test bug) before freezing; a wrong-reason red invalidates
          the gate (evidence: budgets suite called a non-existent token method)
        - security-sensitive failure paths get byte-identical responses across ALL failure
          modes, enforced by dedicated tests (anti-enumeration/oracle; evidence: api-keys
          authz suite drove always-run hash comparison even for unknown rows)
        - UI red suites (vitest/RTL) scope every text/role assertion with
          `within(<section>)` and name the owning component — bare `getByText` string or
          regex matchers over a whole page over-constrain the build when data repeats
          across sections (evidence: dashboard-usage duplicate-match collisions)
Dependencies: every package in `.add/dependencies.allowlist`; CI gate
        (`scripts/check_allowlist.py`) rejects unknown packages
        Folded from v1 (2026-06-10): the allowlist governs PYTHON packages only — node
        dependencies are governed by the committed lockfile + orchestrator review at the
        freeze/gate seams until the allowlist format is extended (open follow-up for v2)
Build/harness conventions folded from v1 (2026-06-10):
        - the contract-freeze flag ritual includes a cross-artifact consistency pass
          (spec vs GLOSSARY vs prior frozen contracts) — it caught the argon2-vs-SHA-256
          conflict before any code existed
        - when frozen test files conflict with lint/format rules, suppress at config level
          (pyproject per-file-ignores or format excludes) — frozen tests are never edited
          to satisfy tooling
Git: `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer; message
        drafted in `tmp/*.txt`, committed via `git commit -F`; scopes: gateway,
        dashboard, infra, docs, pipeline, config
