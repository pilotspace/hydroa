# TASK: GATEWAY_AZURE_AD_AUTHORITY env-configurable (resolve_azure_ad_config carries authority)

slug: azure-ad-authority-config · created: 2026-06-15 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- additive, behavior-preserving config knob; risk: low. The authority is a non-secret URL with a
     safe public-cloud default; no new attack surface (the token mint already uses config.authority). -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/core/config.py:Settings` — add `azure_ad_authority: str = ""`
    (env `GATEWAY_AZURE_AD_AUTHORITY`, env_prefix=`GATEWAY_`), mirroring the existing
    `azure_ad_scope` field at config.py:266.
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py:resolve_azure_ad_config` (line 58) —
    read `getattr(settings, "azure_ad_authority", "") or DEFAULT_AUTHORITY` and pass `authority=` to
    AzureADConfig (today it omits authority, so it ALWAYS uses DEFAULT_AUTHORITY — the bug).
Already present (NOT changed): `AzureADConfig.authority: str = DEFAULT_AUTHORITY` (azure_ad.py:55) and
  `AzureADTokenProvider._token_url()` (azure_ad.py:112) which ALREADY uses `self._config.authority` —
  so once resolve carries the value, the override reaches the token URL end-to-end with no other edit.
  `DEFAULT_AUTHORITY = "https://login.microsoftonline.com"` (azure_ad.py:41).
Context (working folder): `apps/gateway/src/gateway/` (config + azure_ad) + a new test dir
  `apps/gateway/tests/azure_ad_authority/`.
Honors (patterns / conventions): the `azure_ad_scope` precedent — empty env → typed default; opt-in;
  authority is a non-secret URL (NOT field(repr=False)). v22 behavior-preserving bar (default unchanged).
Anchors the contract cites: `Settings.azure_ad_authority`, `resolve_azure_ad_config`, `AzureADConfig.authority`,
  `DEFAULT_AUTHORITY`, `GATEWAY_AZURE_AD_AUTHORITY`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure AD authority is env-configurable so sovereign/government clouds (e.g.
  login.microsoftonline.us, login.partner.microsoftonline.cn) can drive AAD token acquisition;
  defaults to the public cloud when unset (byte-identical to today).
Framings weighed: read-in-resolve + new Setting (chosen — mirrors azure_ad_scope exactly, one seam) ·
  hardcode-only (rejected — the carried v21 follow-up is precisely that this is NOT configurable) ·
  per-deployment authority (rejected — out of scope; one authority per app, like scope).
Must:
<must>
  - Settings exposes `azure_ad_authority: str = ""` bound to env `GATEWAY_AZURE_AD_AUTHORITY`.
  - resolve_azure_ad_config carries the authority into AzureADConfig: a non-empty setting value is used;
    an empty/unset value falls back to DEFAULT_AUTHORITY (public cloud).
  - The configured authority reaches the minted-token URL: AzureADTokenProvider._token_url() begins with
    `{authority}/{tenant}/oauth2/v2.0/token` (end-to-end, via the already-present config.authority use).
  - Behavior-preserving when unset: with no GATEWAY_AZURE_AD_AUTHORITY, resolve yields authority ==
    DEFAULT_AUTHORITY (byte-identical to pre-change). AAD opt-in gating (tenant+client+secret) unchanged.
</must>
Reject:
<reject>
  - GATEWAY_AZURE_AD_AUTHORITY set but AAD otherwise unconfigured (no tenant/client/secret) -> resolve still
    returns None (the authority alone does NOT enable AAD — gating is unchanged).
</reject>
After:
<after>
  - An operator can set GATEWAY_AZURE_AD_AUTHORITY=https://login.microsoftonline.us and AAD tokens mint
    against that sovereign authority; unset = public cloud, exactly as before.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ none material — biggest risk: a stale-default regression (resolve silently keeping DEFAULT when an
    override is set). Mitigated by a RED test that sets the override and asserts the carried value, plus a
    defaults test; cost: low (caught immediately).
  - [x] _token_url already consumes config.authority — confirmed at azure_ad.py:112 (no token-provider edit needed).
  - [x] azure_ad_scope is the exact precedent for an empty-env → typed-default optional AAD setting — confirmed config.py:266.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: configured authority is carried into the resolved config
  Given settings with tenant+client+secret AND azure_ad_authority="https://login.microsoftonline.us"
  When resolve_azure_ad_config(settings) is called
  Then it returns an AzureADConfig whose authority == "https://login.microsoftonline.us"

Scenario: unset authority falls back to the public-cloud default (behavior-preserving)
  Given settings with tenant+client+secret AND azure_ad_authority="" (unset)
  When resolve_azure_ad_config(settings) is called
  Then it returns an AzureADConfig whose authority == DEFAULT_AUTHORITY

Scenario: configured authority reaches the minted-token URL end-to-end
  Given an AzureADTokenProvider built from a config carrying a custom authority
  When _token_url() is computed
  Then it begins with "{custom-authority}/{tenant}/oauth2/v2.0/token"

Scenario: authority alone does not enable AAD
  Given settings with azure_ad_authority set but NO tenant/client/secret
  When resolve_azure_ad_config(settings) is called
  Then it returns None (opt-in gating unchanged)

Scenario: the Settings field binds to the GATEWAY_AZURE_AD_AUTHORITY env var
  Given GATEWAY_AZURE_AD_AUTHORITY in the environment
  When Settings is constructed
  Then settings.azure_ad_authority equals that value (env_prefix GATEWAY_)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Settings (core/config.py):
  azure_ad_authority: str = ""            # env GATEWAY_AZURE_AD_AUTHORITY (env_prefix GATEWAY_)

resolve_azure_ad_config(settings) -> AzureADConfig | None   (azure_ad.py):
  gating UNCHANGED: returns None unless tenant_id AND client_id AND client_secret are all truthy.
  NEW: authority = getattr(settings, "azure_ad_authority", "") or DEFAULT_AUTHORITY
       AzureADConfig(..., scope=scope, authority=authority)

AzureADConfig.authority (UNCHANGED, already present): str = DEFAULT_AUTHORITY
AzureADTokenProvider._token_url() (UNCHANGED, already present):
  f"{config.authority.rstrip('/')}/{config.tenant_id}/oauth2/v2.0/token"

Schema: none (config only; no DB, no wire change).
```

Least-sure flag surfaced at freeze: [contract] none material — this is the smallest possible additive
seam (one Setting field + one resolver line), mirroring azure_ad_scope exactly; the only risk is a
stale default, which the RED carries-authority test catches directly. Default path is byte-identical.
Status: FROZEN @ v1 — approved by Tin Dang (auto-mode delegated; risk:low additive config, behavior-preserving)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (one test per scenario)
Plan:
<test_plan>
  - test_resolve_carries_authority_from_settings: SimpleNamespace(tenant/client/secret + azure_ad_authority=US)
    → AzureADConfig.authority == US.  (RED today: resolve omits authority → DEFAULT.)
  - test_resolve_defaults_authority_when_unset: same minus authority → authority == DEFAULT_AUTHORITY.
  - test_token_url_uses_configured_authority: AzureADTokenProvider(config with custom authority)._token_url()
    startswith "{authority}/{tenant}/oauth2/v2.0/token".
  - test_authority_alone_does_not_enable_aad: SimpleNamespace(no secret) + authority set → resolve returns None.
  - test_settings_binds_env_var: Settings(... azure_ad_authority via env/kwarg) → field equals value.
</test_plan>

Tests live in: `./tests/` · declared: `azure_ad_authority/test_azure_ad_authority.py` · MUST run red before Build.
<!-- token: `azure_ad_authority/...` contains "/" → resolves from project root: apps/gateway/tests/azure_ad_authority/ -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py` `apps/gateway/tests/azure_ad_authority/`
Strategy (ordered batches): 1. red suite 2. add Settings.azure_ad_authority 3. resolve carries authority 4. green + azure_aad regression (behavior-preserving) 5. pyright + ruff.
Safety rule (feature-specific): default unchanged (empty → DEFAULT_AUTHORITY); authority is non-secret (no repr-hide needed).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any frozen test or the contract; allow-list packages only; do not alter AAD gating.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — azure_ad_authority 5/5; regression azure_aad + azure_auth_routing + azure_chat/streaming/embeddings/verify + boot-guard + smoke = 74 green.
- [x] coverage did not decrease — net +5 tests; additive field + one resolver line (no branch removed).
- [x] no test or contract was altered during build — §3 contract untouched; test file formatted BEFORE the tests→build snapshot (tripwire clean, no re-cross needed).
- [x] the green was EARNED — test_resolve_carries_authority_from_settings was RED first (resolve dropped the value → DEFAULT) and test_settings_binds_env_var RED (no field, AttributeError) → both GREEN after wiring; 3 invariant guards (default fallback, token-URL consumption, gating-unchanged) green throughout.
- [x] concurrency / timing safe — pure config read at resolve time; no shared state, no I/O.
- [x] no exposed secrets — authority is a non-secret URL (NOT repr-hidden, correctly); client_secret handling unchanged (still field(repr=False)).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors the azure_ad_scope optional-setting precedent exactly; no new deps.
- [x] a person reviewed and approved the change — auto-mode (project-lead, delegated); risk:low additive config, behavior-preserving (default path byte-identical).

### Deep checks
- [x] WIRING — Settings.azure_ad_authority (config.py) → read by resolve_azure_ad_config (getattr ... or DEFAULT_AUTHORITY) → AzureADConfig.authority → AzureADTokenProvider._token_url(); end-to-end proven by test_token_url_uses_configured_authority + test_settings_binds_env_var.
- [x] DEAD-CODE — none (one field + one assignment + one kwarg).
- [x] SEMANTIC — behavior-preserving (unset → DEFAULT_AUTHORITY, byte-identical) confirmed by the full azure regression (74 tests) staying green.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-mode (project-lead, delegated by Tin Dang) · date: 2026-06-15

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): n/a (config knob).
Spec delta for the next loop: managed-identity/IMDS token source is still the carried AAD delta (next AAD arc) — when added, it should compose with the now-configurable authority.

### Competency deltas
<!-- tagged by competency (DDD · SDD · UDD · TDD · ADD), status open, with evidence -->
- [DDD · open] a partially-wired seam can hide for a whole milestone: AzureADConfig.authority + _token_url() consumed the authority since v21, but resolve never sourced it — the field looked configurable but wasn't. Evidence: the carries-authority test was the only thing that exposed the gap. Lesson: an end-to-end test (settings→URL) catches "looks wired, isn't" that a unit test on either end misses.
- [TDD · open] keeping the invariant guards (default-fallback, gating-unchanged) GREEN from the start while only the new-behavior tests go RED cleanly separates "new capability" from "must-not-regress" in the same suite. Evidence: 2 red / 3 green at the red run, all 5 green after a 2-line change.
