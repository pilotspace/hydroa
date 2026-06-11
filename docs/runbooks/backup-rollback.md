# Backup and Rollback Runbook

This runbook covers backup procedures, restore drills, Alembic rollback, gateway image rollback,
and secrets handling for the Hydroa (formerly ai-proxy) project.

Compose project name: `hydroa-dev`  
Dev Postgres: `localhost:5433` (container `hydroa-dev-db-1`)  
Dev Redis: `localhost:6380` (container `hydroa-dev-redis-1`)  
Production compose file: `infra/docker-compose.prod.yml`

---

## Scheduled pg_dump backup

### Dev environment

Use the running `db` container from the `hydroa-dev` compose project:

```bash
docker exec hydroa-dev-db-1 \
  pg_dump -U gateway gateway_test \
  | gzip > backups/gateway_test_$(date +%Y%m%dT%H%M%S).sql.gz
```

### Production environment

Postgres runs inside the `infra/docker-compose.prod.yml` stack. Run pg_dump against the
production container:

```bash
# On the production host (or via SSH):
docker exec $(docker compose -f infra/docker-compose.prod.yml ps -q db) \
  pg_dump -U gateway gateway \
  | gzip > /var/backups/gateway_$(date +%Y%m%dT%H%M%S).sql.gz
```

Schedule via cron (`/etc/cron.d/hydroa-backup`):

```
0 2 * * * root docker exec ... pg_dump ... | gzip > /var/backups/...
```

Retain at least 7 daily snapshots. Verify backup files are non-empty after each run:

```bash
gzip -t /var/backups/gateway_*.sql.gz && echo "backup OK"
```

---

## Restore drill

Perform a restore drill against a **non-production** database before relying on a backup
in an incident. Steps:

1. **Spin up an isolated Postgres container:**

   ```bash
   docker run -d --name pg-restore-test \
     -e POSTGRES_USER=gateway \
     -e POSTGRES_PASSWORD=gateway \
     -e POSTGRES_DB=gateway_restore \
     -p 5434:5432 \
     postgres:16
   ```

2. **Restore the backup:**

   ```bash
   gunzip -c backups/gateway_test_<TIMESTAMP>.sql.gz \
     | docker exec -i pg-restore-test \
         psql -U gateway gateway_restore
   ```

3. **Verify row counts match the source:**

   ```bash
   docker exec pg-restore-test \
     psql -U gateway gateway_restore \
     -c "SELECT COUNT(*) FROM usage_records;"
   ```

4. **Tear down the restore container:**

   ```bash
   docker rm -f pg-restore-test
   ```

5. **Document results** — record row counts and any errors in the incident log.

For production restores, stop the gateway service first to prevent split writes:

```bash
docker compose -f infra/docker-compose.prod.yml stop gateway
# restore as above against the production db container
docker compose -f infra/docker-compose.prod.yml start gateway
```

---

## Alembic downgrade rollback

### Per-revision procedure

Alembic migrations live in `apps/gateway/`. The baseline revision is `ad14442336db`.

```bash
cd apps/gateway

# 1. Check current head
uv run alembic current

# 2. View history to identify the target revision
uv run alembic history --verbose

# 3. Downgrade one step (to the previous revision)
uv run alembic downgrade -1

# 4. Or downgrade to a specific revision:
uv run alembic downgrade <revision_id>

# 5. Or downgrade to baseline:
uv run alembic downgrade ad14442336db
```

### Additive-migrations caveat

This project uses an **additive-only** migration convention (see `.add/CONVENTIONS.md`):
all migrations ADD columns, tables, or indexes — they never DROP or ALTER existing columns
in a destructive way. This means:

- Downgrading removes the newly added column/table, which is safe.
- Rolling back a migration that added a `NOT NULL` column requires the previous app version
  to not reference that column (handled by the additive convention: new columns are nullable
  or have a server-side default until the next release).
- Never run `DROP TABLE` or `DROP COLUMN` in a `downgrade()` function without explicit human
  approval — data loss is irreversible.

Always test the downgrade path in dev before applying to production:

```bash
# In dev: upgrade then immediately downgrade to verify symmetry
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

---

## Gateway image rollback

### Identify the previous image tag

Gateway images are tagged by git SHA or semantic version. Find the previous tag:

```bash
# Local docker images
docker images hydroa-gateway --format "{{.Tag}}\t{{.CreatedAt}}" | head -10

# Or via the container registry (example: ghcr.io)
docker image ls ghcr.io/<org>/hydroa-gateway
```

### Roll back the running container

```bash
# 1. Set the previous tag in the compose override or environment
export GATEWAY_IMAGE_TAG=<previous-tag>

# 2. Pull the previous image
docker pull ghcr.io/<org>/hydroa-gateway:${GATEWAY_IMAGE_TAG}

# 3. Restart the gateway service with the previous image
docker compose -f infra/docker-compose.prod.yml \
  up -d --no-deps \
  --scale gateway=1 \
  gateway

# 4. Verify health
curl -sf http://localhost:8000/internal/health/live && echo "gateway live"
curl -sf http://localhost:8000/internal/health/ready && echo "gateway ready"
```

### Graceful drain during rollback

The gateway implements a graceful shutdown drain (`shutdown_drain_timeout_seconds`, default 10s).
Docker Compose / Docker Swarm will send SIGTERM and wait for the container to exit.
Ensure `stop_grace_period` in `infra/docker-compose.prod.yml` is at least 15s to allow
the drain to complete before SIGKILL:

```yaml
services:
  gateway:
    stop_grace_period: 15s
```

---

## Secrets handling

All secrets for the gateway are stored in `apps/gateway/.env` (dev) or injected as
environment variables in production. **This file is never committed to git.**

### Required secrets

| Env var | Purpose | Default (dev only) |
|---|---|---|
| `GATEWAY_JWT_SECRET` | Signs JWT tokens | `dev-only-secret-change-me` (dev/test only) |
| `GATEWAY_OPENROUTER_API_KEY` | OpenRouter API access | empty (required in prod) |
| `GATEWAY_DATABASE_URL` | Postgres connection string | `postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test` |
| `GATEWAY_REDIS_URL` | Redis connection string | `redis://localhost:6380/0` |

### Rules

1. **Never commit `apps/gateway/.env`** — it is in `.gitignore`. If accidentally committed,
   rotate all secrets immediately.

2. **Production `GATEWAY_JWT_SECRET`** — generate with:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
   Store in your secrets manager (e.g. HashiCorp Vault, AWS Secrets Manager, GitHub Secrets).

3. **Connection strings contain credentials** — never log raw `DATABASE_URL` or `REDIS_URL`
   values. The readiness probe (`GET /internal/health/ready`) strips credentials from all
   error detail strings before including them in the response body.

4. **Rotate secrets** — after a suspected compromise, rotate `GATEWAY_JWT_SECRET` first
   (invalidates all existing sessions), then rotate DB/Redis passwords, then redeploy.

5. **CI secrets** — store in the CI provider's secret store (e.g. GitHub Actions secrets),
   never in plaintext in workflow files or `Makefile`.
