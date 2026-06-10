# CONVENTIONS.md

## Repository layout

```
apps/gateway/      FastAPI service (src layout: src/gateway/, tests/)
apps/dashboard/    Next.js admin dashboard
infra/envoy/       Envoy edge config
infra/             docker-compose, deployment manifests
playbook/          ADD methodology prompts (1_specify … 6_observe)
features/          Gherkin scenarios (per feature)
contracts/         Frozen interface contracts (per feature)
PRD/ inputs/       Product inputs read by playbook/1_specify.md
scripts/           Pipeline helper scripts
```

## Python (apps/gateway)

- Python **3.12+**, managed with **uv**; all deps declared in `pyproject.toml`
  and present in `dependencies.allowlist` (CI rejects others).
- Layout: `src/gateway/` package. Modules by domain, not by layer:
  `proxy/`, `tenants/`, `auth/`, `usage/`, `core/` (config, db, errors).
- **Async everywhere** on the request path. No blocking IO in handlers.
- Typing: full annotations; `mypy --strict` must pass. Pydantic v2 models for
  all request/response shapes; SQLAlchemy 2.0 typed ORM (`Mapped[...]`).
- Lint/format: `ruff check` + `ruff format` (line length 100).
- Errors: every Reject rule in a SPEC gets a named error code
  (`ERR_<DOMAIN>_<REASON>`), returned as RFC 9457 problem+json.
- **Failure design is mandatory** for every outbound IO call: explicit
  timeout, bounded retry with jitter (idempotent ops only — never retry a
  non-idempotent upstream completion), circuit breaker on OpenRouter,
  and structured log on every failure path.
- Logging: `structlog`, JSON output, always bind `tenant_id` and `request_id`.
- File size: keep modules under ~700 lines; split by domain when approaching.

## Database

- All tenant-owned tables carry `tenant_id`; every query is tenant-scoped.
- Migrations: Alembic, additive/backward-compatible; each migration documents
  its rollback. Usage ledger is append-only — no UPDATE/DELETE.
- IDs: UUIDv7 primary keys. Timestamps: `timestamptz`, UTC only.

## TypeScript (apps/dashboard)

- Next.js App Router, TypeScript `strict`. shadcn/ui components owned in-repo;
  Tremor for charts; TanStack Query for server state; Zod-validated API client.
- Naming: components `PascalCase`, hooks `useCamelCase`, files `kebab-case.tsx`.

## Testing (red/green TDD — required)

- Every feature starts at `playbook/4_tests.md`: tests written and **red**
  before implementation. Build phase makes them green without weakening them.
- Gateway: `pytest` + `pytest-asyncio` + `httpx.ASGITransport`; contract tests
  pin frozen contracts; coverage target recorded per feature (floor 80%).
- Never assert on internals; assert on observable behavior.

## Git

- Conventional commits with project trailer (see user format):
  `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer.
  Message drafted in `tmp/*.txt`, committed via `git commit -F`.
- Scopes: `gateway`, `dashboard`, `infra`, `playbook`, `docs`, `pipeline`.
- Contracts are immutable once FROZEN; changing one reopens Specify.

## Pipeline gates (every change)

1. `ruff check` + `ruff format --check`
2. `mypy --strict`
3. `python scripts/check_allowlist.py` — unknown packages fail the build
4. `pytest` with coverage floor
