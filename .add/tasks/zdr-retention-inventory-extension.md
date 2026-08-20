---
type: Task
title: Extend ZDR/retention purge to vector stores, evals, finetune + structural inventory guard
status: done
depth: deep
sensitivity: data
milestone: release-hardening-p0
scope:
  - apps/gateway/src/gateway/usage
  - apps/gateway/src/gateway/vector_stores
  - apps/gateway/src/gateway/evals
  - apps/gateway/src/gateway/finetune
  - apps/gateway/src/gateway/tenants
  - apps/gateway/tests
gives:
  - S1 retention_sweep._sweep_zdr_purge_pass — purges the five missing payload tables (vector_store_chunks, eval_cases, eval_case_results, finetune_jobs, finetune_job_events) for zdr_enabled tenants, driven by ONE declared inventory tuple it actually consumes
  - S2 finetune write path ZDR gate — entry raise_if_zdr in create_job + raise_if_zdr_locked atomic with the finetune_jobs/finetune_job_events insert (today finetune has ZERO ZDR gating, unlike vector_stores/evals which are already locked-rechecked)
  - S3 structural inventory guard test — walks Base.metadata.tables; any table carrying tenant_id + a payload-shaped column (Text/JSONB/Vector) must sit in the consumed inventory or on a NAMED exemption list; RED on today's tree naming all five victims
generated: { by: add/3.2.0, at: 2026-08-18 }
verified:
  - { by: "Tin Dang", at: 2026-08-19, act: freeze, authority: human, direction: "sha256:4dbc95a9e0ee4e07" }
  - { by: "cli", at: 2026-08-19, act: brief, authority: process, brief: "sha256:ea0aa8698bfe5f94" }
  - { by: "process:run", at: 2026-08-19, act: run, authority: process, outcome: PASS, receipt: /tasks/zdr-retention-inventory-extension.d/runs/1.md }
  - { by: "Tin Dang", at: 2026-08-19, act: gate, authority: plan, outcome: PASS, receipt: /tasks/zdr-retention-inventory-extension.d/runs/1.md, brief: "sha256:ea0aa8698bfe5f94" }
advised_by: appsec-engineer
---
## CARD
goal: A ZDR tenant's vector-store, eval, and finetune payloads are actually purged by the sweeper, the finetune write path is ZDR-gated at all (it currently is not), and a structural guard makes the next TENANT-SCOPED payload table impossible to silently omit (tenant_id-carrying — child tables without one inherit their parent's purge, see A5).
why: Deep-review P0 (artifact 6816985f): the ZDR purge pass enumerates 9 tables by hand and silently misses the three newest stores; recon confirmed finetune additionally has no write-side ZDR gate anywhere (`finetune/api/router.py:170` → `use_cases.py:97` → `repository.py:38` all unguarded), so a ZDR tenant's hyperparameters/error text/event payloads persist at rest today.
beat: done · next: add status

## RULES
<must>
- M1 One sweep_once() for a zdr_enabled tenant leaves ZERO rows in vector_store_chunks, eval_cases, eval_case_results, finetune_jobs, and finetune_job_events for that tenant (seed → enable ZDR → sweep → zero across all five).
- M2 The ZDR purge inventory is ONE declared tuple (retention_policy.py, beside ALL_SWEPT_TABLES) that _sweep_zdr_purge_pass CONSUMES to build its per-table deletes — the guard and the sweeper read the same object, never two parallel lists.
- M3 The structural guard walks Base.metadata.tables and FAILS, naming each victim, for any table with a tenant_id column and a payload-shaped column (Text/JSONB/Vector) that is neither in the consumed inventory nor on the named exemption list; it is RED on the pre-fix tree naming exactly the five tables above.
- M4 The finetune write path is ZDR-gated end to end: entry raise_if_zdr before any provider/persist work in create_job, AND raise_if_zdr_locked as the LAST statement before the transaction that persists finetune_jobs / finetune_job_events commits — after the provider await returns, never before it, so the tenants row lock is never held across an outbound round-trip. A zdr_enabled flip that lands AFTER the entry check still rolls the persist back.
- M5 Nothing pre-existing weakens: the current purge tables, the per-tenant window pass, and metadata containers (vector_stores, vector_store_files, eval_sets, eval_runs, eval_baselines) are untouched by the new deletes; a non-ZDR tenant's rows in all five tables survive byte-for-byte; and the three BLOB-AWARE purgers (artifacts, files, compliance_report_runs) keep their object-store semantics exactly — delete the blob first, DEFER the row when the store is unreachable (retention_sweep.py:763-767), never collapse into a bare DELETE.
</must>
<reject>
- R:TOCTOU_WRITE a ZDR check separated from the finetune persist by an await without an atomic locked re-check in the writing transaction ([[zdr-toctou-async-write-paths]] — HARD-STOPPED twice) -> "TOCTOU_WRITE"
- R:SECOND_INVENTORY the guard asserting against a list the sweeper does not itself consume (a green guard over a dead tuple) -> "SECOND_INVENTORY"
- R:SILENT_EXEMPTION exempting a payload-shaped table from the guard without a named (table, reason, task-citation) row -> "SILENT_EXEMPTION"
- R:CASCADE_RELIANCE counting on FK ON DELETE CASCADE from a container delete instead of an explicit per-table purge statement (a re-parented or denormalized row escapes) -> "CASCADE_RELIANCE"
- R:BLOB_ORPHAN driving the blob-backed tables (artifacts, files, compliance_report_runs) through a generic row-DELETE built from the new tuple — the object key lives ON the row, so deleting it while the object store is unreachable strands the bytes where NO later tick can ever find them -> "BLOB_ORPHAN"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1, S2, S3 · the request does not say whose data beyond "a ZDR tenant"; taking: selection stays tenants.zdr_enabled = true exactly as the existing pass (retention_sweep.py:414) — no per-store opt-outs, superadmin/tenant alike -> if wrong, a selection knob is a new feature, not a rewrite
- A2 [which] covers: S1 · the request names "three stores", not tables; taking: the five payload-bearing tables only — metadata containers (vector_stores, vector_store_files, eval_sets, eval_runs, eval_baselines) carry ids/names/status and SURVIVE · probe: the sweep check asserts containers remain while payloads zero -> if wrong, one inventory row per table to add/remove
- A3 [which] covers: S1 · the request does not say row-delete vs column-scrub for finetune_jobs; taking: wholesale DELETE (the video_generation_jobs precedent — the existing ZDR pass deletes whole job rows) · probe: zero finetune_jobs rows after sweep -> if wrong (job history wanted), payload-column scrub is a redesign, separate task
- A4 [when] covers: S1 · the request does not say when purge lands after enabling ZDR; taking: the next sweeper tick (existing cadence, main.py:795), not synchronous-on-enable -> if wrong, an on-enable hook is additive
- A5 [absent] covers: S1, S3 · the request does not say what a table WITHOUT tenant_id means to the purge; taking: outside the GUARD's population — it is tenant_id-scoped, so a payload-bearing CHILD without its own tenant_id (conversation_messages carries role/content Text and no tenant_id) stays covered by its parent's existing purge + FK cascade, which is accepted for tables ALREADY purged that way but is explicitly not the mechanism for the five new ones (R:CASCADE_RELIANCE) -> if wrong, a join-linked population is a follow-up guard, additive
- A6 [order] covers: S1 · the request does not say purge order across the five tables; taking: any order, each table its own statement inside the existing per-tenant fail-open pass — child tables (chunks, events, case_results) deleted explicitly, never via cascade (R:CASCADE_RELIANCE) -> if wrong, ordering only matters under FK pressure the explicit deletes avoid
- A7 [absent] covers: S2 · the request does not say whether finetune's MISSING gate is in scope or only the sweeper; taking: in scope — purging at rest while the write path stays open is a leaky bucket, and finetune is the only store with no gate at all · probe: the entry-gate check refuses a ZDR tenant's create with 403 ERR_ZDR_PAYLOAD_BLOCKED -> if wrong, scope shrinks by one seam
- A8 [which] covers: S2 · the request does not say which finetune writes are gated; taking: the CREATE path only (create_job + its event rows). Status polling and cancel are NOT read-only — get_job writes apply_poll_result/add_event (use_cases.py:225-241) and cancel_job inserts an event (:299) — but they mutate a job that predates the ZDR flip, which the sweeper purges wholesale on the next tick (A3/A4); gating them would strand in-flight jobs mid-lifecycle · probe: the create checks assert zero rows; poll/cancel are deliberately unasserted -> if wrong, gating them is additive and the sweeper already bounds the exposure to one tick
- A18 [which] covers: S1 · the request does not say that every inventory table purges the same WAY; taking: the tuple names WHICH tables are purged, not HOW — the three blob-backed tables (artifacts, files, compliance_report_runs) keep their existing object-store-aware purgers unchanged and the twelve plain tables get the tenant_id-scoped DELETE (R:BLOB_ORPHAN) · probe: with a recording object-store double, a ZDR sweep still calls delete per blob row and DEFERS the row when the store raises -> if wrong, one dispatch branch, not a rewrite
- A9 [when] covers: S2 · the request does not say where the refusal boundary falls in time; taking: zdr_enabled AT COMMIT TIME decides — re-read under lock after the provider await returns, never the value cached at entry · probe: the check starts its flip only AFTER the provider double reports the request is inside submit, so an entry-only locked check cannot pass it -> if wrong, the entry check alone is the exact TOCTOU we were HARD-STOPPED for twice
- A10 [order] covers: S2 · the request does not say check-vs-work order at entry; taking: entry raise_if_zdr BEFORE any provider call or row insert in create_job (mirror evals launch, run_executor.py:104) -> if wrong, a refused tenant still cost a provider round-trip
- A11 [which] covers: S3 · the request does not define "payload-bearing" for the guard; taking: structurally derived — tenant_id column AND any Text/JSONB/Vector column — with named exemptions for payload-shaped-but-deliberately-kept tables (audit_events is compliance evidence, retained by design; tenants itself carries config JSONB) · probe: the guard is RED on the pre-fix tree naming exactly the five victims and no false positives survive un-exempted -> if wrong, the population debate happens at gate with the victim list in hand
- A12 [absent] covers: S3 · the request does not say what an exemption means; taking: a named (table, reason, task-citation) row in the guard source — a bare table name is itself a violation (R:SILENT_EXEMPTION) -> if wrong, ceremony trims to (table, reason)
- A13 [when] covers: S3 · n/a · the guard is a test — it runs on every CI run by existing collection; no runtime boundary of its own
- A14 [who] covers: S2 · n/a · actor authz on finetune routes is untouched — this task adds a data-policy gate, not an authz change (settled by the existing route dependencies)
- A15 [experience] covers: S1, S2 · the request does not say what the refused/purged tenant sees; taking: the finetune refusal is the EXISTING 403 ERR_ZDR_PAYLOAD_BLOCKED problem shape (same as files/evals — no new error surface); purge itself is silent, reportable via the existing sweeper logs -> if wrong, a dedicated purge report is UDD follow-up
- A16 [order] covers: S3 · n/a · the guard's verdict is set-membership — victim output sorted only for stable failure messages, no ordering semantics
- A17 [experience] covers: S3 · the request does not say what a failing guard tells the next engineer; taking: the assertion message lists each missing table AND points at the inventory tuple + exemption convention (the upload-bounds sweep precedent) -> if wrong, the guard still fails, just ruder
every `gives:` surface is swept on every dimension; `[<dim>] n/a · <why>` retires one. one line, one silence — split, never bundle. `· probe: <what shipped behavior must show>` declares a reading checkable: cite its A id from CHECKS and the gate holds the PASS to it.

## PLAN
contract: ONE new tuple ZDR_PURGE_TABLES (retention_policy.py, beside ALL_SWEPT_TABLES) = existing nine + the five victims; _sweep_zdr_purge_pass drives its per-tenant purge from that tuple, DISPATCHING per table — the three blob-backed names keep their existing object-store-aware purgers, the rest get the tenant_id-scoped DELETE (R:BLOB_ORPHAN); finetune gains entry raise_if_zdr in application/use_cases.create_job plus raise_if_zdr_locked as the last statement before that create's transaction commits (post-provider-await, so no lock is held across the round-trip); new tests/retention_zdr_inventory/ suite carries the guard + sweep + finetune-gate + blob-semantics checks. No schema change — no migration, four-manifest lesson does not trigger.
scope: apps/gateway/src/gateway/tenants/application/retention_policy.py · apps/gateway/src/gateway/usage/application/retention_sweep.py · apps/gateway/src/gateway/finetune/application/use_cases.py · apps/gateway/src/gateway/finetune/infrastructure/repository.py · apps/gateway/tests/retention_zdr_inventory/

## EDGES
- E1 the same sweep that zeroes a ZDR tenant's five tables leaves a non-ZDR tenant's rows in those tables byte-for-byte intact
- E2 the guard's failure output names each missing table (pre-fix: exactly the five)
- E3 a ZDR tenant's finetune create is refused 403 ERR_ZDR_PAYLOAD_BLOCKED with zero rows persisted and zero provider calls
- E4 zdr_enabled flipped true AFTER the entry check passed → the post-await locked re-check still refuses the persist (zero rows)
- E5 the object store is unreachable during a ZDR purge of a blob-backed table → the row is DEFERRED, never deleted-without-its-blob

## CHECKS
- test_zdr_sweep_purges_all_three_stores · covers: M1, M2, M5, A1, A2, A3, A4, A6, E1, R:CASCADE_RELIANCE · seed all five tables for a zdr tenant AND a non-zdr tenant (plus their containers) → one sweep_once() → zero rows for the zdr tenant in all five, containers and the non-zdr tenant's rows intact; the pass's table set is asserted to BE ZDR_PURGE_TABLES (same object the sweeper imports); child rows are re-checked gone even where no container was deleted (no cascade reliance)
- test_inventory_guard_red_names_victims · covers: M3, A5, A11, A12, A17, E2, R:SECOND_INVENTORY, R:SILENT_EXEMPTION · Base.metadata walk (tenant_id + Text/JSONB/Vector) diffed against the tuple the SWEEPER imports; exemptions must be (table, reason, citation) rows; RED today with a message naming exactly vector_store_chunks, eval_cases, eval_case_results, finetune_jobs, finetune_job_events
- test_finetune_create_blocked_for_zdr_tenant · covers: M4, A7, A8, A10, A14, A15, E3 · POST the finetune create as a zdr_enabled tenant → 403 ERR_ZDR_PAYLOAD_BLOCKED problem body, zero finetune_jobs/finetune_job_events rows, zero provider calls; a non-ZDR tenant on the same app still creates
- test_finetune_zdr_flip_mid_await_refused · covers: M4, A9, E4, R:TOCTOU_WRITE · the flip starts only AFTER the provider double reports the request is inside submit — so the entry check has already passed on zdr=false and an entry-only gate (even a locked one) cannot save it; only the post-await locked re-check refuses. Zero rows at rest, 403 out
- test_blob_backed_purge_keeps_object_store_semantics · covers: M5, A18, E5, R:BLOB_ORPHAN · with a recording object-store double, a ZDR sweep over a blob-backed table (artifacts/files) calls delete for each row's object key, and when the store RAISES the row is DEFERRED (still present, retryable next tick) rather than deleted — the new tuple must not have collapsed these into a bare DELETE
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
