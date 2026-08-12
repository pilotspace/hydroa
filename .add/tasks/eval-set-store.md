---
type: Task
title: eval-set-store
status: direction
milestone: evals-regression-gate
gives:
  - S1 the eval-set / eval-case persistence contract + the ZDR disposition — the frozen case shape every downstream task hangs off
generated: { by: add/3.2.0, at: 2026-08-12 }
verified: []
---
## CARD
goal: tenant-scoped eval sets + cases, with the ZDR disposition decided and enforced atomically with the write
why: the freeze-first, risk-first foundation — an eval case is a persisted request payload (the ZDR HARD-STOP surface), and every other task assumes an answer to how a case is stored
beat: direction · next: add freeze eval-set-store
> ZDR disposition (refuse a ZDR tenant outright vs. assertion-only/payload-hash cases) is an OPEN product decision with a HARD-STOP attached — settle it with the human BEFORE drafting the frozen contract, per the milestone risk note.

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
