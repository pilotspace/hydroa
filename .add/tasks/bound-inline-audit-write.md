---
type: Task
title: Bound the inline audit write — bare create_async_engine with no pool or command timeout on every mutation
status: direction
depth: standard
sensitivity: architecture
milestone: release-hardening-p0
gives:
  - S1 <the surface this publishes — an endpoint, function, or section>
generated: { by: add/3.2.0, at: 2026-08-19 }
verified: []
---
## CARD
goal: The inline audit write cannot add unbounded latency to a mutation it records — its engine declares a pool and a command timeout, and a slow or wedged audit store degrades the write path in a bounded, observable way.
why: DIAGNOSIS ON RECORD (unauthored — author before freezing). Required by Tin at the audit-coverage-structural-guard gate 2026-08-19 rather than carried as residue. `record_audit` is INLINE-awaited on every mutating route by design (A5: fire-and-forget loses the read-race, and the write is fail-open), and it runs on its own session from `app.state.sessionmaker` — but that engine is a bare `create_async_engine` with no pool_timeout and no command timeout. So a healthy request path inherits the audit store's worst case: if the audit write blocks, the mutation's response blocks with it, on EVERY audited route (129 of them after the R9 retrofit). Fail-OPEN protects correctness but not latency — the except branch is only reached once the await returns. NOTE the interaction with the new counter: `gateway_audit_write_failed_total` counts a swallowed FAILURE, so a write that merely hangs is invisible to it; whatever bound is chosen must turn a hang into a counted failure rather than a silent stall. Cross-ref the R9 appsec lens residue #4 and [[lifespan-shutdown-must-be-bounded]] — same house rule, different await.
beat: direction · next: add freeze bound-inline-audit-write

## RULES
<must>
- M1 <the rule that must hold>
</must>
<reject>
- R:<NAME> <what must never happen> -> "<NAME>"
</reject>

## ASSUMPTIONS
- A1 [who] covers: <S ids> · the request does not say <who may act / whose data>; taking <reading> -> <cost if wrong>
- A2 [which] covers: <S ids> · the request does not say <which rows/cases are in>; taking <reading> -> <cost if wrong>
- A3 [when] covers: <S ids> · the request does not say <where the boundary falls>; taking <reading> -> <cost if wrong>
- A4 [absent] covers: <S ids> · the request does not say <what a missing value means>; taking <reading> -> <cost if wrong>
- A5 [order] covers: <S ids> · the request does not say <what orders / breaks a tie>; taking <reading> -> <cost if wrong>
- A6 [experience] covers: <S ids> · the request does not say <who receives this and what would make it hard for them>; taking <reading> -> <cost if wrong>
every `gives:` surface is swept on every dimension; `[<dim>] n/a · <why>` retires one. one line, one silence — split, never bundle. `· probe: <what shipped behavior must show>` declares a reading checkable: cite its A id from CHECKS and the gate holds the PASS to it.

## PLAN
contract: <the shape this publishes>
scope: <files>

## EDGES
- E1 <a boundary or failure case a check must cover — optional>

## CHECKS
- <test_name> · covers: M1 · <what it proves>
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
