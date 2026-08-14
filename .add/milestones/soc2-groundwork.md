---
type: Milestone
title: SOC 2 groundwork — internal readiness (1.0.0)
status: direction
generated: { by: add/3.2.0, at: 2026-08-14 }
verified:
  - { by: "cli", at: 2026-08-14, act: freeze, authority: process, direction: "sha256:75a11da44c802486" }
---
## CARD
goal: bring Hydroa to internal SOC 2 readiness — the technical controls a Type I audit samples are RUNNING and produce evidence, so an auditor engagement can start with a defensible posture rather than a scramble
why: R8 lead, Tin-approved 2026-08-14 (AskUserQuestion ×3: broad scope = change-mgmt + access + evidence-export PLUS the enterprise-trust items pentest/SLO/security-debt that R7 skipped when evals came forward; NO independent reviewer recruited yet; NO auditor engaged — internal readiness only). SOC 2 is real within months and drives the release ([[soc2-audit-date-is-real-driver]]). The single hardest thing to defend under CC8.1 has been that every merge since 0.8.0 landed on evidence that tests ran — and, more recently, that a SECOND HUMAN reviewed. Branch protection now enforces both required checks AND one approval on the merged artifact, but the approval has been satisfied by a disclosed self-approval via `pilotspacex-byte` (the auditor sample set #117/#118/#199–#206), which is an honest documented GAP, not a control. This milestone makes the sampled controls genuinely run. **1.0.0 is HELD for a real auditor attestation — this milestone delivers readiness, never the attestation and never the 1.0.0 cut.**
next: add freeze independent-review-control

## SCOPE
In:  The TECHNICAL, auditor-sampled controls and their FIRST real evidence cycle — not just a
     written procedure. (1) **Genuine independent review** (CC1.4/CC8.1 segregation of duties):
     a real second human with write access reviewing PRs, and the byte-approval path retired or
     gated behind disclosure. (2) **Access review + least-privilege** (CC6.1–6.3): every
     principal with GitHub org/repo, ghcr, prod-datastore, or secret access enumerated,
     justified, stale grants removed, on a periodic cadence — INCLUDING the shared-keyring
     `pilotspacex-byte` situation. (3) **Change-management evidence export** (CC8.1): automated
     proof that required `ci`+`dashboard` ran on the MERGED sha, with the approver, for any
     window — now possible because CI reaches a verdict. (4) **External penetration test**
     (CC4.1/CC7.1): a real external test, every finding fixed or risk-accepted with an owner.
     (5) **SLOs + incident runbooks** (CC7.2–7.5): SLOs defined, monitored, each with a
     detect→respond→recover→post-mortem runbook; the pgvector deploy runbook WALKED on a real
     target; the `kind-e2e` masked gate resolved. (6) **Security-debt closure** (#27/#31/#51 +
     the masked-gate residue), each dual-adversarially-verified. (7) **Vendor/subprocessor
     register** (CC9.2): every upstream provider with its data-flow, retention, and per-vendor
     ZDR/residency posture. (8) **Internal readiness assessment**: the delivered controls mapped
     to the Common Criteria with per-criterion ready/gap+remediation.
Out: **The SOC 2 audit itself** — auditor engagement, the Type I observation window, and Type II
     (which needs a months-long window) are the follow-on once a firm is selected. **The 1.0.0
     cut** — held for a real attestation; internal readiness does NOT authorize the tag. **Full
     GRC/policy authorship** — the formal policy corpus, risk-assessment methodology, HR/vendor
     legal agreements, and org-chart controls are a legal/GRC track, not this engineering
     milestone (this is the "+ enterprise-trust items" scope, not the full CC1–CC9 sweep). **A
     compliance product feature** (customer-facing SOC 2 report portal) — later, if ever.

## GROUND
touches:
  - `.github/` — branch protection (already `enforce_admins: true`, checks `["ci","dashboard"]`, `required_approving_review_count: 1`), workflow evidence, CODEOWNERS.
  - the immutable audit store + `usage_records` — existing evidence trail a compliance export reads.
  - the green 4-shard CI (`ci` aggregates the shards + coverage) — the merged-artifact evidence that only became possible in R6.
  - `docs/runbooks/` — the deploy + incident runbooks; the pgvector collation runbook still unwalked on a real target.
  - the observability/alerting stack — SLO wiring, alert routes (today one global sink, no per-tenant webhooks per the roadmap audit).
  - GitHub org/repo access, ghcr `packages:write`, prod datastore + secret grants — the access-review surface.
anchors: branch protection is REAL and enforced (proven by an HTTP 405 refused admin-merge — [[masked-gate-never-reached-a-verdict]]); the disclosed byte-approval tally is the honest baseline, NOT a control; the immutable audit store already exists; a control is proven by being demonstrated LIVE (a genuine approval, a real access removal, an alert that fires), never by reading its config back.
risks:
  - **The four-eyes evidence is a disclosed GAP, and closing it depends on a human this team does not yet have.** It is the single most-sampled control. `independent-review-control` cannot be closed by process or tooling alone — it needs a real second person with write access to actually review. The milestone must NOT pretend to close it by writing a procedure; the exit is gated on the recruit, and until then byte-approval stays a disclosed gap ([[masked-gate-never-reached-a-verdict]]: an undisclosed self-approval is strictly WORSE than the documented gap — never let the record become untrue to look closed).
  - **A readiness milestone drifts into theater.** The failure mode is a folder of documents describing controls that never actually run — the compliance analogue of a masked gate. Every control here ships with its FIRST real evidence cycle or it does not count: a genuine independent approval, an access review that actually removes/justifies a grant, an alert that fires in a drill, the pgvector runbook walked on a real target. Same discipline as [[guard-must-be-red-against-its-motivating-tree]] — demonstrate the control against the thing it exists to catch.
  - **Pentest finding = HARD-STOP.** An external finding is a security finding; it is fixed or explicitly risk-accepted with an owner and a date — never waived to hit a readiness milestone. Do not risk-accept a live exploit.
  - **Scope creep into 1.0.0.** Attestation is external and an auditor's to give. Delivering readiness does NOT authorize the 1.0.0 tag; tagging 1.0.0 off internal readiness alone would be the exact overclaim this milestone exists to prevent. 1.0.0 waits for a real Type I attestation.

## EXIT
- [ ] Genuine independent review is established and evidenced: a real second human with write access exists, and the last 5 merges to `main` carry an APPROVED review from that distinct person — not a byte-approval; the self-approval path is retired or gated behind mandatory disclosure   (← independent-review-control)   (verify: 5 consecutive PRs show `reviewDecision: APPROVED` by a GitHub identity confirmed to be a second human; `pilotspacex-byte` self-approval disabled or blocked; sample the review authors — this exit CANNOT be met by tooling if no reviewer was recruited, which is the honest signal)
- [ ] Every principal with org/repo, ghcr, prod-datastore or secret access is enumerated, least-privileged, and a periodic access-review has run once with a dated record   (← access-review-and-least-privilege)   (verify: an access register lists every write/admin principal with a justification; at least one stale grant removed OR explicitly justified; the shared-keyring `pilotspacex-byte` disposition is decided and recorded; a dated review artifact exists)
- [ ] The change-management trail exports as auditor evidence: for any window, each merge to `main` maps automatically to the green `ci`+`dashboard` run ON THE MERGED sha and a named approver   (← change-management-evidence-export)   (verify: run the export over the last release window; every merge resolves to a required-check run against its own merge commit and an approver identity; a merge with no green merged-sha run is flagged, not hidden)
- [ ] An external penetration test has been run and every finding is fixed or risk-accepted with an owner; no open HIGH/critical   (← external-pentest)   (verify: the report exists; each finding has a disposition; a real finding was HARD-STOPPED and fixed, not waived to a date)
- [ ] SLOs are defined and monitored, each with a detect→respond→recover→post-mortem runbook; the pgvector deploy runbook is WALKED on a real target; `kind-e2e` is green or honestly retired   (← slo-and-incident-runbooks)   (verify: an alert fires against a real SLO breach in a drill; the pgvector runbook walk is recorded on a real volume; no gate that has never reached a verdict is still counted as passing)
- [ ] Tracked security debt is closed or explicitly risk-accepted, each dual-adversarially-verified   (← security-debt-closure)   (verify: #27/#31/#51 + the masked-gate residue each closed with a test RED against its own defect, or a dated waiver with an owner)
- [ ] A vendor/subprocessor register documents every upstream provider with its data-flow, retention, and per-vendor ZDR/residency posture   (← vendor-subprocessor-register)   (verify: every provider the gateway can dial appears with a data-flow + retention note; ZDR/residency posture stated per vendor)
- [ ] An internal readiness assessment maps the delivered controls to the SOC 2 Common Criteria (CC6/CC7/CC8/CC9 at minimum) with per-criterion ready/gap+remediation — explicitly NOT an attestation   (← readiness-assessment)   (verify: the report covers each criterion; every open gap has an owner; the report states in plain text that 1.0.0 remains held pending a real auditor attestation)

## CLOSE
evidence: <one row per task at ship — gate · tests/evidence · residue>
sequencing: recruit-first. `independent-review-control` LEADS because it is the load-bearing dependency AND the one gated on something outside engineering (a human to recruit) — start it first so the recruiting clock runs while the rest builds. `access-review`, `change-management-evidence-export` and `vendor-subprocessor-register` run concurrently (documentation + tooling over existing config + the audit store). `external-pentest` runs mid-milestone against a stable target. `slo-and-incident-runbooks` ∥ `security-debt-closure` are engineering tasks that parallel. `readiness-assessment` is LAST — it synthesizes everything and states the honest gap list. Rejected: readiness-assessment first (would describe controls that don't yet run — theater); pentest last (a finding late is a finding you cannot fix before the milestone claims done).
release: NO product release cuts here. **1.0.0 is HELD for a real auditor attestation** (Tin's standing call) — this milestone delivers internal readiness, not the attestation and not the tag. When a firm is engaged and a Type I attestation is given, THEN 1.0.0 is cut as a separate decision. Until `independent-review-control` closes, every merge — INCLUDING this milestone's own tasks — still owes a genuine second human; the byte-approval disclosure continues on each PR and the tally keeps growing until a real reviewer exists.
