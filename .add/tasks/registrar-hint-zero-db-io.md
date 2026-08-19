---
type: Task
title: Restore the registrar-hint ZERO-database-IO invariant broken by the revocation guard
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
goal: GET /admin/domain-claims/registrar-hint honours its frozen ZERO-database-IO invariant again, with session revocation still enforced on that route.
why: DIAGNOSIS ON RECORD (unauthored — author before freezing). tests/domain_capture/test_registrar_hint.py::test_registrar_hint_zero_db_io is RED on branch add/auth-hardening-login-sessions. Cause is commit 42224201 (auth-hardening P0-1), the same commit behind catalog-sync-session-autobegin, but a DIFFERENT failure mode: not a transaction-state clash, a query-count clash. _get_owner_identity now issues SELECT revoked_auth_sessions.jti + SELECT users.sessions_not_before on every call, so a route whose shipped contract (domain-capture task, M12) is "zero database IO" now performs two reads per request. Proven independent of the audit retrofit by stashing the domain_claims_router.py edit and re-running — it fails identically. This is a frozen invariant of an ALREADY-SHIPPED task, so the resolution is a genuine design decision and NOT a mechanical fix: either the route is exempted from the revocation guard (weakens a P0 security gate — needs its own justification), or M12's invariant is amended with the human who owns it, or the guard learns a cache/no-IO path. Do not pick one at build time. NOT shipped: 42224201 is not an ancestor of origin/main.
beat: direction · next: add freeze registrar-hint-zero-db-io

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
