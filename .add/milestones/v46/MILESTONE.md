# MILESTONE: Video & image understanding (Gemini multimodal)

goal: A user (and any API key holder) can send a short video or image plus a prompt to a Gemini model via /v1/chat/completions and get an understanding/answer back — surfaced in the dashboard — reusing the existing chat/streaming/billing pipeline with zero new infra.
rationale: new-major → milestone 7 of 9 (program v40–v48). Tin 2026-06-26 checkpoint: "reuse-only MVPs, keep full-auto" + "native Gemini multimodal" ([[v46-v48-reuse-only-decision]]). The proxy already routes chat to Gemini but its OpenAI→Gemini translation flattens message content with `str(content)` — so a multimodal content-part array (text + image + video) is lost. v46 closes that gap so a Gemini model can SEE an image/short-video sent inline, reusing the ENTIRE existing chat/streaming/billing pipeline. Zero new infra/deps; the Gemini Files API (large clips / >inline-limit) is a documented scale delta.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Extend `gemini_upstream.py` `_openai_to_gemini_request` to translate OpenAI multimodal **content-part arrays** into Gemini parts: a text part → `{text}`; an `image_url` part whose url is a `data:` URL → `{inlineData:{mimeType, data}}` (decode the base64); a `video_url` part (data URL) → `{inlineData:{mimeType:"video/…", data}}`. A plain string content stays byte-identical. A size guard rejects an inline payload over a configurable cap BEFORE forwarding. Pure translation-layer change — unit-tested with no live provider; joins make test-fast.
  - Dashboard: an `/app/vision` surface — choose a short video/image + type a prompt → POST /v1/chat/completions to a Gemini model with a multimodal content array → render the model's answer. Via the BFF. Role-open.
Out:
  - Gemini **Files API** / fileUri (large clips, >inline cap, GCS) — a documented SCALE delta (needs a provider-side upload flow).
  - Multimodal for OpenAI/Anthropic/Bedrock adapters (OpenAI already passes image parts through verbatim; the others are deltas). v46 = the Gemini gap + a UI only.
  - Fetching remote http(s) media URLs server-side (SSRF surface) — only `data:` URLs (inline base64) are supported; http(s) image/video URLs = a delta.
  - Streaming-specific multimodal nuances beyond what the existing stream path already does (the translation is request-shape only; response streaming is unchanged).

## Shared decisions & glossary deltas   (living — every task must honor these)
- MULTIMODAL CONTENT PART (NEW glossary): an OpenAI chat `content` may be a list of parts: `{type:"text", text}` · `{type:"image_url", image_url:{url}}` · `{type:"video_url", video_url:{url}}`. Only `data:` URLs (inline base64) are honored by the Gemini translation; a non-data url → ValueError → a clear 4xx (delta: Files API).
- INLINE SIZE GUARD (design-for-failure): GATEWAY_GEMINI_INLINE_MAX_BYTES (default ~20 MiB, Gemini's inline request ceiling); the SUM of decoded inline bytes over the cap → a clear 4xx (ERR_PAYLOAD_INPUT_TOO_LONG / a new ERR) BEFORE forwarding. No partial/huge request to Gemini.
- REUSE: no new endpoint, no new provider, no new dep — the change rides `/v1/chat/completions` + the existing Gemini adapter + streaming + billing. The FE uses an existing Gemini model id from the catalog.
- BACK-COMPAT (HARD): a string-content request must translate BYTE-IDENTICALLY to today (the frozen Gemini tool-use / json-mode / reasoning tests must stay green).
- FE honors WCAG-AA + v23/v24 tokens + the four states + best-effort error handling; all gateway calls via the BFF (no tenant id from the FE).

## Shared / risky contracts (freeze these first)
- The OpenAI→Gemini multimodal content-part translation + the inline size guard + the data-URL-only rule + byte-identical string back-compat -> owning task `gemini-multimodal`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] gemini-multimodal     depends-on: none              — extend `_openai_to_gemini_request` to translate text/image_url/video_url content parts → Gemini inlineData, data-URL only, with an inline size guard; string content byte-identical. No-DB unit tests in tests/gemini_provider. FREEZES the content-part + size-guard contract.
- [ ] vision-understanding-ui  depends-on: gemini-multimodal — dashboard `/app/vision` (pick a short video/image + prompt → chat to a Gemini model → render the answer) via the BFF; role-open nav entry.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can POST /v1/chat/completions to a Gemini model with a content array containing a text part + an inline image (and/or short video) data URL, and get a coherent answer that reflects the media; a string-content request is byte-identical to today; an over-cap inline payload is rejected before forwarding   (← gemini-multimodal)
- [ ] A signed-in user can, in `/app/vision`, pick a short video/image, ask a question, and see the Gemini model's answer   (← vision-understanding-ui)

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
