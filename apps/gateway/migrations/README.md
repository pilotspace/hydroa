# Migration Policy

## Tooling

Alembic manages the schema for the gateway service. Always run migrations against a
fresh empty database before merging to main.

## Rules

1. **Additive-only columns** — new columns must have a server_default or be nullable so
   that existing rows remain valid without a data migration step.
2. **One migration per change** — every ORM model change must be accompanied by a
   corresponding Alembic revision committed in the same PR.
3. **Rollback documented per revision** — every `downgrade()` function must carry a
   plain-English comment describing what data (if any) will be lost.
4. **No destructive DDL without documentation** — `DROP COLUMN`, `DROP TABLE`, and
   `ALTER TYPE` operations must include a `DATA-LOSS WARNING` in the revision docstring
   and must be reviewed by a second engineer before merging.
5. **Parity gate** — CI runs `make migrate-check` after tests; a non-empty autogenerate
   diff fails the build. If you add a column to an ORM model you must also generate a
   migration (`uv run alembic revision --autogenerate -m "<description>"`).

## Workflow

```bash
# Apply all pending migrations (dev / CI)
make migrate

# Check for drift between ORM metadata and the current DB schema
make migrate-check

# Generate a new migration after an ORM change
cd apps/gateway && uv run alembic revision --autogenerate -m "describe the change"

# Roll back to a specific revision
cd apps/gateway && uv run alembic downgrade <rev>

# Roll back everything (DESTRUCTIVE — dev / CI only)
cd apps/gateway && uv run alembic downgrade base
```

## Production

In production the schema is managed exclusively by `alembic upgrade head`.
The `create_all` bootstrap in `main.py` is guarded to `environment in ("dev", "test")`
and will never run when `GATEWAY_ENVIRONMENT=production`.
