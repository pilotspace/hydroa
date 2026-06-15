# MILESTONE: Provider security & config hardening

goal: Every provider adapter's transport-error wrap suppresses the secret-bearing exception chain (from None), and Azure AD authority is env-configurable — closing the two v21 carried follow-ups; behavior-preserving, regression-guarded.
rationale: sub-milestone (project-lead/auto, 2026-06-15). Closes the two v21 carried follow-ups, one of which is a SECURITY finding the v21 azure-embeddings review surfaced as systemic: the shared execute_with_retry seam + openai/bedrock/gemini/anthropic/openrouter adapters wrap transport errors with `raise UpstreamUnavailableError(str(exc)) from exc`, which re-attaches the httpx request (carrying the upstream auth header / secret-bearing body) on `__cause__.request` — harvestable by any crash-reporter walking the chain. azure_ad + azure_embeddings already use `from None` (v21); this generalizes that bar across ALL adapters. Spans multiple frozen contracts, so it is its own milestone, not folded into v21. Behavior-preserving (same exception type + message; only the chain is suppressed).

stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  (1) A uniform secret-chain hardening: every provider transport-error wrap (the shared
     execute_with_retry seam + each adapter's stream()/post_json path) uses `from None` so the
     secret-bearing httpx request is never re-attached to the raised UpstreamUnavailableError.
     Regression tests assert `__cause__ is None` per adapter. (2) GATEWAY_AZURE_AD_AUTHORITY made
     env-configurable so resolve_azure_ad_config carries the authority (live edge can drive AAD).
Out: Changing the exception TYPE or message (behavior-preserving only). Reworking the breaker /
     retry semantics. Any new provider. Redacting secrets from logs elsewhere (transport-error
     chain only this milestone). Managed-identity/IMDS token source (still a carried delta).

## Shared decisions & glossary deltas   (living — every task must honor these)
- `from None` on a transport-error wrap is the project standard wherever the chained exception's
  `.request` could carry an upstream auth header or secret body (foundation v21 convention).
- Behavior-preserving: the raised exception TYPE (UpstreamUnavailableError) and MESSAGE (str(exc))
  are unchanged; only `__cause__` is suppressed. All existing provider regression suites stay green.

## Shared / risky contracts (freeze these first)
- The set of transport-error wrap sites + the `__cause__ is None` assertion shape -> owning task `provider-secret-chain-hardening`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] provider-secret-chain-hardening  depends-on: none — sweep all 14 secret-bearing `from exc`/`from terminal_exc` transport-error wraps (upstream_retry.py ×3 + openrouter/openai ×3/anthropic/gemini ×2/bedrock/bedrock_embeddings/azure stream) → `from None`; per-adapter regression test asserts UpstreamUnavailableError raised + `__cause__ is None`; all existing provider suites stay green (behavior-preserving).
- [ ] azure-ad-authority-config        depends-on: none — add GATEWAY_AZURE_AD_AUTHORITY Setting; resolve_azure_ad_config carries authority (falls back to DEFAULT_AUTHORITY); the live edge can then drive AAD. Tiny additive config change + a resolve test.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] Every provider adapter transport-error path raises UpstreamUnavailableError with `__cause__ is None` (no secret-bearing request reachable via the exception chain); all provider regression suites green.   (← provider-secret-chain-hardening)
- [ ] resolve_azure_ad_config honors GATEWAY_AZURE_AD_AUTHORITY (env-overridable, defaults to login.microsoftonline.com).   (← azure-ad-authority-config)
