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
- [ ] websearch-grounding-passthrough  depends-on: none                         — gateway maps `web_search:true` → provider-native grounding tool (OpenAI/OpenRouter/Anthropic/Gemini), strips the raw flag, no-ops on non-grounding providers, default-OFF knob; Anthropic/Gemini response translators preserve citations. FREEZES the web-search flag + citation envelope.
- [ ] chat-websearch-toggle             depends-on: websearch-grounding-passthrough — dashboard composer "Web search" toggle → additive `useChatStream.SendInput.webSearch?` → `web_search:true` on the chat POST body.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] With the knob enabled, a chat request carrying `web_search:true` reaches each provider as that provider's NATIVE grounding tool (not a leaked raw flag), and grounding citations the provider returns survive into the OpenAI-shaped response   (← websearch-grounding-passthrough)
- [ ] A user can flip a "Web search" toggle in `/app/chat` and the chat POST body carries `web_search:true`; with the knob off or flag absent, behavior is unchanged   (← chat-websearch-toggle)

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
