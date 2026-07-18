# MILESTONE: Platform Key Default Credential

goal: A tenant with no configured BYOK key automatically uses the platform tenant credential by default, and a configured tenant key takes precedence once present
rationale: sub-milestone — milestone 4 of 5 in the confirmed "Full 5, admin-first" superadmin roadmap
  (platform-identity → platform-admin-console → tenant-impersonation → **platform-key-default** →
  platform-access-plan). platform-identity (done) explicitly deferred this in its own Scope-Out
  ("credential-resolution reordering / platform-key-as-default → platform-key-default") and built the
  half of the machinery this consumes: the reserved `kind='platform'` tenant that owns its own BYOK
  rows, and the unwired `resolve_platform_credential()` composition primitive. Sized 2026-07-15 (Tin)
  as the last unstarted roadmap milestone, after platform-access-plan closed. It SUPERSEDES a
  currently-frozen fail-closed invariant (`ports.py:536` "NEVER returns a platform key as a fallback"),
  which makes the core task security-sensitive (dual adversarial verify).
stage: production · status: active · created: 2026-07-02T15:53:54+00:00
release: 0.10.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Precedence-aware credential fallback at the ONE resolution seam (`resolve_provider_credential()`,
    `use_cases.py:1279-1310`): the requesting tenant's OWN (tenant_id, provider) key is tried first and
    ALWAYS wins when present; ONLY on `ProviderKeyMissing` does resolution fall back to the reserved
    `kind='platform'` tenant's own BYOK key for that provider. Wired into all 4 proxy verbs (chat,
    embeddings, images, audio) and all 8 providers.
  - Default-ON for every tenant (Tin 2026-07-15), governed by a single global settings kill-switch;
    with the switch OFF the wrapper is a no-op and behavior is byte-identical to today (402
    `ERR_PROVIDER_KEY_MISSING`, no fallback).
  - Every platform-fallback resolution emits an audit event (requesting tenant + provider + "served via
    platform fallback") — the proxy credential path has NO audit today; this adds it for the fallback case.
  - A `credential_source` marker (platform | byok) stamped on the usage record for requests served by
    the platform credential, so platform-subsidized usage is distinguishable for reporting. Usage stays
    attributed to the REQUESTING tenant and counts against that tenant's own plan budget/rate exactly as
    today (Tin: no separate platform-wide ceiling in v1).
Out (explicitly deferred — anti-scope-creep):
  - Per-tenant opt-in / allow-list for fallback — Tin chose default-ON + global kill-switch, not per-tenant.
  - A separate platform-wide subsidized-spend ceiling / differential budget dimension — v1 counts
    fallback usage against the requesting tenant's own budget (Tin).
  - Any superadmin dashboard surface for platform-subsidized spend / "which tenants rely on fallback" —
    the `credential_source` marker makes it QUERYABLE, but a UI is a fast-follow, not v1 (and when built
    must clear the UI-usable-bar standing rule, not a bare table).
  - Re-introducing any env-var / settings GLOBAL provider key — that path was deliberately REMOVED as a
    hardening step; fallback is strictly platform-TENANT-owned BYOK rows through the identical
    Fernet/cache/timeout machinery, never raw env secrets.
  - Changing usage/cost attribution to the credential OWNER — attribution stays the requesting tenant.
  - Any change to `resolve()`'s own frozen fail-closed contract for NON-fallback callers (ops path, the
    platform tenant's own requests) — fallback is composed OUTSIDE `resolve()` so that invariant holds
    everywhere else.
  - Guaranteeing the platform's Bedrock/Azure/Vertex credential actually COVERS every tenant's
    region/deployment/project — a non-bearer fallback the platform key can't serve surfaces as a clean
    UPSTREAM error, not a gateway-side guarantee (see shared decisions).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Fallback is composed OUTSIDE `resolve()`, never inside it.** A wrapper calls the existing `resolve()`
  for the requesting tenant, catches `ProviderKeyMissing`, and only THEN attempts a second, explicitly
  separate resolution against `get_platform_tenant().id`. `resolve()`'s frozen fail-closed docstring
  (`ports.py:535-536`) is superseded on the NEW wrapper, not broken on `resolve()` — every other caller
  keeps the invariant.
- **Confused-deputy contextvar (SECURITY, hard requirement).** When the platform credential serves a
  request, `set_provider_credential(cred, owner_tenant_id)` MUST be passed the PLATFORM tenant's id (the
  secret's real owner), NOT the requesting tenant's — the per-(tenant, identity) Azure-AAD/Vertex token
  caches depend on it (vertex-adapter M4 CR-2). Getting this one argument wrong is the milestone's
  sharpest leakage/over-mint risk.
- **Cache under the OWNER key, never cross-cache.** Cache the platform result under
  `(platform_tenant_id, provider)`, never under `(requesting_tenant_id, provider)` — else a tenant that
  later adds its OWN key could be served a stale platform entry.
- **Precedence is absolute: own key ALWAYS wins.** Fallback fires ONLY on `ProviderKeyMissing` for the
  requesting tenant; a configured (even if later) tenant key is never overridden.
- **Kill-switch OFF ⇒ byte-identical to today.** Global setting default-ON; when off, the 402 fail-closed
  path is exactly as before.
- **402 semantics narrow.** `ERR_PROVIDER_KEY_MISSING` (402) now fires only when BOTH the tenant's own
  key AND the platform key are absent (or the kill-switch is off).
- **All 8 providers; non-bearer failures surface cleanly.** Bedrock (region-bound), Azure
  (deployment_map), Vertex (project-bound) fallbacks may 403/404 UPSTREAM if the platform credential
  doesn't cover the request — surface as the upstream provider error, never a silent success or
  misattribution.
- **Usage stays the requesting tenant's.** `credential_source=platform` is a provenance MARKER only; it
  never changes which tenant the usage/cost/budget is attributed to.
- New GLOSSARY term — **platform-fallback credential**: the `kind='platform'` tenant's own BYOK key used
  to serve a DIFFERENT (customer) tenant's outbound call when that tenant has no key of its own —
  distinct from "superadmin acting cross-tenant" (an authz concept) and from the platform tenant's own
  requests.

## Shared / risky contracts (freeze these first)
- The fallback resolution shape — the wrapper composing requesting-tenant → platform-tenant resolution,
  the confused-deputy owner-tenant-id rule, the kill-switch, how it supersedes the "NEVER platform
  fallback" docstring — AND the request-scoped "served via platform fallback" signal it exposes
  (consumed by both the audit event and the usage marker). -> owning task `platform-credential-fallback`
  (the riskiest, security-sensitive decision; the marker/audit both key off this signal).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] platform-credential-fallback   depends-on: none                          — SECURITY (dual-verify). Precedence-aware fallback wrapper at the resolution seam, wired into all 4 verbs + 8 providers; confused-deputy contextvar (platform owner id); owner-keyed cache; global kill-switch (default-ON); audit event on fallback use; exposes the request-scoped "served via platform fallback" signal.
- [ ] fallback-usage-marker          depends-on: platform-credential-fallback  — Stamp the usage record with `credential_source` (platform | byok) from that signal, so platform-subsidized usage is distinguishable for reporting; attribution + budget/rate stay the requesting tenant's, unchanged.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant with no BYOK key for a (bearer) provider successfully proxies a request served by the platform credential   (← platform-credential-fallback: test_e2e_default_on_serves_platform_credential_real_wiring + M-suite)
- [x] Once that tenant configures its OWN key for the provider, the platform credential is never used for it again (own-key precedence)   (← platform-credential-fallback: e2e step 5 through the real positive-only cache + M2/M4 unit tests)
- [x] With the global kill-switch OFF, a keyless tenant gets 402 ERR_PROVIDER_KEY_MISSING — byte-identical to today   (← platform-credential-fallback: test_e2e_kill_switch_off_keyless_gets_402_real_wiring + R2/setting unit tests)
- [x] Every platform-fallback resolution emits an audit event naming the requesting tenant + provider   (← platform-credential-fallback: M5 audit_served tests + PlatformCredentialFallbackService)
- [x] A non-bearer (Bedrock/Azure/Vertex) fallback the platform credential can't cover surfaces a clean upstream error, not a silent success/misattribution   (← platform-credential-fallback: R1/R3 402 fail-closed tests)
- [x] Usage served by the platform credential is marked `credential_source=platform` and still counts against the requesting tenant's own budget   (← fallback-usage-marker: test_recorder_stamps_credential_source_in_raw asserts raw credential_source=="platform" AND tenant_id==requester; provenance-only, spend counters untouched)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched (add.py / state.json engine unchanged; only task/milestone docs authored).
- skill   : untouched.
- book    : untouched.
- gateway (BE) : the credential-resolution seam gained an OPTIONAL platform-fallback collaborator
  (`resolve_provider_credential(..., platform_fallback=)`) composing requesting-tenant→platform-tenant
  resolution OUTSIDE the frozen `resolve()` (its "NEVER a platform key" invariant intact for all other
  callers); a `PlatformCredentialFallback` port + `PlatformCredentialFallbackService` (fail-closed
  timeout, fire-and-forget audit); a confused-deputy contextvar (platform-tenant cache owner); a global
  kill-switch setting (default-ON); wired into all 4 verbs × 8 providers via api/*_deps + main.py. Task 2
  added a never-reset `_credential_source_ctx` published at resolution, consumed in `_dispatch_record`,
  stamping `credential_source="platform"` into the FROZEN `usage_records` raw JSONB seam (no new column).

### Cross-task evidence   (one row per task)
- platform-credential-fallback : gate=PASS · tests=22 green (20 unit + 2 DB-backed e2e) · security dual-verify
  (independent add-verify + self refute-read, all 8 attack items CLEAR) · residue=none (e2e closed the wired-path residue)
- fallback-usage-marker : gate=PASS · tests=9 green + regression task1/usage/proxy 96 green (0 fail, byte-identity held) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cited inline on each box)
- goal: "A tenant with no configured BYOK key automatically uses the platform tenant credential by default,
  and a configured tenant key takes precedence once present." PROVEN by
  test_e2e_default_on_serves_platform_credential_real_wiring (keyless tenant served the platform BYOK
  credential through the real create_app wiring, platform-tenant id as cache owner) + e2e step 5 (own key
  configured → the very next resolve serves IT, never the platform entry).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] open a PR from the Close ship-review above; the human reviews + merges (security milestone — dual adversarial verify recorded before merge)
- [ ] confirm the global kill-switch default + document the operator runbook for provisioning the platform tenant's BYOK keys
- [ ] tag / publish / deploy  (human-run, per release.md) — bundle into the next release cut alongside the 4 already-closed roadmap milestones
