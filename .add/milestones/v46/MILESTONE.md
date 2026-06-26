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
- [x] gemini-multimodal     depends-on: none              — extend `_openai_to_gemini_request` to translate text/image_url/video_url content parts → Gemini inlineData, data-URL only, with an inline size guard; string content byte-identical. No-DB unit tests. FREEZES the content-part + size-guard contract. (gate PASS, 22 tests; 38 frozen green)
- [x] vision-understanding-ui  depends-on: gemini-multimodal — dashboard `/app/vision` (pick a short video/image + prompt → chat to a Gemini model → render the answer) via the BFF; role-open nav entry. (gate PASS, 8 tests)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An API key holder can POST /v1/chat/completions to a Gemini model with a content array containing a text part + an inline image (and/or short video) data URL, and get a coherent answer that reflects the media; a string-content request is byte-identical to today; an over-cap inline payload is rejected before forwarding   (← gemini-multimodal)
- [x] A signed-in user can, in `/app/vision`, pick a short video/image, ask a question, and see the Gemini model's answer   (← vision-understanding-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : the Gemini adapter (`gemini_upstream.py`) now translates OpenAI multimodal content-part arrays → Gemini `inlineData` — `_content_to_gemini_parts` + `_data_url_to_inline` (strict data:URL parse, base64) wired into `_openai_to_gemini_request` (user + assistant-text; system stays text-only), with a per-request inline size guard (GATEWAY_GEMINI_INLINE_MAX_BYTES default 20 MiB → 413 before forward). String content is BYTE-IDENTICAL (the frozen Gemini suites stay green). Translation ValueErrors → clear 4xx (413 / 400 ERR_UNSUPPORTED_CONTENT_PART); existing ValueErrors re-raise unchanged. SSRF-safe: only data:URLs, never fetches remote. Reuse-only: rides /v1/chat/completions + streaming + billing; no new endpoint/dep. 22 no-DB tests.
- dashboard : NEW `/app/vision` workspace — a Gemini-only model select (honest "No Gemini model available" empty-state) + image/video file picker (→ data URL) + prompt + Ask → non-streaming multimodal POST /v1/chat/completions via the BFF → renders the answer; best-effort error handling (413 → "media too large"). lib/vision.ts builds the exact backend content-part shape. A role-open "Vision" nav entry. vitest 592 → 600 green; tsc 0; eslint 0.
- tooling / skill / book : untouched (only `.add/` bookkeeping).

### Cross-task evidence   (one row per task)
- gemini-multimodal : gate=PASS · tests=22 green (no-DB; the 3 FROZEN Gemini suites 38 green UNCHANGED proving byte-identical back-compat; make test-fast 206→228) · residue=Gemini Files API for large clips / >inline-cap = a scale delta; multimodal for OpenAI/Anthropic/Bedrock adapters = deltas; remote-URL fetch (with SSRF protection) = a delta. I read the helpers + the error mapping directly (existing ValueErrors re-raise; SSRF-safe).
- vision-understanding-ui : gate=PASS · tests=8 green (full dashboard 600, +8; tsc 0; eslint 0) · residue=streaming the answer + a richer media preview = deltas. The FE content-part shape matches the v46 backend by inspection (verified end-to-end).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (API key holder gets understanding from a Gemini model on inline image/video; string byte-identical; over-cap rejected before forward): gemini-multimodal — 22 tests incl. text+image / video / string-byte-identical / over-cap-413 / non-data-url / unknown-part; 38 frozen tests confirm back-compat.
  - EC2 (signed-in user gets a Gemini answer about a picked image/video in /app/vision): vision-understanding-ui — 8 tests incl. image/video part-shape + the answer render, over the EC1 path via the BFF.
- goal: a user (and any API key holder) can send a short video/image + a prompt to a Gemini model via /v1/chat/completions and get an understanding back, surfaced in the dashboard — proven by 22 gateway + 8 dashboard tests green (228 no-DB gateway incl. 38 frozen unchanged, 600 dashboard), reusing the ENTIRE existing chat/streaming/billing pipeline with ZERO new infra/dep, SSRF-safe (data-URL-only) and size-guarded (413 before forward).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] v46 commits land on the v40→v46 task stack (committed locally): t1 gemini-multimodal → t2 vision-understanding-ui → .add close. PUSH/PR await Tin's go-ahead (outward act).
- [ ] open a PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40–v46 are a stack — merge in order or retarget.
- [ ] deploy note: NO migration, NO new infra/dep. Optionally set GATEWAY_GEMINI_INLINE_MAX_BYTES (default 20 MiB). The /app/vision surface needs a Gemini model in the catalog with working credentials (else it shows the honest empty-state). Gemini Files API (large clips) = a documented scale delta.
- [ ] v46 joins the releasable set (v33–v45 already pending); bundle into the next release cut when Tin calls it (release.md).
