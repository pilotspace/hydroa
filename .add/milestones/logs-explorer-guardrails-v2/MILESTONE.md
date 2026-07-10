# MILESTONE: Request Logs Explorer + Guardrails v2

goal: A tenant admin can opt into PII-scrubbed request/response capture, explore and replay logged calls from the console, and enforce per-key guardrail policies with ML moderation, output schema validation, and guardrail analytics.
rationale: new-major — "cluster 2: Trust & Observability" of the Tin-approved 2026-07-10 SaaS-gateway roadmap (approved twice: at the 4-cluster roadmap decision and re-confirmed at the Claude-vs-Hydroa competitive review, "both in parallel" with enterprise-identity-compliance). A new theme no active milestone covers: today `usage_records.raw` holds only token frames (no payload store exists), guardrail policies are tenant-level only, and there is no ML moderation, no output-schema validation, and no guardrail analytics.
stage: production · status: active · created: 2026-07-10T12:17:06+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  (1) opt-in per-tenant/per-key request+response payload capture, PII-scrubbed through the EXISTING guardrail masking engine before persist, size-capped, wired into the existing retention sweeper; (2) a tenant-admin logs query API (time/model/key/status/cost filters, pagination, single-log detail); (3) a console Logs Explorer page — filterable table, detail drawer (request/response/metadata/guardrail verdicts), replay-into-chat-playground; (4) per-KEY guardrail policy resolution layered over the tenant policy; (5) an ML moderation layer as a new guardrail check class (provider-backed, default-off, honest-degradation on provider failure); (6) opt-in output JSON-schema validation with a bounded retry (an explicit, recorded SUPERSESSION of the v11 "translate-don't-enforce" pin — opt-in only, byte-identical when off); (7) guardrail analytics — per-policy/per-key hit counts + a dashboard analytics view.
Out: log export to S3/OTel (P2 backlog) · request coalescing · anomaly detection · evals · unifying semantic+vector cache · per-tenant alert webhooks · any change to default-path billing or routing. ZDR interplay: the sibling milestone's Zero-Data-Retention mode must override capture opt-in (fail-closed: ZDR tenant ⇒ no payload rows ever) — the capture store task freezes that hook here.

UI/UX in scope (Logs Explorer page + guardrail analytics view): follows the Aurora design system + UDD design-definition loop (design.md) per Tin's standing polished-UI bar — information architecture = master table + detail drawer pattern (mirrors the existing audit/alerts views), WCAG 2.2 AA floor, signature element = the replay-in-playground affordance from the log detail drawer.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **"Request log" is a NEW bounded concept distinct from "usage record" and "audit event"** — payload-bearing, opt-in, retention-governed, tenant-facing; never a source of billing truth (usage_records stays the only billing ledger).
- **Scrub-before-persist is an invariant, not a display filter** — PII masking runs through the existing guardrail engine BEFORE any payload row is written; raw unscrubbed payloads never touch disk. A scrub failure drops the payload (metadata-only row), never stores raw.
- **Capture is opt-in and fail-open for the proxy path** — capture-store unavailability must never fail or slow a proxied request (bounded timeout, fire-and-forget, mirrors the alert-seam pattern).
- **Guardrail policy resolution order: key > tenant > default-off** — a per-key policy overrides the tenant policy wholesale (no field merge — design decides and freezes this).
- **Output-schema validation supersedes v11 translate-don't-enforce ONLY as opt-in** — record the SUPERSESSION at the freeze; a request without the opt-in stays byte-identical.
- **Security floor**: capture store + logs API are `data`-sensitivity (tenant isolation, payload exposure); ML moderation egress is a new outbound IO seam (timeout + retry + breaker per CLAUDE.md).

## Shared / risky contracts (freeze these first)
- request-log row schema + capture hook placement (+ ZDR override hook) -> owning task `payload-capture-store`
- guardrail policy resolution order (key > tenant) -> owning task `per-key-guardrail-policies`
- output-validation supersession of the v11 pin -> owning task `output-schema-validation`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] payload-capture-store       depends-on: none                   — opt-in PII-scrubbed request/response capture store (new table + capture hook in the proxy path, size caps, retention wiring, ZDR override hook). (data; foundation)
- [ ] logs-explorer-api           depends-on: payload-capture-store  — tenant-admin query API: list w/ filters (time/model/key/status/cost) + pagination + detail fetch; tenant-scoped, RBAC-gated.
- [ ] logs-explorer-ui            depends-on: logs-explorer-api      — console Logs Explorer page: table + detail drawer + replay-in-playground (UDD design loop; Aurora).
- [ ] per-key-guardrail-policies  depends-on: none                   — per-KEY guardrail policy CRUD + resolution (key > tenant), admin API + dashboard.
- [ ] ml-moderation-layer         depends-on: none                   — ML moderation check class in the guardrail engine (provider-backed, default-off, block/audit modes, honest degradation + breaker on the new egress seam).
- [ ] output-schema-validation    depends-on: none                   — opt-in response JSON-schema validation + ONE bounded retry on mismatch (recorded supersession; off = byte-identical).
- [ ] guardrail-analytics         depends-on: per-key-guardrail-policies — guardrail verdict counters (per policy/pattern/key) + admin analytics API + dashboard view.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A tenant with capture ON gets a PII-scrubbed request/response log row per proxied call; a tenant with capture OFF (or ZDR) gets none; a capture-store outage never fails a proxied request   (← payload-capture-store)
- [ ] A tenant admin can list/filter/paginate logs and fetch one log's full detail; another tenant's logs are 404-invisible   (← logs-explorer-api)
- [ ] A tenant admin can browse logs in the console, open a detail drawer, and replay a logged request into the chat playground   (← logs-explorer-ui)
- [ ] A key with its own guardrail policy enforces it (overriding the tenant policy); a key without one inherits the tenant policy   (← per-key-guardrail-policies)
- [ ] With ML moderation ON, a flagged prompt is blocked (block mode) or audited (audit mode); a moderation-provider outage degrades honestly per the configured failure mode, never silently passes as "checked"   (← ml-moderation-layer)
- [ ] With output validation ON, a schema-mismatched response triggers exactly one retry then a structured error; with it OFF the response path is byte-identical to today   (← output-schema-validation)
- [ ] A tenant admin can see guardrail hit counts by policy/pattern/key over a time window in the dashboard   (← guardrail-analytics)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Open one integrated PR (or a stacked series) from the Close ship-review; Tin reviews + merges (push over HTTPS via gh per the repo gotcha; org-billing CI block ⇒ local-suite evidence + admin-merge)
- [ ] Full local suite green on the integrated branch (nohup + Monitor for the full run — background Bash dies at the 10-min cap)
- [ ] FEATURES.md + docs/runbooks updated for the new surfaces (logs explorer, guardrails v2) before close
- [ ] Bundle into the next release cut (release.md) with milestone attribution; Tin tags / deploys
