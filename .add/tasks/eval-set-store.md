---
type: Task
title: eval-set-store
status: direction
milestone: evals-regression-gate
gives:
  - S1 the eval-set / eval-case persistence contract + the ZDR disposition — the frozen case shape every downstream task hangs off
generated: { by: add/3.2.0, at: 2026-08-12 }
verified: []
advised_by: appsec-engineer
---
## CARD
goal: tenant-scoped eval sets + cases, with the ZDR disposition decided and enforced atomically with the write
why: the freeze-first, risk-first foundation — an eval case is a persisted request payload (the ZDR HARD-STOP surface), and every other task assumes an answer to how a case is stored
beat: direction · next: add freeze eval-set-store
> ZDR disposition SETTLED 2026-08-12 (Tin): **refuse a ZDR tenant outright** — an eval-case write by a ZDR tenant is rejected at repository entry with 403 `ERR_ZDR_PAYLOAD_BLOCKED`, atomic with the write, matching the 8 existing payload stores. Assertion-only/payload-hash mode is a later-milestone follow-up, not R7.

## RULES
<must>
- M1 An eval set is tenant-owned metadata (name + optional description) scoped to `tenant_id`; create returns the set with a server-assigned id. A set carries NO request payload — only a case does.
- M2 An eval case belongs to exactly one set of the SAME tenant and stores a `request_body` (the OpenAI-shaped request to replay) plus one deterministic `assertion` (scorer kind + expected); both are tenant-scoped.
- M3 An eval-case write by a ZDR tenant is refused with 403 `ERR_ZDR_PAYLOAD_BLOCKED`, and the check is ATOMIC with the write — `raise_if_zdr_locked` (SELECT … FOR UPDATE, the shared primitive in `tenants/application/retention_policy.py`) is the first statement inside the transaction that commits the case, so a flip landing mid-await persists NOTHING. (The set-create path — payload-free — uses no ZDR gate.)
- M4 Every read/list is tenant-scoped, and a cross-tenant id is INDISTINGUISHABLE from an absent one — uniform 404, never an enumeration oracle (the carried tenant-isolation invariant; a live oracle was closed in #84).
- M5 A case create names its parent set by id; a set that is absent OR owned by another tenant is refused identically as `ERR_EVAL_SET_NOT_FOUND` (404) — the ownership check is in the SAME query that resolves the set.
- M6 The store is reached through a `typing.Protocol` port (`EvalSetRepository`) with a zero-network fake injected via `app.state`; the SQLAlchemy repository is the adapter wired at the composition root. A use-case orchestrates the create; the router never calls the repository directly. (backend-architect lens)
- M7 A ZDR tenant CAN create an eval SET (metadata-only, no payload) — only the CASE write (which persists `request_body`) is refused. The gate is on payload-at-rest, not on evals-as-a-feature; over-blocking the set would be a correctness defect. (appsec lens)
</must>
<reject>
- R:ZDR_BLOCKED a ZDR tenant writes an eval case (payload at rest) -> "ERR_ZDR_PAYLOAD_BLOCKED"
- R:SET_NOT_FOUND a case create names an absent or cross-tenant set -> "ERR_EVAL_SET_NOT_FOUND"
- R:CASE_INVALID a case with an empty/absent `request_body` or an empty/absent `assertion` -> "ERR_EVAL_CASE_INVALID"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say which roles may author sets/cases; taking "any authenticated member of the owning tenant may author; superadmin only via impersonation, never cross-tenant" -> if wrong, an over-broad or over-narrow authz surface that a later task inherits. · probe: a member of tenant A can create a set+case under A; a member of tenant B gets 404 for A's set (A5→CHECKS test_cross_tenant_set_uniform_404).
- A2 [which] covers: S1 · the request does not say which assertion kinds a case may carry at store time; taking "the store accepts ANY well-formed assertion object (kind + expected) and does NOT validate the kind against the scorer registry — deterministic-scorers owns kind validation at run time" -> if wrong, either the store couples to the scorer task (breaks the parallel wave) or an unscoreable kind is stored silently. · probe: a case with `{kind: "exact", expected: "..."}` stores; an unknown kind is NOT rejected here (documented as scorer-owned).
- A3 [when] covers: S1 · the request does not say whether the ZDR flag is read once or per write; taking "fresh per case write, locked (`raise_if_zdr_locked`) — never cached across cases in a batch" -> if wrong, a flag flip between two cases in one submission leaks a payload (the exact TOCTOU class HARD-STOPPED three times). · probe: A slow double flipping ZDR between the lock and the insert persists zero rows (CHECKS test_zdr_case_write_refused_atomically).
- A4 [absent] covers: S1 · the request does not say what a missing assertion or empty request_body means; taking "both are required; absent/empty/whitespace -> ERR_EVAL_CASE_INVALID (422), never a silently stored unscoreable case" -> if wrong, a case that can never produce a verdict pollutes every downstream run. · probe: a case with `assertion: null` and one with `request_body: {}` both 422.
- A5 [order] covers: S1 · the request does not say how cases within a set are ordered; taking "creation order, exposed via a monotonic `created_at` + stable id tiebreak; no user-defined reordering in R7" -> if wrong, a run's per-case results and the console diff have no stable order to align against a baseline. · probe: listing a set's cases returns them in creation order across two calls.
- A6 [experience] covers: S1 · the request does not say what a ZDR tenant sees when refused; taking "the 403 body names ZDR as the reason and the fact that evals require payload storage, so the operator can act (disable ZDR for this tenant, or wait for assertion-only mode) rather than see a bare 403" -> if wrong, a ZDR operator hits an unactionable wall. · probe: the 403 body carries `ERR_ZDR_PAYLOAD_BLOCKED` and a human-readable reason.

## PLAN
contract:
```
POST /v1/evals/sets            body: { name, description? }
  201 -> { id, name, description, created_at }
  4xx -> { error: "ERR_VALIDATION" }                       # name absent/blank
POST /v1/evals/sets/{set_id}/cases   body: { request_body: {…}, assertion: { kind, expected } }
  201 -> { id, eval_set_id, created_at }                    # NOTE: response echoes NO payload back
  4xx -> { error: "ERR_ZDR_PAYLOAD_BLOCKED"                 # 403, ZDR tenant, atomic with write
                 | "ERR_EVAL_SET_NOT_FOUND"                 # 404, absent OR cross-tenant set
                 | "ERR_EVAL_CASE_INVALID" }                # 422, empty request_body or assertion
GET  /v1/evals/sets                 -> [ {id,name,description,created_at,case_count} ]   # tenant-scoped
GET  /v1/evals/sets/{set_id}/cases  -> [ {id, assertion, created_at} ]  # tenant-scoped, creation order; request_body NOT listed by default
Schema (new module `evals/`, both tables tenant-scoped, append-only for cases):
  eval_sets(id uuid pk, tenant_id uuid fk, name text, description text null, created_at timestamptz)
    — unique (tenant_id, name)
  eval_cases(id uuid pk, tenant_id uuid fk, eval_set_id uuid fk, request_body jsonb, assertion jsonb, created_at timestamptz)
    — index (tenant_id, eval_set_id, created_at)
Access: reads filter tenant_id in the SAME query that resolves the row (M4/M5). Case create:
  `async with sessionmaker() as s: await raise_if_zdr_locked(s, tenant_id); <resolve set owned by tenant>; s.add(case); await s.commit()`
```
Target (measurable): the 6 red CHECKS below go green; the ZDR slow-double test asserts on the PERSISTED ROW (0 rows), not the response; cross-tenant + absent set both return byte-identical 404. Boot/wiring confirmed by the router mounting under the app and the new tables migrating clean.
scope (may touch): `apps/gateway/src/gateway/evals/` (new: domain/entities.py · domain/ports.py · application/use_cases.py · infrastructure/repository.py · api/router.py) · `apps/gateway/src/gateway/core/error_catalog.py` (new ERR_EVAL_SET_NOT_FOUND · ERR_EVAL_CASE_INVALID) · a new Alembic migration under `apps/gateway/migrations/` · `apps/gateway/src/gateway/main.py` (mount the router) · `apps/gateway/tests/evals/`
regression floor: the full gateway suite (`make ci`) stays green; no change to any existing payload store or `retention_policy.py`.
least-sure flag: [spec] whether case reads should EVER echo `request_body` back (I default to no, to keep the payload write-only from the API's perspective) — a console per-case diff (evals-console) may need it, which would widen the read contract then.

## EDGES
- E1 ZDR flag flips between the lock and the insert (slow double) — zero rows land; assert on the row, not the 403.
- E2 A case create names a set owned by another tenant — 404 `ERR_EVAL_SET_NOT_FOUND`, identical to an absent set (never 403).
- E3 An empty/whitespace `assertion` or an empty `request_body` — 422 `ERR_EVAL_CASE_INVALID`, nothing stored.
- E4 A duplicate set name for the same tenant — rejected by the unique constraint (ERR_VALIDATION), not a silent second set.
- E5 A ZDR tenant creates a set, then a case — the set succeeds (201, no payload), the case is refused 403; over-blocking the set would be the defect. (appsec lens)

## CHECKS
- test_create_set_is_tenant_scoped · covers: M1, M6 · a set created by tenant A is listed for A and absent for B — driven through the injected fake port (zero network), which proves the port+fake exist and work.
- test_create_case_persists_payload_and_assertion · covers: M2, M6 · a case stores request_body + assertion under the right set/tenant; re-fetch after commit proves the write survived the session boundary (backend-architect: mutation persists past the session).
- test_zdr_case_write_refused_atomically · covers: M3, R:ZDR_BLOCKED, E1 · a slow double flips zdr_enabled mid-await between the lock and the insert; the write raises 403 and the persisted eval_cases count for that tenant is 0.
- test_zdr_tenant_can_create_set_but_not_case · covers: M7, E5 · a ZDR tenant's set create returns 201 and persists a row; the subsequent case create returns 403 ERR_ZDR_PAYLOAD_BLOCKED and persists nothing.
- test_cross_tenant_and_absent_set_uniform_404 · covers: M4, M5, R:SET_NOT_FOUND, E2 · a case create against another tenant's set and against a random uuid both return byte-identical 404 ERR_EVAL_SET_NOT_FOUND.
- test_case_requires_request_and_assertion · covers: R:CASE_INVALID, E3 · a case with empty request_body and one with null assertion both 422, and nothing is stored.
red-first: every check MUST fail first (the `evals/` module does not exist yet — all six are red at authoring).

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
