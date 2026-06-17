# MILESTONE: Provider config cleanup — v25 follow-ups

goal: the BYOK provider seam carries no dead config: OpenAI direct-chat has retry parity with the other 5 adapters, and the vestigial env-key boot guard is fully retired
rationale: small-cleanup bucket — the two carry-overs the v25 BYOK close flagged. Pure technical-debt
retirement on the just-shipped BYOK seam; no new product surface. (Tin chose to land these before the v27
UI↔BE coverage program.)
stage: production · status: done · created: 2026-06-17

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`.

## Scope
In:  (1) route OpenAIDirectProvider.complete() through the shared execute_with_retry seam + thread the
     shared upstream-retry Settings, so all 6 chat adapters share one retry policy; (2) delete the vestigial
     empty-upstream-key boot guard (no-op since v25 task-3) and re-pin the BYOK env-secret-absence invariants.
Out: a bespoke openai_max_retries knob (rejected — reuse the shared knobs); repurposing the guard to validate
     the Fernet key (rejected by Tin — out of cleanup scope); the v27 UI↔BE coverage program (separate milestone).

## Shared decisions & glossary deltas   (living — every task must honor these)
- The shared retry seam (`execute_with_retry` + `upstream_max_retries`/`upstream_retry_backoff_base_s`/
  `upstream_retry_deadline_s`) is the ONE retry policy for all chat adapters — no per-provider divergence.
- BYOK invariant: the platform Settings carries NO provider api_key/secret field; credentials are per-tenant
  at request time. Tests pin this against `Settings.model_fields` (a live surface), not a constant.

## Shared / risky contracts
- none shared across the two tasks — they touch disjoint code (openai_provider/main.py ctor vs config.py guard).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] openai-retry-parity      depends-on: none — OpenAI complete() → execute_with_retry; main.py threads the 3 settings.
- [x] retire-empty-key-guard   depends-on: none — delete EmptyUpstreamKeyError/_UPSTREAM_KEY_ENV_VARS/validate_upstream_keys.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A transient OpenAI 5xx/408/429 is retried per the shared policy (and at max_retries=0 surfaces exactly
      like the other 5 adapters); `app.state.chat_adapters["openai"]._max_retries == settings.upstream_max_retries`.   (← openai-retry-parity)
- [x] `gateway.core.config` exposes no boot-guard symbols and `create_app` no longer calls one; the gateway
      still boots and the BYOK env-secret-absence invariants stay green.   (← retire-empty-key-guard)
