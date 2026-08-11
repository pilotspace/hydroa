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
# or directly — note `uv run python`, NOT a bare `python3`:
cd apps/gateway && uv run python ../../scripts/pg_preflight.py --database-url 'postgresql://...' --json
```

⚠ A bare `python3 scripts/pg_preflight.py` fails with `ModuleNotFoundError: No module named
'sqlalchemy'` unless the project venv is active. It fails *safely* — the result is UNKNOWN
(exit 2), never a false OK — but the `reason` then reads "could not reach or query the
database", which sends you hunting for a network or credentials problem that does not exist.
Read the `reason` field before trusting the diagnosis.

The exit code is the contract; the text is for humans.

⚠ **`make pg-preflight` does not preserve the exit code.** `make` exits **2** on any recipe
failure, so a FAIL (script exit 1) and an UNKNOWN (script exit 2) are indistinguishable
through `make` — and 2 means UNKNOWN in the table below, whose remedy ("check host, port and
credentials") is the wrong action for a FAIL. Both outcomes mean *stop*, so nothing unsafe
follows from this, but **never branch automation on `make pg-preflight`'s exit code.** Call
the script directly when the code matters, and read `status:` in the output when it does not.

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
newer, then re-run. If the server genuinely does not expose
`pg_database_collation_actual_version()`, record that fact before proceeding — never
assume UNKNOWN means OK.

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

## 3 · Managed Postgres is not a supported target

**The in-cluster StatefulSet in `charts/ai-proxy` is the only supported Postgres for
this deployment** (Tin, 2026-07-30). Managed Postgres — RDS, Aurora, Cloud SQL, Azure
Database — is out of scope, and this section exists so that its absence reads as a
decision rather than as a gap somebody forgot to fill.

Two things would have to be settled before any of them could be added, and neither has
been:

- **Extension provisioning.** Migration `55dc3f920a38` runs `CREATE EXTENSION IF NOT
  EXISTS vector` under the migration role. Every managed provider gates extension
  creation differently — through a role, a server parameter, or an allow-list — and
  each gate would need to be checked against a live instance, not inferred.
- **Collation lineage on a platform we do not control.** §1's preflight reads
  `pg_database_collation_actual_version()`. A provider that does not populate it reports
  UNKNOWN, and UNKNOWN would need a documented meaning per provider.

An earlier draft of this runbook named the elevated role one provider is commonly said
to require for extension creation, marked UNVERIFIED. It has been removed, deliberately
including the role name, so that no reader can copy a privilege requirement nobody here
checked. While a target was still undecided
that marker was an honest hedge; now that the answer is "there is no managed target", the
same words would be an invitation to act on unchecked guidance about a platform nobody
here runs. If a managed target is ever adopted, verify it against the live instance and
write what you observed — do not restore that paragraph from memory.

---

## 4 · Remedy when the preflight FAILs

Pick by whether the data stays on the same cluster.

### 4a · Same cluster, same volume → REINDEX

> ⚠ **Read this before choosing §4a.** Walking this runbook on 2026-08-10 established that
> §4a **cannot finish** on the musl-lineage case — which is the case §2 tells you to expect,
> and the one this whole document is about. `REINDEX` works and genuinely repairs the index
> ordering (verified: `amcheck` fails before it, passes after). But the second step **errors**:
>
> ```
> ERROR:  invalid collation version change
> ```
>
> Postgres refuses to move `datcollversion` from SQL `NULL` to a value this way. The recorded
> version therefore stays `NULL`, so `pg_preflight.py` keeps returning **FAIL** forever and the
> database never reaches a state that reports itself healthy.
>
> **If the preflight said `recorded_version: None`, go to §4b.** §4a alone leaves you with
> correct indexes and a permanently failing check. Use §4a's `REINDEX` only when you need the
> indexes usable *now* and cannot yet schedule the dump/restore.
>
> Where §4a *does* fully apply: a genuine version CHANGE, e.g. `2.31` recorded and `2.36`
> reported after a base-image glibc bump. There the `REFRESH` succeeds, because there is a
> recorded version to move from.

The collation changed under the existing files, so the indexes must be rebuilt:

```bash
export PGDATABASE=gateway          # the example below needs this set; it is not implied
psql "$DATABASE_URL" -c "REINDEX DATABASE \"$PGDATABASE\";"
psql "$DATABASE_URL" -c "ALTER DATABASE \"$PGDATABASE\" REFRESH COLLATION VERSION;"   # ONLY after the REINDEX above
```

The `REFRESH` is the **last** step and only ever after a successful `REINDEX` — see §5. If it
errors with `invalid collation version change`, that is the NULL case above: the REINDEX still
counts, but finish with §4b.

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

### 4b · Dump/restore into a database created under the NEW libc

Applies to a new volume, a new provider or a restore — **and to the same-volume
musl→glibc case**, where it is the only remedy that finishes (see the note in §4a). The
earlier framing of this section as "moving to a new lineage" undersold it: this is the
primary remedy, not the exotic one.

```bash
pg_dump --format=custom "$SOURCE_URL" > gateway.dump
createdb --template=template0 --lc-collate=en_US.utf8 --lc-ctype=en_US.utf8 gateway_new
pg_restore --dbname="$TARGET_URL" --no-owner gateway.dump
make pg-preflight DATABASE_URL="$TARGET_URL"      # must report status: OK
```

`--template=template0` matters: it creates the database with the collation you name
rather than inheriting whatever `template1` carries on that server.

**Why this works where §4a cannot:** `CREATE DATABASE` records the collation version *as of
now*, so a database created while the server runs glibc gets `datcollversion` populated
(`2.36` = the reported version) instead of the `NULL` the musl-era database is stuck with.

**The new database may live on the same cluster and the same volume.** You do not need new
hardware, a new PVC or a new provider — `createdb` on the server you are already running is
enough, because it is the *creation time* that matters, not the storage. Verified in the §6
rehearsal.

⚠ `template0` and `template1` on a musl-created cluster keep `datcollversion = NULL`
permanently, and so does the old database. That is expected and harmless; only the database
the gateway connects to needs to be clean. Scope any verification query with
`AND datname = current_database()` or it will report those templates forever.

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

## 6 · Rehearsal record — 2026-08-10

This runbook had never been walked. It has now been, end to end, against a genuine
musl-lineage volume rather than a described one. Everything in §4 above that carries a ⚠ came
out of this rehearsal; none of it was visible from reading the document.

**Setup — a real 0.12.x-era volume, not a simulation.** `postgres:16-alpine` (musl) on a fresh
volume → `alembic upgrade c7e0a4b2d9f1` (the revision immediately before the vector migration,
52 tables) → 2 001 tenants and 3 000 users, with emails spanning punctuation and accents
(`ann`, `a-nn`, `a.nn`, `a_nn`, `änn`, `ånn`, `a nn`), which is where musl's codepoint ordering
and glibc's `en_US.utf8` weighting actually disagree. Then the same volume served by
`pgvector/pgvector:pg16` (Debian, glibc).

**Findings, in order of severity:**

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | §4a cannot finish on the musl case — `REFRESH COLLATION VERSION` errors | `ERROR: invalid collation version change`; `datcollversion` stays `NULL`; preflight keeps saying FAIL |
| 2 | §4b works, and works on the **same** cluster/volume | restored DB: `recorded 2.36 = actual 2.36`, preflight `status: OK`, exit 0 |
| 3 | The documented `python3 scripts/...` invocation does not run | `ModuleNotFoundError: sqlalchemy` → UNKNOWN, reported as "could not reach the database" |
| 4 | `make pg-preflight` collapses FAIL into make's exit 2 (= UNKNOWN) | script exit 1 vs make exit 2, same run |

**The silent-failure claim in §2 is confirmed, not assumed.** After the image swap Postgres
emitted **zero** collation warnings (`docker logs | grep -ci collation` → 0) while
`recorded=NULL, actual=2.36`. A healthy-looking database with genuinely broken indexes.

**The corruption is real, and provable.** Before the remedy:

```
ERROR:  item order invariant violated for index "users_email_key"
DETAIL:  Lower index tid=(3,2) (points to index tid=(18,1)) higher index tid=(3,3) ...
```

After `REINDEX`, every btree index in `public` passes `bt_index_check(..., true)`. So §4a's
REINDEX is not optional busy-work — it is what actually repairs the data — it simply cannot
also clear the recorded-version state.

**Final state after §4b:** preflight `status: OK` / exit 0 · `datcollversion = actual = 2.36` ·
all btree indexes pass amcheck · the scoped mismatch query returns **0 rows**.

**Still not established by this rehearsal, and not claimed:** no managed-provider target was
tested (§3 says managed Postgres is unsupported, so provider privilege for `CREATE EXTENSION`
remains unverified), and no Kubernetes StatefulSet/PVC path was exercised — §2's `kubectl`
steps are still un-walked. This was a local Docker rehearsal on a production-*shaped* dump,
which is what the exit criterion asks for, and it is not the same as a production dry-run.

---

## Related

- `docs/runbooks/backup-rollback.md` — backups, the restore drill, Alembic rollback
- `docs/runbooks/cloud-deploy.md` — applying the chart to a managed cluster
- `apps/gateway/migrations/versions/55dc3f920a38_vector_store_core.py` — the migration
  that introduces the extension and the `vector(1536)` column
