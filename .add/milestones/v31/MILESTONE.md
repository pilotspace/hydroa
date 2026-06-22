# MILESTONE: Operator-wide reconciliation + UI↔BE coverage

goal: a platform operator reads cross-tenant reconciliation drift through an authorized ops-auth endpoint, and every implemented backend control-plane capability has a dashboard surface
rationale: new-major (v31). Carries the ONE consciously-deferred slice of v30 (`operator-wide-reconciliation`, risk:high) as the LEAD task (Tin 2026-06-22 "defer to v31, open v31, seed operator-wide as first task"), then resumes the UI↔BE coverage program from the v25-intake gap audit (the original v31 sketch, renumbered 5×). Operator-wide is sequenced first because it is risk:high (a deliberate tenant-scoping exception + a new ops-auth authority) and its endpoint is the prerequisite for the eventual operator-view dashboard surface.
stage: production · status: active · created: 2026-06-22

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  operator-wide cross-tenant reconciliation **endpoint** behind a new platform-operator authority (separate ops-auth surface) · the UI↔BE coverage program — dashboard surfaces for the backend control-plane capabilities the v25 intake audit found with no UI (alerts viewer, SSO login button, catalog-sync trigger, upstream-health view, rate-limit counter view, routing-config write).
Out: an operator-view **dashboard UI** in this milestone unless explicitly scoped as a follow-up task (the operator-wide slice ships the ENDPOINT + ops-auth first; UI surface re-sized at open) · any change to markup semantics or the drift-sign convention (frozen v29) · alert delivery-channel changes (the `drift-alert` seam stays as-is) · the provider-config gap (delivered by v25).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **`platform operator`** (carried from v30): an authority that reads *across* tenants. The #1 invariant ("every tenant-owned query is tenant-scoped") gets exactly ONE named, audited exception — the cross-tenant reconciliation read — behind a **separate ops-auth surface**, never on a tenant JWT.
- **`ops-auth`** (carried from v30): the separate operator credential surface — its own issuer/signing key (NOT mintable via tenant signup), enforced on an edge-restricted path. Designed-for-failure per the IO rule (verification timeout/cache/fallback where a key fetch is involved).
- UI tasks honor the established dashboard a11y + data-slot + npm-test-gate conventions (see [[ui-restyle-recipe]] foundation lessons).

## Shared / risky contracts (freeze these first)
- **Platform-operator authority model = separate ops-auth surface** (DECIDED 2026-06-18, Tin) -> owning task `operator-wide-reconciliation`. A dedicated operator credential with its own issuer/signing key, NOT issuable through tenant signup, enforced on an edge-restricted path (`/ops/...` or the edge-blocked `/internal` family). Cross-tenant power NEVER rides a tenant JWT; the tenant-isolation invariant stays pure. **Freeze the exact wire shape (issuer/claims/path/verification + failure modes) in this task's §3 — risk:high, autonomy lowered, security HARD-STOP: human-approved by Tin BEFORE any code.**
- **OPEN at operator-wide specify**: whether the cross-tenant aggregation needs an all-tenants *mode* on the (v30-re-frozen) `reconcile_window` — a second supersession — vs a sibling query. Decide at §1/§3.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] operator-wide-reconciliation  **DONE 2026-06-22, gate PASS (risk:high, Tin-approved).** `GET /ops/reconciliation` behind mTLS+XFCC ops-auth (default-OFF/fail-closed); tenant JWT→403, else byte-identical 401; global + per-tenant drift; READ-ONLY. Refute UPHELD 0.87, 1303 suite green. Shipped: `ops/` package, `OpsCertVerifier`, `reconcile_by_tenant`, `GATEWAY_OPS_CERT_FINGERPRINTS`. ⚠ RELEASE REQ: Envoy must strip client XFCC + restrict /ops/* (trust boundary).
- [x] alerts-events-viewer       **DONE 2026-06-22, gate PASS (auto, refute 0.82 no-blockers).** `GET /admin/alerts` on usage_router: owner/admin-scoped, paginated (limit 1..100/offset, total), newest-first, READ-ONLY. **Visibility (Tin-approved @ freeze): `tenant_id = :tid OR tenant_id IS NULL`** — own soft-budget + platform system events (circuit/health/drift); other tenants NEVER. 422 ERR_PAYLOAD_INVALID / member 403 / missing-bearer 401. + dashboard admin-only `/alerts` page (DataTable, Type/When/Status, 4 states). 15 backend + 4 frontend tests; full suites 1318 + 365 green. NO migration/new table. [usage/api/router.py:get_alerts, components/alerts/*]
- [x] sso-login-button           **DONE 2026-06-22, gate PASS (auto, autonomy:auto).** Ground re-scoped: the SSO button already existed — the gap was a per-tenant domain field. Added a "Work email or domain" input on /login driving the existing SSO button's `?domain=` (email→domain extraction, empty→env fallback, lenient validation, full-page nav via window.location.assign). 5 SSO tests + 361 dashboard suite green; eslint+tsc clean. [LoginForm.tsx]
- [x] catalog-sync-trigger       **DONE 2026-06-23, gate PASS (auto, refute 0.87 no-blockers).** NEW `admin_catalog_router` → `POST /admin/catalog/sync`: owner/admin (member 403/missing-bearer 401), delegates to the existing `SyncCatalogUseCase` (idempotent global upsert; inherits its 10s timeout+retry), 200 `{synced, synced_at(ISO, gateway clock)}`, upstream-down → 502 ERR_UPSTREAM_UNAVAILABLE before any write, fail-safe provider_resolver refresh. Internal `/internal/catalog/sync` + SyncResponse byte-identical (separate CatalogSyncResponse). + `/models` owner/admin "Re-sync catalog" button (last-sync shown, invalidates admin-models; member can't see it). 9 backend + 3 frontend tests; suites 1326 + 368 green. NO migration. last-sync EPHEMERAL (persist = spec delta). [catalog/api/router.py:admin_sync_catalog, ModelsPage.tsx]
- [ ] upstream-health-view       depends-on: alerts-events-viewer — `GET /admin/health/upstreams` (last ping per provider/up-down) + a health panel.
- [ ] ratelimit-counter-view     depends-on: none — `GET /admin/ratelimits` (current Redis rpm/tpm per key) + a read-only panel on `/keys` or `/usage`.
- [ ] routing-config-write       depends-on: none — largest slice: write endpoints for model-groups / routing strategy / per-deployment rpm-tpm limits + circuit/retry thresholds (today env-only) + a `/routing` editor. May warrant its own sub-milestone — re-size at open.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A platform operator reads cross-tenant reconciliation drift through the authorized ops-auth endpoint; a tenant admin/member is denied (403).   (← operator-wide-reconciliation — DONE 2026-06-22, gate PASS)
- [x] An owner browses alert history (soft-budget, circuit-open, health) in the dashboard.   (← alerts-events-viewer — DONE 2026-06-22; visibility own+NULL system rows, gate PASS)
- [x] A tenant with SSO configured logs in from the `/login` page without a manual URL.   (← sso-login-button — DONE 2026-06-22, domain field drives per-tenant ?domain=)
- [x] An owner forces a catalog re-sync from the dashboard and sees the new last-sync time.   (← catalog-sync-trigger — DONE 2026-06-23, gate PASS; last-sync ephemeral, persist=spec delta)
- [ ] An owner sees per-provider upstream up/down status in the dashboard.   (← upstream-health-view)
- [ ] An owner sees current rpm/tpm consumption per key.   (← ratelimit-counter-view)
- [ ] An owner edits model-groups / routing strategy / deployment limits from the dashboard.   (← routing-config-write)

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
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
