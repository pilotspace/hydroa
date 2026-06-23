# MILESTONE: Helios agent-coding integration readiness

goal: A real AI coding agent (Helios) can drive sustained coding sessions through the proxy — streaming tool-calls, prompt caching, and reasoning all working with accurate cost tracking and graceful behavior under load — proven by a CI-gated stub suite and a live double-pass.
rationale: new-major — a new product theme no archived milestone's goal covers: proving and hardening the proxy for a real AI coding agent (Helios, ../helios-mono) before integration. Helios speaks the OpenAI Chat Completions wire to the proxy (it sends `tools`, streaming, `role:tool`, `reasoning_effort`/`reasoning.effort`), so the proxy must faithfully translate those into each provider's native form and account cost accurately under sustained agent-loop traffic. Confirmed via intake interview 2026-06-23 (Tin: both test layers · ai-proxy-primary + real cross-repo smoke · all four surfaces).
stage: production · status: active · created: 2026-06-23

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Prove + harden the four agent-coding surfaces on the `/v1/chat/completions` chat path:
     (1) parallel tool-calls under streaming SSE, (2) prompt-caching passthrough + token
     accounting, (3) reasoning/thinking passthrough + token accounting, (4) concurrency/load
     back-pressure + cross-provider partial-disconnect billing. Plus the two verification
     layers: a deterministic CI-gated stub suite replaying Helios's real OpenAI-wire request
     shapes, and a live double-pass where Helios (config-only) drives a real coding task
     through the proxy against real providers.
Out: Helios code/provider changes (config-only pointing at the proxy — no edits to ../helios-mono);
     the 43 control-plane/admin-UI SPEC deltas unrelated to agent coding; new providers; image/
     audio modalities; the actual Helios↔proxy production cut-over (this milestone proves readiness,
     it does not flip Helios's default).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Helios is an **OpenAI-wire client** of the proxy: it sends OpenAI Chat Completions JSON
  (`tools`, `tool_choice`, `stream`, `role:"tool"`, `reasoning_effort`, `reasoning.effort`).
  The proxy's job is to TRANSLATE those into each provider's native shape — never require the
  client to speak a provider-native dialect.
- INVARIANT (carry from v9/v10): a request that engages none of these surfaces stays
  BYTE-IDENTICAL to the current default path; OpenRouter/OpenAI passthrough is unchanged.
- Cost accuracy is a first-class exit gate: cached-token and reasoning-token rows must bill at
  their catalog rates; partial-stream disconnects must never silently bill $0 where tokens were
  served (extends [[stream-disconnect-billing]] / [[disconnect-provider-cost]] beyond OpenRouter).
- Every new behavior is proven first by the stub harness (deterministic, in CI), then confirmed
  by the live double-pass — the project's standing live-verify rule.

## Shared / risky contracts (freeze these first)
- Helios request-shape fixtures + faithful stub-upstream contract  -> owning task `agent-coding-stub-harness`
- OpenAI-wire ⇄ provider-native mapping for cache hints            -> owning task `prompt-cache-passthrough`
- OpenAI-wire ⇄ provider-native mapping for reasoning/thinking      -> owning task `reasoning-passthrough`
- Cross-provider partial-disconnect billing record shape           -> owning task `disconnect-billing-all-providers`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] agent-coding-stub-harness        depends-on: none                       — deterministic suite replaying Helios's real OpenAI-wire bodies (streaming chat · tools · parallel tools · multi-turn role:tool · reasoning_effort) against a faithful stub upstream; joins make test-fast / CI
- [ ] parallel-tool-streaming-verify   depends-on: agent-coding-stub-harness  — prove + fix ≥2 simultaneous streamed tool_calls for Anthropic · Gemini · Bedrock (code claims it; untested today)
- [ ] prompt-cache-passthrough         depends-on: agent-coding-stub-harness  — forward OpenAI-wire cache hints → Anthropic/Gemini native cache_control; surface + bill cache_creation/cache_read tokens
- [ ] reasoning-passthrough            depends-on: agent-coding-stub-harness  — translate reasoning_effort/reasoning.effort → Anthropic thinking / Gemini thinkingConfig; extract reasoning tokens from native responses; relay thinking SSE
- [ ] disconnect-billing-all-providers depends-on: agent-coding-stub-harness  — extend partial-token disconnect cost estimate/recovery beyond OpenRouter (Anthropic/Gemini bill $0 on partial today)
- [ ] concurrency-load-guard           depends-on: agent-coding-stub-harness  — back-pressure/capacity behavior + a concurrent agent-loop load test
- [ ] helios-live-smoke                depends-on: parallel-tool-streaming-verify, prompt-cache-passthrough, reasoning-passthrough, disconnect-billing-all-providers — real cross-repo double-pass: Helios config-only → proxy, runs an actual coding task against real providers (scripts/live_helios_verify.py)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] Streamed parallel tool-calls (≥2 in one turn) reach the client correctly indexed for Anthropic·Gemini·Bedrock   (← parallel-tool-streaming-verify)
- [ ] An OpenAI-wire request with cache hints activates provider caching; cache_creation/cache_read tokens are billed  (← prompt-cache-passthrough)
- [ ] An OpenAI-wire reasoning_effort/reasoning.effort request activates extended thinking on Anthropic/Gemini and reasoning tokens are billed  (← reasoning-passthrough)
- [ ] A mid-stream client disconnect on any provider records served tokens / cost — never a silent $0  (← disconnect-billing-all-providers)
- [ ] The proxy stays responsive and back-pressures (not unbounded queueing) under a sustained concurrent agent-loop load test  (← concurrency-load-guard)
- [ ] All four surfaces are green in CI via the stub harness  (← agent-coding-stub-harness)
- [ ] Helios, pointed at the proxy by config only, completes a real coding task against real providers — live double-pass ×2  (← helios-live-smoke)

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
