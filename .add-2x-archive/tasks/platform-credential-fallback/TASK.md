# TASK: Precedence-aware platform credential fallback

slug: platform-credential-fallback · created: 2026-07-15 · stage: production · sensitivity: security · risk: high
milestone: platform-key-default
autonomy: conservative
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `proxy/application/use_cases.py:resolve_provider_credential(resolver, tenant_id, provider)` — THE single credential seam, keyed on `(tenant_id, provider)`; today catches `ProviderKeyMissing` → `ProblemError(402, ERR_PROVIDER_KEY_MISSING)`. The one place fallback composes.
  - `proxy/application/use_cases.py:CompletionUseCase._resolve_credential` (chat) + the 3 sibling verbs: `embeddings_use_case.py:266`, `images_use_case.py:195`, `audio_use_case.py:349,:619` — all call the seam with `authz.tenant_id` (the REQUESTING tenant). All 4 must route through the new wrapper.
  - `proxy/domain/credential_context.py:set_provider_credential(cred, tenant_id)` — sets `current_provider_credential` (secret) + `current_credential_tenant` (OWNER id, scopes Azure-AAD/Vertex token caches). The confused-deputy boundary: must receive the PLATFORM tenant id when serving fallback.
  - `tenants/infrastructure/repository.py:get_platform_tenant(session)` — `SELECT * FROM tenants WHERE kind='platform'`; the reserved single `kind='platform'` row (real uuid7 id, NOT a constant). Resolve at runtime, never cache as a constant.
  - `proxy/infrastructure/cached_tenant_credential_resolver.py:CachedTenantCredentialResolver` — process-wide single instance (`main.py:1364`), TTL cache keyed `(uuid, provider)`, POSITIVE-only (miss never cached → own-key takes effect next request), fails CLOSED on timeout.
  - `ops/api/deps.py:resolve_platform_credential` — EXISTING but ops-mTLS-gated, ZERO proxy callers, NO precedence check. A composition reference (get_platform_tenant + resolve_provider_credential + fire-and-forget `record_audit`), NOT directly callable from the JWT/gateway-key proxy path.
Context (working folder): new wrapper lives in the proxy application layer (alongside `resolve_provider_credential`); a global kill-switch setting in `core/config.py`; audit via the existing `record_audit`/`emit_platform_audit` primitive.
Honors (patterns / conventions): fail-closed floor (never fabricate a tenant_id / silent empty cred); SecretStr masking; positive-only cache semantics; per-(tenant, identity) token-cache scoping (vertex-adapter M4 CR-2); `require_superadmin`/audit reuse verbatim, no parallel primitive.
Seams consulted: the credential-resolution seam (`resolve_provider_credential`) + the platform-tenant seam (`get_platform_tenant`) — composed, not modified.
Anchors the contract cites: `resolve_provider_credential`, `set_provider_credential`, `get_platform_tenant`, `CachedTenantCredentialResolver.resolve`, `ProviderKeyMissing`, `ports.py:535-536` (`TenantCredentialResolver` docstring — superseded on the wrapper, not on resolve()), the new kill-switch setting.
Issues/Risks (→ feed §1):
  - FROZEN-CONTRACT COLLISION: `ports.py:536` "NEVER returns a platform key as a fallback" + 5 restatements in `credential-resolution-seam/TASK.md`. Compose fallback OUTSIDE `resolve()` so its invariant holds for every other caller (incl. `resolve_platform_credential` itself). HARD design constraint.
  - CONFUSED-DEPUTY (SECURITY): `set_provider_credential(cred, PLATFORM_tenant_id)` for fallback — wrong arg = redundant minting now, cross-tenant token-cache leakage if a future refactor trusts that id as an authz boundary.
  - CACHE STALENESS: cache platform result under `(platform_tenant_id, provider)`, never cross-cache under requesting tenant — else a tenant adding its own key later gets a stale platform entry (loses positive-only "next-request" property).
  - NON-BEARER 403/404: Bedrock (region-bound), Azure (deployment_map), Vertex (project-bound) fallbacks may fail UPSTREAM — surface clean upstream error, never silent misattribution.
  - NO HOT-PATH AUDIT today: fallback must add one (tenant, provider, "served via platform fallback").
  - 402 SEMANTICS NARROW: fires only when BOTH tenant + platform keys absent (or kill-switch off).
Related intent: milestone `platform-key-default` (goal: keyless tenant auto-uses platform credential, own key wins once present); roadmap milestone 4 of 5; platform-identity Scope-Out deferred it here; GLOSSARY new term **platform-fallback credential** (distinct from "superadmin acting cross-tenant" and from the platform tenant's own requests).
Ground SHA: 3c27af5   (cite symbols; any line ref is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Precedence-aware platform credential fallback (default-ON, global kill-switch)
Framings weighed: **wrapper OUTSIDE `resolve()`** (chosen — compose in the proxy application layer: try requesting tenant, catch `ProviderKeyMissing`, retry against the platform tenant; keeps `resolve()`'s frozen fail-closed invariant intact for every other caller) · modify `resolve()` to fall back internally (rejected — literally contradicts the frozen `ports.py:536` docstring and would make a request FOR the platform tenant's own missing key recurse/resolve nonsense) · reuse `resolve_platform_credential()` from the ops path (rejected — ops-mTLS-gated, unreachable from tenant-JWT proxy auth, and does NO precedence check).
Must:
<must>
  - M1 FALLBACK: when the requesting tenant has no own key for the resolved provider AND the kill-switch is ON, resolution serves the platform tenant's own credential for that provider and the request proceeds.
  - M2 PRECEDENCE: the requesting tenant's OWN key ALWAYS wins — fallback is attempted ONLY after `resolve()` raises `ProviderKeyMissing` for the requesting tenant; a configured (even newly-added) tenant key is never overridden.
  - M3 CONFUSED-DEPUTY: when serving the platform credential, the owner id published to the credential contextvar (`set_provider_credential(cred, owner_id)`) is the PLATFORM tenant's id — never the requesting tenant's.
  - M4 CACHE: the platform credential is resolved/cached under `(platform_tenant_id, provider)`; it is NEVER cross-cached under the requesting tenant's key (preserves the positive-only "own key takes effect next request" property).
  - M5 AUDIT: every fallback resolution emits an audit event naming the requesting tenant + provider + "served via platform fallback".
  - M6 KILL-SWITCH: with the global kill-switch OFF, no fallback is attempted and behavior is byte-identical to today (402 on a keyless tenant).
  - M7 UNIFORM: fallback applies identically across all 4 proxy verbs (chat, embeddings, images, audio) and all 8 providers — one seam, no per-verb/per-provider special-casing of the fallback decision.
  - M8 SIGNAL: on a served fallback, a request-scoped "served via platform fallback" signal is set (consumed downstream by `fallback-usage-marker`); on an own-key or no-fallback request it stays unset.
  - M9 PROVIDER-AGNOSTIC: fallback resolution does NOT validate upstream coverage; a platform credential the upstream rejects (Bedrock region / Azure deployment_map / Vertex project mismatch) surfaces the provider's OWN upstream error unchanged — never a silent success or misattribution, never re-mapped to a gateway 402.
</must>
Reject:
<reject>
  - R1 BOTH-ABSENT: requesting tenant has no own key AND the platform tenant has no key for the provider -> "ERR_PROVIDER_KEY_MISSING" (402) — the honest "no credential available" signal, unchanged shape from today.
  - R2 SWITCH-OFF: kill-switch OFF + requesting tenant has no own key -> "ERR_PROVIDER_KEY_MISSING" (402), no fallback attempt (M6).
  - R3 PLATFORM-ROW-MISSING: the reserved `kind='platform'` tenant row is not provisioned while the kill-switch is ON + a keyless tenant requests -> fail closed to "ERR_PROVIDER_KEY_MISSING" (402) to the tenant, AND emit an error-level audit/log so operators see the misconfiguration (never fabricate a tenant_id; never serve an empty credential). [see ⚠ — the 402-vs-500 signal is the flag]
</reject>
After:
<after>
  - `current_provider_credential` = the platform credential; `current_credential_tenant` = the PLATFORM tenant's id (M3).
  - usage/cost/budget/rate stay attributed to the REQUESTING tenant, exactly as today (attribution never moves to the credential owner).
  - an audit event for the fallback is recorded (M5); the "served via platform fallback" signal is set for the request (M8).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ R3 error signal — when the platform tenant ROW is absent (a server misconfiguration, distinct from "platform has no key for this provider") the draft fails closed to the tenant-facing 402 ERR_PROVIDER_KEY_MISSING (+ loud operator audit) rather than a 500 ERR_PLATFORM_TENANT_MISSING. Lowest confidence because it's a genuine product-signal choice, not a code fact: default-ON means a missing platform row would otherwise turn EVERY keyless tenant's 402 into a 500 storm; but 402 hides a real server misconfig from the tenant-facing error. If wrong: either clients misread a misconfig as "add your own key" (402 choice) or a transient/rollout platform-row gap spams 500s across all keyless tenants (500 choice). → surfaced at the freeze for the human call.
  - [ ] Kill-switch is a global boolean setting in `core/config.py`, default TRUE (Tin: default-ON) — confirm field name (`platform_credential_fallback_enabled`) + that a per-request read of `settings` is acceptable (no hot-path cost).
  - [ ] Audit uses the existing `record_audit`/`emit_platform_audit` primitive with a new proxy action (e.g. `proxy.platform_credential_fallback`), fire-and-forget like the usage record — not a new audit subsystem.
  - [ ] The M8 signal is a request-scoped `contextvar` (mirroring `credential_context` / plan-rate-enforcement's `tenant_tpm_ctx`), set by the wrapper and read by the usage recorder — no signature change threaded through 4 verbs.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: keyless tenant served by platform fallback   # M1
  Given the kill-switch is ON, tenant T has no own key for provider P, and the platform tenant has a key for P
  When resolution runs for (T, P)
  Then the platform tenant's credential is served and resolution succeeds (no 402)

Scenario: own key wins over platform fallback   # M2
  Given the kill-switch is ON, tenant T HAS its own key for provider P, and the platform tenant also has a key for P
  When resolution runs for (T, P)
  Then T's OWN credential is served
  And the platform credential is never consulted (no fallback attempt, no fallback audit)

Scenario: fallback publishes the platform owner id, not the requester   # M3
  Given the kill-switch is ON and tenant T (no own key for P) is served the platform credential for P
  When the credential contextvar is read
  Then current_credential_tenant is the PLATFORM tenant's id
  And it is NOT tenant T's id

Scenario: platform credential cached under the platform key only   # M4
  Given the kill-switch is ON and tenant T (no own key for P) was just served the platform fallback for P
  When tenant T then configures its OWN key for P and makes a second request
  Then the second request is served by T's own key, not a stale cached platform entry

Scenario: fallback emits an audit event   # M5
  Given the kill-switch is ON and tenant T (no own key for P) requests provider P
  When the platform fallback serves the request
  Then an audit event is recorded naming tenant T, provider P, and "served via platform fallback"

Scenario: kill-switch OFF is byte-identical to today   # M6
  Given the kill-switch is OFF and tenant T has no own key for provider P (platform HAS a key for P)
  When resolution runs for (T, P)
  Then a 402 ERR_PROVIDER_KEY_MISSING is raised
  And no fallback is attempted and no fallback audit is recorded

Scenario: fallback applies across every verb and provider   # M7
  Given the kill-switch is ON, tenant T has no own key, and the platform tenant has a key for provider P
  When a chat, embeddings, images, or audio request for T resolves a model whose provider is P
  Then each is served by the platform fallback through the same seam (no per-verb divergence)

Scenario: served-fallback signal set only on fallback   # M8
  Given the kill-switch is ON
  When a request is served by the platform fallback
  Then the request-scoped "served via platform fallback" signal is set
  And when the same tenant is instead served by its OWN key, the signal stays unset

Scenario: upstream rejection of the platform credential surfaces cleanly   # M9
  Given the kill-switch is ON and tenant T is served a platform Bedrock/Azure/Vertex credential that the upstream does not cover (wrong region/deployment/project)
  When the upstream call is made
  Then the provider's own upstream error surfaces unchanged
  And it is NOT re-mapped to a gateway 402 or reported as success

Scenario: both keys absent -> 402   # R1
  Given the kill-switch is ON, tenant T has no own key for P, and the platform tenant ALSO has no key for P
  When resolution runs for (T, P)
  Then a 402 ERR_PROVIDER_KEY_MISSING is raised
  And no credential is served (fail closed)

Scenario: switch off, keyless tenant -> 402   # R2
  Given the kill-switch is OFF and tenant T has no own key for P
  When resolution runs for (T, P)
  Then a 402 ERR_PROVIDER_KEY_MISSING is raised
  And no fallback is attempted

Scenario: platform tenant row missing while fallback ON -> 402 + loud audit   # R3
  Given the kill-switch is ON, tenant T has no own key for P, and the reserved kind='platform' tenant row is NOT provisioned
  When resolution runs for (T, P)
  Then a 402 ERR_PROVIDER_KEY_MISSING is raised to the tenant (never a fabricated tenant_id, never an empty credential)
  And an error-level audit/log records the platform-tenant-missing misconfiguration
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# This is a CODE contract (no new HTTP route). Fallback is composed ADDITIVELY at the existing
# seam so every current caller stays byte-identical; the new behavior rides one keyword-only param.

# --- NEW collaborator (port), proxy/domain — the injected fallback capability ---
class PlatformCredentialFallback(Protocol):
    enabled: bool                                  # the global kill-switch, read at construction
    async def platform_tenant_id(self) -> uuid.UUID | None
        # resolve the reserved kind='platform' row id via get_platform_tenant(session), bounded by a
        # timeout; returns None if the row is not provisioned OR on DB error/timeout (fail-closed).
        # NEVER fabricates an id.
    async def audit_served(self, *, tenant_id: uuid.UUID, provider: str) -> None
        # fire-and-forget audit of a served fallback (M5): action "proxy.platform_credential_fallback".
    async def audit_misconfig(self, *, tenant_id: uuid.UUID, provider: str) -> None
        # error-level audit/log that the platform tenant row is missing while fallback is ON (R3).

# --- EXTENDED seam, proxy/application/use_cases.py (additive keyword-only param) ---
async def resolve_provider_credential(
    resolver: TenantCredentialResolver | None,
    tenant_id: Any,
    provider: str,
    *,
    platform_fallback: PlatformCredentialFallback | None = None,   # NEW; None => today's behavior, byte-identical
) -> object | None:
  # unchanged: resolver is None or provider not in BYOK_PROVIDERS -> return None
  # try resolver.resolve(tenant_id, provider):
  #    success -> set_provider_credential(cred, tenant_id)            # own key (M2), owner = requester
  #    ProviderKeyMissing:
  #      if platform_fallback is None or not platform_fallback.enabled:
  #          raise ProblemError(402, ERR_PROVIDER_KEY_MISSING) from None      # R2 / today (M6)
  #      plat_id = await platform_fallback.platform_tenant_id()
  #      if plat_id is None:
  #          await platform_fallback.audit_misconfig(tenant_id, provider)     # R3: loud + 402
  #          raise ProblemError(402, ERR_PROVIDER_KEY_MISSING) from None
  #      try: plat_cred = await resolver.resolve(plat_id, provider)           # reuses cache keyed (plat_id,provider) => M4
  #      except ProviderKeyMissing: raise ProblemError(402, ERR_PROVIDER_KEY_MISSING) from None  # R1 both absent
  #      mark_platform_fallback()                                             # M8 signal
  #      await platform_fallback.audit_served(tenant_id, provider)            # M5
  #      return set_provider_credential(plat_cred, plat_id)                   # M3 owner = PLATFORM id

# --- NEW request-scoped signal, proxy/domain/credential_context.py (M8) ---
current_served_via_platform_fallback: ContextVar[bool]   # default False
def mark_platform_fallback() -> None                      # sets it True for the request
def served_via_platform_fallback() -> bool                # read by fallback-usage-marker (sibling task)
# reset alongside the existing credential contextvars in reset_provider_credential()'s finally.

# --- NEW setting, core/config.py ---
platform_credential_fallback_enabled: bool = True         # default-ON (Tin 2026-07-15); kill-switch

# --- WIRING (no contract, listed for scope) ---
# main.py: app.state.platform_credential_fallback = <impl>(session_factory, settings.*_enabled, audit sink)
# proxy/api/deps.py: getattr(app.state, "platform_credential_fallback", None) -> passed to each of the 4
#   use-cases, which forward it to resolve_provider_credential (chat _resolve_credential + embeddings/images/audio).

Schema: READS only — tenants (WHERE kind='platform', via get_platform_tenant) + tenant_provider_keys
        (via the existing resolver, now also for the platform tenant id). NO new column/table in this
        task (the credential_source marker is the sibling task `fallback-usage-marker`).
```

Glossary deltas: **platform-fallback credential** — the reserved `kind='platform'` tenant's own BYOK key, served for a DIFFERENT (customer) tenant's outbound call when that tenant has no key of its own; distinct from "superadmin acting cross-tenant" (authz) and from the platform tenant's own requests.
Least-sure flag surfaced at freeze: [contract] R3 platform-tenant-ROW-missing error signal — RESOLVED by Tin 2026-07-15: fail closed to tenant-facing 402 ERR_PROVIDER_KEY_MISSING + an error-level operator audit/log (never a 500-storm across all keyless tenants during a platform-row gap). All other §3 shapes (additive keyword-only seam, PlatformCredentialFallback port, M8 signal contextvar, default-ON kill-switch) approved as drafted.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (task-local; full-suite floor 80%)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_m1_keyless_tenant_served_by_platform_fallback: keyless + ON + platform has key / resolve / platform cred bound, no 402 · covers M1
  - test_m2_own_key_precedence: requester has key / resolve / own cred bound, platform never consulted, no fallback audit · covers M2
  - test_m3_confused_deputy_owner_is_platform: served fallback / read owner ctxvar / owner == platform id, != requester · covers M3 (SECURITY)
  - test_m4_platform_resolved_under_platform_key: served fallback / inspect resolve calls / last resolve keyed on platform id (cache-key correctness) · covers M4
  - test_m5_fallback_emits_audit: served fallback / resolve / audit == (requester, provider) · covers M5
  - test_m6_kill_switch_off_no_fallback: switch OFF + keyless / resolve / 402, platform never consulted · covers M6
  - test_m7_uniform_across_all_providers[8]: each of 8 providers / resolve / served through same seam + audited · covers M7
  - test_m8_signal_set_on_fallback_only: fallback then own-key / resolve / signal True while bound, False after reset, unset on own-key · covers M8
  - test_m9_no_gateway_side_coverage_check: bedrock fallback / resolve / cred bound, no gateway 402 coverage-gate · covers M9
  - test_r1_both_absent_402: both keys absent / resolve / 402, no cred bound, no served audit · covers R1
  - test_r2_switch_off_keyless_402: switch OFF + keyless / resolve / 402, no fallback machinery touched · covers R2
  - test_r3_platform_row_missing_402_plus_audit: platform_tenant_id → None / resolve / 402 + misconfig audit, no cred bound · covers R3
  - test_kill_switch_setting_defaults_on: Settings introspection / — / field exists, default True · covers M6 config surface
</test_plan>

  - test_e2e_default_on_serves_platform_credential_real_wiring: REAL create_app default-ON + real store/resolver/service + real get_platform_tenant DB read + Fernet / keyless customer resolve / platform cred served, owner=platform id, own-key precedence through the real cache · covers M1–M4 end-to-end (added at VERIFY to close residue #1)
  - test_e2e_kill_switch_off_keyless_gets_402_real_wiring: real wiring, kill-switch OFF / keyless resolve / 402, no cred bound · covers M6 end-to-end
Tests live in: `apps/gateway/tests/platform_credential_fallback/` · 22 tests (20 unit RED @ 3c27af5 for the right reason [TypeError: seam has no `platform_fallback` kwarg · ImportError: `served_via_platform_fallback` absent · AssertionError: `platform_credential_fallback_enabled` setting absent] + 2 DB-backed e2e added at VERIFY to close the wired-path residue).

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/credential_context.py` `apps/gateway/src/gateway/proxy/domain/ports.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/application/platform_fallback.py` `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py` `apps/gateway/src/gateway/proxy/application/images_use_case.py` `apps/gateway/src/gateway/proxy/application/audio_use_case.py` `apps/gateway/src/gateway/proxy/application/governance.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/proxy/api/embeddings_deps.py` `apps/gateway/src/gateway/proxy/api/images_deps.py` `apps/gateway/src/gateway/proxy/api/audio_deps.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches):
  1. DOMAIN: add `current_served_via_platform_fallback` ContextVar + `mark_platform_fallback()`/`served_via_platform_fallback()` to credential_context.py; reset it in `reset_provider_credential()`'s finally (fold its token into `_CredentialScope`, or set(False) on reset). Add the `PlatformCredentialFallback` Protocol to ports.py.
  2. SEAM: extend `resolve_provider_credential()` with the keyword-only `platform_fallback` param + the catch-ProviderKeyMissing→(enabled? platform_tenant_id? resolve(plat_id)? set owner=plat_id + mark + audit_served : 402 / R1 / R3) branch. Keep the `None` default byte-identical.
  3. IMPL: `platform_fallback.py` — concrete `PlatformCredentialFallback` over a session_factory: `platform_tenant_id()` = bounded-timeout `get_platform_tenant(session)` → `.id` or None (fail-closed on error/timeout); `audit_served`/`audit_misconfig` = fire-and-forget `record_audit` (action `proxy.platform_credential_fallback` / `...misconfig`).
  4. CONFIG: `platform_credential_fallback_enabled: bool = True` in core/config.py.
  5. WIRE: main.py builds `app.state.platform_credential_fallback` (enabled from settings); deps.py reads it via getattr and threads it into the 4 use-cases; each verb forwards it to `resolve_provider_credential` (chat `_resolve_credential`, embeddings/images/audio, + governance.py NonChat path if it resolves creds).
Persona (required): generic (no project persona file fits a credential-resolution security task yet; SOUL.md stance + security sensitivity govern).
Spawn isolation (default): shared-tree — delegated to a single add-build subagent, sequential (no parallel writers); worktree not needed.
Known-problem fixes:
  - frozen-contract collision → compose ONLY in the wrapper; do NOT touch `TenantCredentialResolver.resolve()` or its `ports.py:535-536` docstring.
  - confused-deputy → `set_provider_credential(plat_cred, plat_id)` uses the PLATFORM id (M3); assert in test_m3.
  - cache staleness → resolve the platform cred via `resolver.resolve(plat_id, provider)` so the shared cache keys under plat_id (M4); never cross-cache.
  - the M8 signal MUST reset with the credential scope (test_m8 asserts False after reset).
Strategy actually used: as planned (5 batches, direct build not delegated — held tight control of the security-sensitive seam). One VERIFY-phase refinement: reordered `mark_platform_fallback()` to the LAST statement in `_resolve_platform_fallback` (after the credential scope is bound) so the caller's `finally` always resets the M8 signal — closes a theoretical cross-request leak window a throw between mark and return would have opened.
Safety rule (feature-specific): fail-closed everywhere — platform_tenant_id() returns None (never a fabricated id) on absent row / DB error / timeout; a platform resolve miss → 402, never an empty/None credential bound.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [x] no test or contract was altered during build — the 20 red tests + frozen §3 are untouched; only src/ + a new test package added.
- [x] the green was EARNED, not gamed — independent add-verify (agent a2ca5d0506811d2bf) refute-read: no vacuous asserts, no stubbed-away branch; test_m3/m4/m2 are genuine security observables.
- [x] concurrency / timing of the risky operation is safe — audits are fire-and-forget (ensure_future, fail-open record_audit); platform_tenant_id() is asyncio.timeout-bounded + fail-closed; M8 signal reset unconditional + set last.
- [x] no exposed secrets, injection openings, or unexpected dependencies — audit metadata carries only tenant id + provider + provenance; no new packages.
- [x] layering & dependencies follow CONVENTIONS.md — fallback composed in the application layer over the domain seam; Protocol in domain/ports; no infra reach-up.
- [x] a person reviewed and approved the change — Tin Dang approved PASS 2026-07-15 (green full suite + dual-verify EARNED + residue #1 closed).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a keyless tenant + platform key → platform credential BOUND to the request contextvar, no 402 — confirmed by test_m1/m7 (all 8 providers) asserting a non-None token + get_provider_credential() is the platform cred.
- [x] own key present → own cred served, platform NEVER consulted (fb.platform_id_calls == 0) — confirmed by test_m2.
- [x] fallback owner id in the contextvar is the PLATFORM id, not the requester (confused-deputy) — confirmed by test_m3 (get_credential_tenant() == platform_id != requester_id).
- [x] kill-switch OFF / both-absent / platform-row-missing → 402 ERR_PROVIDER_KEY_MISSING, no cred bound; R3 also emits a misconfig audit — confirmed by test_m6/r1/r2/r3.
- [x] the served-fallback signal is True only on a served fallback and False after reset — confirmed by test_m8.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `PlatformCredentialFallback` (ports) → seam kwarg + 4 use-case ctors; `PlatformCredentialFallbackService` → main.py:1374 app.state; `mark/served_via_platform_fallback` → seam + (future) usage recorder; `platform_credential_fallback_enabled` → main.py construction. Confirmed by independent add-verify WIRING pass.
- [x] DEAD-CODE (code) — `served_via_platform_fallback()` is consumed by the sibling `fallback-usage-marker` task (declared seam), not orphaned; all other new symbols have live call sites.
- [ ] SEMANTIC (prose / non-code) — n/a (code task).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 cites still resolves — `resolve_provider_credential`, `set_provider_credential`, `get_platform_tenant`, `CachedTenantCredentialResolver.resolve`, `ProviderKeyMissing` all resolved during the build edits; ports docstring at 535-536 byte-identical (git diff confirmed by add-verify).
- [x] no anchor moved/renamed since Ground SHA 3c27af5 (same-session build).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self + agent a2ca5d0506811d2bf (independent, dual-verify) · adversarially checked: confused-deputy owner id, frozen-invariant non-regression (git diff of resolve()/ops path), cache-key correctness, fail-closed platform_tenant_id, all three 402 branches, M8 cross-request signal leak, secret-in-audit, test honesty (no test passes with the security property broken). Self refute-read additionally reordered mark_platform_fallback to the last statement (post-scope) to close a theoretical signal-leak window.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: a2ca5d0506811d2bf (add-verify, appsec persona)
1. Security: CLEAR — all 8 attack items CLEAR; confused-deputy + fail-closed + frozen-invariant + secret-exposure all clean.
2. Concurrency: CLEAR — fire-and-forget audits fail-open; timeout-bounded DB read; unconditional signal reset.
3. Architecture: CLEAR — additive keyword-only seam, wrapper OUTSIDE resolve(), no frozen-contract edit.
Verdict: PASS
Residue: RESOLVED at Tin's request — added 2 DB-backed e2e tests (`test_platform_credential_fallback_e2e.py`) exercising the REAL create_app default-ON wiring (real resolver/store/service + real get_platform_tenant DB read + Fernet) serving a keyless tenant the platform credential (owner=platform id) + own-key precedence through the real cache + kill-switch-OFF→402. The remaining residue (realtime-WS/memory-embeddings not wired for fallback, out of the frozen 4-verb scope) stays a seeded spec delta, not a defect.
Binding: advisory — security (a human floor still applies; recorded for Tin's gate).

### GATE RECORD
Reported: yes — gate report (banner/ARC/evidence/residue) rendered to Tin before this outcome.
Outcome: PASS — full suite 3959✓ (1 isolated-confirmed unrelated flake) · 90.87% cov · 22/22 task tests · dual security verify EARNED · 3-lens PASS · residue #1 closed with 2 e2e tests.
If RISK-ACCEPTED -> owner: n/a (clean PASS, no risk accepted)
Reviewed by: Tin Dang · date: 2026-07-15

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `proxy.platform_credential_fallback` served audits (how many tenants rely on fallback) · rate of `result=error` misconfig audits (platform-row-missing alarm) · 402 ERR_PROVIDER_KEY_MISSING rate (should DROP for platform-covered providers once default-ON).

### Decisions (ADR)
- [AI] specify — chose **wrapper OUTSIDE `resolve()`**; rejected modify `resolve()` to fall back internally (rejected — literally contradicts the frozen `ports.py:536` docstring and would make a request FOR the platform tenant's own missing key recurse/resolve nonsense) · reuse `resolve_platform_credential()` from the ops path (rejected — ops-mTLS-gated, unreachable from tenant-JWT proxy auth, and does NO precedence check).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (5 batches, direct build not delegated — held tight control of the security-sensitive seam). One VERIFY-phase refinement: reordered `mark_platform_fallback()` to the LAST statement in `_resolve_platform_fallback` (after the credential scope is bound) so the caller's `finally` always resets the M8 signal — closes a theoretical cross-request leak window a throw between mark and return would have opened.
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
- [SPEC · dropped] No end-to-end integration test of the wired default-ON path — CLOSED at VERIFY (Tin's gate hold): 2 DB-backed e2e tests now exercise the real create_app wiring serving a keyless tenant the platform credential. (evidence: test_platform_credential_fallback_e2e.py, 2026-07-15)
- [SPEC · seeded] Realtime-WS + memory-embeddings credential paths are NOT wired for fallback (out of the frozen 4-verb scope) — a keyless tenant there still gets 402. Revisit if fallback should extend to realtime. (evidence: grep of tenant_credential_resolver= call sites, 2026-07-15)

### Competency deltas
- [SDD · folded] Composing new behavior OUTSIDE a frozen fail-closed seam (a wrapper that catches the seam's own exception) let a hard "NEVER fallback" invariant be superseded for ONE caller without editing the frozen contract or weakening it for every other caller — a reusable pattern for "add an escape hatch to a fail-closed gate." (evidence: _resolve_platform_fallback composes over resolve() untouched) [folded foundation-version 53]
- [ADD · folded] For a security task, writing the red suite MYSELF (not delegating) then delegating only the adversarial VERIFY to an independent agent gave a genuine dual-lens without me marking my own homework. (evidence: self-authored 20 red tests + independent add-verify EARNED) [folded foundation-version 53]
