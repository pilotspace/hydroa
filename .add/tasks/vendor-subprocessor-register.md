---
type: Task
title: vendor-subprocessor-register
status: done
gives:
  - S1 build_register + rendered subprocessor register (CC9.2 vendor risk)
generated: { by: add/3.2.0, at: 2026-08-14 }
verified:
  - { by: "cli", at: 2026-08-14, act: freeze, authority: process, direction: "sha256:a593b6e1b13485e4" }
  - { by: "cli", at: 2026-08-14, act: brief, authority: process, brief: "sha256:a87d0a63eb722c4f" }
  - { by: "process:run", at: 2026-08-14, act: run, authority: process, outcome: PASS, receipt: /tasks/vendor-subprocessor-register.d/runs/1.md }
  - { by: "process:verify", at: 2026-08-14, act: gate, authority: process, outcome: PASS, receipt: /tasks/vendor-subprocessor-register.d/runs/1.md, brief: "sha256:a87d0a63eb722c4f", reason: "10/10 red-first CHECKS green; every referent (M1-M6, probed A2-A6, E1-E8, R:TOOL_WRITES_REGISTER/UNDECLARED_PROCESSOR/ASSUMED_COMPLIANT/FABRICATED_SIGNOFF) bound to a passing test; ruff clean. Pure build_register over UsageSource port with reconciliation: used-but-undeclared processor is a HARD finding; fail-closed on missing field/unsigned DPA (never documented); DPA expiry computed against injected now; IncompleteFetch fails-not-truncates; read+report only, no write path; review record unreviewed-draft; infra (non-customer-data) listed-for-reconciliation not risk-classified." }
---
## CARD
goal: A read+report-only CC9.2 subprocessor register that classifies every declared third-party processor of customer data (documented / incomplete / dpa_expired / unused) AND reconciles the declared list against what the system actually reaches — a used-but-undeclared processor is a HARD finding, never a silent omission.
why: R8 soc2-groundwork task (CC9.2 vendor & subprocessor risk). Third of the standalone technical controls; no recruit dependency. A register that is only a hand-maintained doc is theater — reconciliation against real usage is the first real evidence cycle.
beat: done · next: add status

## RULES
<must>
- M1 The tool is READ + REPORT ONLY. It never edits the declared register (`subprocessors.json`), and never mutates any vendor system — it only reads and classifies. -> "TOOL_WRITES_REGISTER"
- M2 The core is PURE: `build_register(source, declared, *, now_iso)` over a `UsageSource` Port. No clock, no network — `now_iso` is INJECTED. A `UsageSource` that cannot enumerate the WHOLE live processor set raises `IncompleteFetch`; the core NEVER emits a reconciliation from a partial enumeration (else an undeclared processor slips through as "all declared").
- M3 Every declared subprocessor that processes customer data is classified. A missing required field (purpose / region / data_categories) or an unsigned/absent DPA is NEVER `documented` — it fails CLOSED to `incomplete`. -> "ASSUMED_COMPLIANT"
- M4 The declared register is reconciled against the live `UsageSource`: a processor the system actually reaches but which is ABSENT from the declared register is a HARD finding recorded in `undeclared`, never dropped. -> "UNDECLARED_PROCESSOR"
- M5 A DPA whose expiry is on/before the INJECTED `now_iso` is classified `dpa_expired` — never left "valid" by omission; the boundary is computed, never assumed.
- M6 The review record reviewer is `unreviewed — draft` until a real human signs. The tool never fabricates a reviewer. -> "FABRICATED_SIGNOFF"
</must>
<reject>
- R:TOOL_WRITES_REGISTER a code path that writes/edits subprocessors.json or issues any vendor-system mutation -> "TOOL_WRITES_REGISTER"
- R:UNDECLARED_PROCESSOR a live-reached processor absent from the declared register being omitted / silently treated as fine -> "UNDECLARED_PROCESSOR"
- R:ASSUMED_COMPLIANT a vendor missing a required field or a signed in-window DPA being classified `documented` -> "ASSUMED_COMPLIANT"
- R:FABRICATED_SIGNOFF the review record naming any reviewer other than `unreviewed — draft` -> "FABRICATED_SIGNOFF"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say who MAINTAINS the register vs who REVIEWS it; taking: `subprocessors.json` is human-maintained INPUT and the tool is a reviewer-assistant that classifies but never signs (M6) -> if wrong, the tool would appear to author/approve vendor risk, which is exactly the fabrication M6 forbids
- A2 [which] covers: S1 · the request does not say which vendors are in scope; taking: only entries with `processes_customer_data: true` are risk-classified — pure infra that never touches customer data is listed-but-not-classified · probe: a processes_customer_data=false vendor appears in output with NO risk classification -> if wrong, the register drowns real subprocessor risk in undifferentiated infra
- A3 [when] covers: S1 · the request does not say where the DPA-expiry boundary falls; taking: a DPA is expired when `now_iso >= dpa_expiry` (expiry instant is NOT still-valid — fail closed at the boundary) · probe: a DPA with expiry == now_iso classifies `dpa_expired` -> if wrong, a lapsed DPA reads as covered on its expiry day
- A4 [absent] covers: S1 · the request does not say what a missing value means; taking: any missing required field or absent/unsigned DPA means NON-compliant → `incomplete`, never assumed compliant · probe: a vendor with dpa_status other than "signed" is `incomplete`, never `documented` -> if wrong, a blank field silently passes as compliant (R:ASSUMED_COMPLIANT)
- A5 [order] covers: S1 · the request does not say what orders the register; taking: worst-severity first (dpa_expired, incomplete, documented, unused) then vendor name — a total, deterministic order · probe: two builds over identical source+declared+now are byte-identical and severity-ordered -> if wrong, the auditor diff is noisy and non-reproducible
- A6 [experience] covers: S1 · the request does not say who receives this; taking: the reader is an auditor — the summary is payload-free (counts + named findings, no endpoint secrets/tokens) · probe: the rendered summary carries the counts + `unreviewed — draft` and NO token/secret substring -> if wrong, evidence leaks credentials or is unreadable
every `gives:` surface is swept on every dimension. A1 is an unprobed reading (who-maintains); A2–A6 are probe-backed and cited from CHECKS.

## PLAN
contract: >
  Dataclasses (frozen): `Subprocessor(name, purpose, data_categories: tuple[str,...], region,
  processes_customer_data: bool, dpa_status: str, dpa_expiry: str|None)`;
  `UsageRef(name, surface, observed_in)` — a processor the system actually reaches;
  `ReviewedVendor(vendor: Subprocessor, classification, reason, used: bool)`;
  `ReviewRecord(reviewer, generated_window)`;
  `VendorRegister(vendors: tuple[ReviewedVendor,...], undeclared: tuple[UsageRef,...], review,
  n_vendors, n_incomplete, n_expired, n_undeclared)`.
  `Classification = Literal["documented","incomplete","dpa_expired","unused"]`.
  Port `UsageSource.processors(*, org, repo) -> Sequence[UsageRef]` (may raise IncompleteFetch).
  `build_register(source, declared: Sequence[Subprocessor], *, org, repo, now_iso) -> VendorRegister`
  — PURE. `render_summary(register) -> str` and `as_dict(register) -> dict` (payload-free,
  deterministic). IO adapter `ConfigUsageSource` (design-for-failure; live enumeration of provider
  hosts / egress allowlist / storage config = documented NotImplementedError the operator wires).
scope: scripts/soc2/vendor_register.py · scripts/soc2/subprocessors.json · apps/gateway/tests/soc2_vendor_register/test_vendor_register.py

## EDGES
- E1 a processor in the live UsageSource but absent from the declared register → HARD finding in `undeclared` (R:UNDECLARED_PROCESSOR)
- E2 a declared vendor NOT present in live usage → `unused` (flagged, not a hard fail)
- E3 a DPA expired by the injected `now_iso` → `dpa_expired`
- E4 `IncompleteFetch` from the UsageSource → build raises, no partial register emitted
- E5 a vendor missing a required field / dpa_status != "signed" → `incomplete`, never `documented` (fail closed)
- E6 a `processes_customer_data: false` vendor → listed but NOT risk-classified
- E7 identical source+declared+now → byte-identical, severity-ordered output
- E8 the review record stays `unreviewed — draft` until a human signs

## CHECKS
- test_used_but_undeclared_is_hard_finding · covers: M4, E1, R:UNDECLARED_PROCESSOR · a live processor absent from the declared register lands in `undeclared`, counted, never dropped
- test_tool_has_no_write_path · covers: M1, R:TOOL_WRITES_REGISTER · no public symbol writes/edits the register or mutates a vendor; the Port's only method is the read `processors`
- test_missing_dpa_never_documented_fails_closed · covers: M3, A4, E5, R:ASSUMED_COMPLIANT · a vendor with dpa_status != "signed" (or a missing required field) is `incomplete`, never `documented`
- test_expired_dpa_flagged_against_injected_now · covers: M5, A3, E3 · a DPA with expiry on/before now_iso is `dpa_expired`
- test_declared_but_unused_flagged_not_hard · covers: E2 · a declared vendor absent from live usage is `unused` and is NOT counted as undeclared/incomplete/expired
- test_non_customer_data_vendor_listed_not_classified · covers: A2, E6 · a processes_customer_data=false vendor appears but carries no risk classification and is excluded from the risk counts
- test_review_record_unreviewed_until_signed · covers: M6, E8, R:FABRICATED_SIGNOFF · review.reviewer == "unreviewed — draft"
- test_incomplete_fetch_fails_not_truncates · covers: M2, E4 · a UsageSource raising IncompleteFetch propagates; no partial register
- test_build_is_pure_deterministic_and_ordered · covers: M2, A5, E7 · two builds are equal; vendors are worst-severity-first then name
- test_summary_is_payload_free_and_counted · covers: A6 · summary shows the counts + unreviewed-draft and contains no token/secret substring
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
