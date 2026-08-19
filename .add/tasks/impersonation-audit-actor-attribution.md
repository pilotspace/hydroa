---
type: Task
title: Audit rows drop the real superadmin behind an impersonated identity — qualify or close the CC6 claim
status: direction
depth: standard
sensitivity: security
milestone: release-hardening-p0
gives:
  - S1 <the surface this publishes — an endpoint, function, or section>
generated: { by: add/3.2.0, at: 2026-08-19 }
verified: []
---
## CARD
goal: An audit row written under an impersonated identity names the REAL acting superadmin, not only the impersonated target — or the CC6 evidence claim is explicitly qualified to say it does not.
why: DIAGNOSIS ON RECORD (unauthored — author before freezing). Found by the appsec lens on audit-coverage-structural-guard (2026-08-19), which returned SAFE-TO-GATE overall: this is PRE-EXISTING, not introduced by that build, so it did not block its gate. When a superadmin impersonates a tenant user, every audit row records the IMPERSONATED identity's actor and drops the real acting human — across roughly 40 emitters, not just the ones retrofitted in R9. Partly mitigated today because impersonation START and END are themselves audited, so a reviewer can bracket the window and infer who acted; that inference is not the same as attribution ON the row, and it degrades badly under concurrent or nested sessions. It is frozen in place by A24 of the audit task, so it could not be fixed there. WHY IT MATTERS BEYOND CORRECTNESS: R8 soc2-groundwork makes a CC6 access-review claim that leans on audit rows identifying the actor. Either the rows carry the real actor, or the claim must say plainly that they do not — shipping an unqualified claim over evidence that cannot support it is the failure mode to avoid. Decide WHICH at direction: add actor fields for the impersonator (touches ~40 emitters + the evidence envelope + the export projections, and the envelope just grew three fields in R9), or qualify the CC6 control text and record the bracketing procedure as the compensating control. Cross-ref [[soc2-groundwork-milestone]].
beat: direction · next: add freeze impersonation-audit-actor-attribution

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
