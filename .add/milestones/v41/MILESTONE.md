# MILESTONE: Web search augmentation

goal: A signed-in user can toggle web search in the chat workspace so the assistant answers with up-to-date, web-grounded information via each provider's native grounding.
rationale: new-major → milestone 2 of 9 (program v40–v48, "AI Application Platform"). Tin 2026-06-26 chose "native grounding NOW, pluggable gateway search tool LATER" + "implement all, best decision". EXTENDS the v10 tool-use seam + v40 chat workspace; adds the first augmented-generation capability. DEPENDS-ON nothing external (uses each provider's BUILT-IN web search — no external search API/key this milestone).
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A single opt-in `web_search: true` request flag the gateway TRANSLATES to each provider's NATIVE web-search/grounding tool and STRIPS before sending upstream (so verbatim-passthrough providers don't 400 on an unknown field): OpenAI/OpenRouter → `{type:"web_search_preview"}`; Anthropic → `{type:"web_search_20250305",name:"web_search"}`; Gemini → `{googleSearch:{}}`. Gated by a default-OFF `GATEWAY_WEB_SEARCH_ENABLED` knob.
  - Grounding-citation passthrough: the Anthropic/Gemini response translators (which today DROP unrecognized blocks) preserve web-search results / `groundingMetadata` into the OpenAI-shaped response so sources reach the client (the recon's top risk).
  - Providers without native grounding (Bedrock, and Azure unless configured) → the flag is a safe no-op (no injection, no error) — honest capability.
  - Dashboard: a "Web search" toggle in the chat composer that sets `web_search` on the send; the frozen `useChatStream.SendInput` gains an ADDITIVE optional `webSearch?` field.
Out:
  - The pluggable gateway-side search tool (external Tavily/Brave/SerpAPI backend) — explicitly DEFERRED per Tin's "tool later" (a follow-up increment / later milestone; needs an external API key + per-search cost).
  - Rich in-thread citation RENDERING (clickable source cards) — the BE passes citations through; FE rich rendering is a deferred polish delta. v41 ships the toggle + grounded answers.
  - Per-model capability registry (there is none today) — the flag is best-effort per provider; non-grounding models no-op.
  - Any new external network dependency from the gateway (native grounding is provider-side; the gateway's existing timeout/retry/circuit-breaker covers the upstream call).

## Shared decisions & glossary deltas   (living — every task must honor these)
- WEB-SEARCH-FLAG (NEW glossary): a single top-level `web_search: bool` on the chat request. The gateway, when `GATEWAY_WEB_SEARCH_ENABLED` is on, maps it to the resolved provider's native grounding tool and REMOVES the raw flag from the upstream payload; default-OFF and a no-op on providers without native grounding. Never leaks to upstream verbatim.
- CITATION HONESTY: grounding sources the provider returns are passed through (Anthropic web_search_result / Gemini groundingMetadata) — surfaced, not fabricated; when a provider returns none, the client sees none (no invented sources). Mirrors v27/v38/v40 honesty.
- DEFAULT-OFF + FAIL-SAFE: the feature ships OFF; with the knob off OR the flag absent, behavior is byte-identical to today. A provider that rejects the native tool must not break the base chat path.

## Shared / risky contracts (freeze these first)
- web-search flag → native-tool translation + flag-strip + citation passthrough (the per-provider mapping + the OpenAI-response envelope for citations) -> owning task `websearch-grounding-passthrough`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] websearch-grounding-passthrough  depends-on: none                         — gateway maps `web_search:true` → provider-native grounding tool (OpenAI/OpenRouter/Anthropic/Gemini), strips the raw flag, no-ops on non-grounding providers, default-OFF knob; Anthropic/Gemini response translators preserve citations. FREEZES the web-search flag + citation envelope.
- [x] chat-websearch-toggle             depends-on: websearch-grounding-passthrough — dashboard composer "Web search" toggle → additive `useChatStream.SendInput.webSearch?` → `web_search:true` on the chat POST body.

## Exit criteria (observable; map each to the task that delivers it)
- [x] With the knob enabled, a chat request carrying `web_search:true` reaches each provider as that provider's NATIVE grounding tool (not a leaked raw flag), and grounding citations the provider returns survive into the OpenAI-shaped response   (← websearch-grounding-passthrough)
- [x] A user can flip a "Web search" toggle in `/app/chat` and the chat POST body carries `web_search:true`; with the knob off or flag absent, behavior is unchanged   (← chat-websearch-toggle)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `proxy/domain/web_search.py` (native tool map + citation normalizers) · `GATEWAY_WEB_SEARCH_ENABLED` knob · central knob-kill in `CompletionUseCase` + `deps.py` wire-up · per-provider injection in openrouter/openai/azure adapters (inject+strip) and anthropic/gemini request builders · anthropic/gemini non-stream citation passthrough into `response["grounding"]`. New `tests/web_search` (36) joined `make test-fast`. The raw `web_search` flag never reaches any upstream (security pin).
- dashboard : `useChatStream.SendInput` gains additive `webSearch?` → `web_search:true` on the chat POST; `ModelControls` "Web search" checkbox; `ChatWorkspace` state wiring. New `tests-bff/chat-websearch-toggle.test.tsx` (3). Off-path byte-identical to v40.
- tooling / skill / book : untouched (only `.add/` task + milestone bookkeeping).

### Cross-task evidence   (one row per task)
- websearch-grounding-passthrough : gate=PASS · tests=36 green (make test-fast 190) · residue=Anthropic/Gemini STREAM-citation + Gemini googleSearch+functionDeclarations limitation + live-verify of tool shapes (deltas). Independent sonnet refute-read: flag-strip security invariant PROVEN clean on every path (0.98); it caught + we fixed a deps wire-up BLOCKER (feature would've been dead), a groundingChunks-null crash, and 2 tautological tests.
- chat-websearch-toggle : gate=PASS · tests=3 green (full dashboard suite 541) · residue=promote toggle to a visible composer affordance + rich citation rendering (deltas).

### Goal met?   (map the evidence back to this milestone's Exit criteria)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change
  - EC1 (flagged request → native tool, not a leaked flag; citations survive): websearch-grounding-passthrough — per-provider captured-body asserts + flag-absence security pins on complete()+stream() for all providers; Anthropic/Gemini non-stream citations → response["grounding"]; OpenAI/OpenRouter survive verbatim.
  - EC2 (user toggles Web search; off ⇒ unchanged): chat-websearch-toggle — `test_toggle_on_sends_flag` (body.web_search===true) + `test_toggle_off_no_flag` (no key, v40 body intact) + full v40 suite green.
- goal: a signed-in user toggles web search in `/app/chat` and the assistant answers with web-grounded info via each provider's native grounding — proven by gateway `make test-fast` 190 green + dashboard 541 green + `next build` OK, default-OFF so production is unaffected until the knob is enabled.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] v41 commits land on `feat/v41-web-search` (stacked on v40): t1 gateway grounding → t2 dashboard toggle → .add close. (committed locally; PUSH/PR awaits Tin's go-ahead — outward act.)
- [ ] open PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40 + v41 are a stack — merge v40 first or retarget.
- [ ] enable `GATEWAY_WEB_SEARCH_ENABLED=true` in a deploy + run the deferred live-verify of the provider tool shapes before announcing the feature.
- [ ] v41 joins the releasable set (v33–v40 already pending); bundle into the next release cut when Tin calls it (release.md).
