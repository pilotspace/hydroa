# Deploying a pgvector-bearing release

Applies from **0.13.0** onward, the first release whose schema carries a `vector(1536)`
column (`vector_store_chunks.embedding`, migration `55dc3f920a38`).

Two things changed at once in that release, and only one of them is loud:

1. The schema now needs the `vector` extension. A target that lacks it fails the
   migration **loudly** — `CREATE EXTENSION IF NOT EXISTS vector` is the single
   provisioning choke point, and a failed migration is a stopped deploy. This is the
   good case.
2. The Postgres image moved from `postgres:16-alpine` (musl libc) to
   `pgvector/pgvector:pg16` (Debian, glibc). A data volume created under the old image
   and served by the new one has its text indexes ordered by musl's collation and every
   query comparing under glibc's. **This one is silent.**

Read §1 before any upgrade that reuses an existing volume or restores an existing dump.

---

## 1 · Preflight — always run this first

```bash
make pg-preflight DATABASE_URL='postgresql://user:pass@host:5432/dbname'
# or directly:
python3 scripts/pg_preflight.py --database-url 'postgresql://...' --json
```

The exit code is the contract; the text is for humans.

| Exit | Status | Meaning | What to do |
| --- | --- | --- | --- |
| `0` | **OK** | The recorded collation version matches what the OS reports. | Proceed with the deploy. |
| `1` | **FAIL** | Either no version was ever recorded (the musl lineage), or the recorded one differs from the current provider. Text indexes no longer match the collation in use. | Stop. Apply the remedy in §4 **before** serving traffic. |
| `2` | **UNKNOWN** | Could not reach the database, authentication was refused, or the server does not report a collation version. | Stop. This is *not* a pass — see below. |

**Why UNKNOWN is not a pass.** Postgres warns about a collation-version mismatch only
when it has a recorded version to compare against. Databases created under musl have
`datcollversion` set to SQL `NULL`, so there is nothing to compare and **no warning is
ever emitted**. "We saw no warnings" is therefore not evidence of anything. UNKNOWN
exists so that "could not check" can never be reported as "checked and fine".

On UNKNOWN: confirm the host, port and credentials, confirm the server is Postgres 15 or
newer, then re-run. If a managed provider genuinely does not expose
`pg_database_collation_actual_version()`, record that fact against the provider before
proceeding — do not assume it means OK.

---

## 2 · In-cluster Postgres (the StatefulSet PVC)

`charts/ai-proxy` runs Postgres as a StatefulSet with a PersistentVolumeClaim
(`datastores.postgres` in `values.yaml`). Bumping the chart's image **keeps the PVC** —
which is exactly the dangerous path: same volume, new libc.

```bash
kubectl -n <ns> get statefulset <release>-postgres \
  -o jsonpath='{.spec.template.spec.containers[0].image}'      # what is serving today
kubectl -n <ns> exec -it <release>-postgres-0 -- \
  psql -U gateway -d gateway -c \
  "SELECT datcollversion AS recorded, pg_database_collation_actual_version(oid) AS actual
     FROM pg_database WHERE datname = current_database()"
```

- `recorded` empty/NULL while `actual` is populated → the volume predates the image
  change. Apply §4.
- Both populated and equal → safe to roll the image forward.

Take a backup first (`docs/runbooks/backup-rollback.md`) regardless of which case you
are in.

## 3 · Managed Postgres

`docs/runbooks/cloud-deploy.md` targets a managed Kubernetes cluster (EKS / GKE / AKS)
with either in-cluster or managed datastores. For a managed Postgres instance:

1. **The extension must be installable.** `CREATE EXTENSION IF NOT EXISTS vector` runs
   as part of migration `55dc3f920a38` under the application's migration role. If that
   role cannot create extensions, the deploy stops there with a permission error.

   > **UNVERIFIED — provider privilege requirements.** On AWS RDS/Aurora, creating
   > extensions is commonly restricted to a role in `rds_superuser`, and pgvector must
   > additionally appear in that engine version's supported-extensions list; equivalent
   > restrictions exist on Cloud SQL and Azure Database. **None of this has been
   > verified against our actual target**, because the target provider has not been
   > chosen and recorded yet. Do not treat the paragraph above as fact.
   >
   > What would settle it, in one step, before this runbook claims anything:
   > ```bash
   > psql "$DATABASE_URL" -c "SHOW rds.extensions"        # or the provider equivalent
   > psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector"
   > ```
   > Record the provider, engine version, role and result here, then delete this block.

2. **Run the §1 preflight against the managed instance**, not only against a local
   copy. A provider that does not populate `datcollversion` reports UNKNOWN, and you
   need to know that before an incident, not during one.

3. **A restore into a managed instance crosses lineages by definition** — the dump came
   from somewhere else. Treat it as §4's dump/restore case.

---

## 4 · Remedy when the preflight FAILs

Pick by whether the data stays on the same cluster.

### 4a · Same cluster, same volume → REINDEX

The collation changed under the existing files, so the indexes must be rebuilt:

```bash
psql "$DATABASE_URL" -c "REINDEX DATABASE \"$PGDATABASE\";"
psql "$DATABASE_URL" -c "ALTER DATABASE \"$PGDATABASE\" REFRESH COLLATION VERSION;"   # ONLY after the REINDEX above
```

The `REFRESH` is the **last** step and only ever after a successful `REINDEX` — see §5.

`REINDEX DATABASE` takes locks and can run long on large tables. Use
`REINDEX (CONCURRENTLY) DATABASE` on Postgres 12+ if you cannot take the downtime, and
expect it to take substantially longer.

**If it is interrupted** (pod evicted, session killed, timeout): a plain `REINDEX` is
transactional per index — an interrupted run leaves the old index in place, so the
database is no worse than before; re-run it. A `REINDEX CONCURRENTLY` that is
interrupted can leave behind invalid indexes; find and drop them, then re-run:

```sql
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
-- DROP INDEX CONCURRENTLY <name>;   -- for each, then REINDEX again
```

Do **not** run the `REFRESH COLLATION VERSION` step if the REINDEX did not complete —
that is precisely how a half-fixed database starts reporting itself healthy.

### 4b · Moving to a new lineage (new volume, new provider, restore) → dump/restore

```bash
pg_dump --format=custom "$SOURCE_URL" > gateway.dump
createdb --template=template0 --lc-collate=en_US.utf8 --lc-ctype=en_US.utf8 gateway_new
pg_restore --dbname="$TARGET_URL" --no-owner gateway.dump
python3 scripts/pg_preflight.py --database-url "$TARGET_URL"     # must exit 0
```

`--template=template0` matters: it creates the database with the collation you name
rather than inheriting whatever `template1` carries on that server.

**If it is interrupted:** the target database is partial and must be treated as
discarded — `dropdb` it and start again. Never point the gateway at a partially
restored database; the migration will appear to succeed against a subset of tables.
The source is untouched throughout, so rollback is "keep using the source".

---

## 5 · The trap: `REFRESH COLLATION VERSION` is not a fix

Searching the collation-mismatch warning text leads quickly to:

```sql
ALTER DATABASE mydb REFRESH COLLATION VERSION;   -- NOT a remedy on its own
```

This records the *current* collation version against the database. It rebuilds **no
index**. Run on its own, it removes the warning and leaves every index still sorted by
the old collation — converting a problem Postgres was telling you about into one it no
longer mentions. Wrong `ORDER BY` results and unique constraints that miss duplicates
continue, now silently.

It is legitimate only as the final step of §4a, after a successful `REINDEX`.

`scripts/pg_preflight.py` never issues it, and never issues any statement other than
`SELECT` — guarded by
`apps/gateway/tests/pgvector_deploy/test_pgvector_deploy.py::test_preflight_only_ever_executes_select`.

---

## Related

- `docs/runbooks/backup-rollback.md` — backups, the restore drill, Alembic rollback
- `docs/runbooks/cloud-deploy.md` — applying the chart to a managed cluster
- `apps/gateway/migrations/versions/55dc3f920a38_vector_store_core.py` — the migration
  that introduces the extension and the `vector(1536)` column
