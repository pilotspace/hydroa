# TASK: Gemini adapter: OpenAI multimodal content-parts -> inlineData + size guard

slug: gemini-multimodal · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` — ADD `_content_to_gemini_parts(content, *, max_inline_bytes) -> list[dict]` (str → [{text}]; list-of-parts → text/inlineData) + a `_data_url_to_inline(url) -> (mimeType, b64data)` helper. WIRE it into `_openai_to_gemini_request` at the user branch (line 271) + the assistant-text branch (line 251). System messages stay text-only (line 247). Thread an inline byte budget through `_openai_to_gemini_request` (a new kwarg, summed across all parts in the request).
  - `apps/gateway/src/gateway/core/config.py` (MODIFY, additive) — `gemini_inline_max_bytes: int = Field(default=20_971_520, ge=0)` (20 MiB Gemini inline ceiling; 0 = unlimited).
  - the caller of `_openai_to_gemini_request` inside `GeminiCompletionUpstream` (≈line 658) — pass `max_inline_bytes` from settings (find how the upstream gets settings/default_max_tokens today and mirror it).
  - `apps/gateway/tests/gemini_provider/` (or a NEW `tests/gemini_multimodal/`) — no-DB unit tests on the pure translation + the size guard.
Context (working folder):
  - `_openai_to_gemini_request(payload, *, default_max_tokens)` at gemini_upstream.py:207 currently does `str(msg.get("content",""))` at lines 247/251/271 — flattening a list content to a Python repr (broken multimodal). The chat use-case (use_cases.py:610) validates `messages` is a non-empty list but does NOT parse `content` → a list content passes through verbatim to this adapter. So this is the ONLY place to change for Gemini multimodal.
  - OpenAI content-part shapes: `{type:"text", text}` · `{type:"image_url", image_url:{url}}` · (NEW) `{type:"video_url", video_url:{url}}`. A data URL = `data:<mime>;base64,<B64>`.
  - Gemini part shapes: `{text: "..."}` and `{inlineData: {mimeType: "...", data: "<base64>"}}`.
  - Error catalog: reuse PAYLOAD_INPUT_TOO_LONG (413) for over-cap inline; a clear 4xx (ValueError → mapped) for a non-data URL / malformed data URL.
Honors (patterns / conventions):
  - BACK-COMPAT (HARD): a string `content` must translate BYTE-IDENTICALLY to today — the frozen gemini_tool_use / gemini_json_mode / gemini_provider / reasoning tests MUST stay green. The new path only triggers when `content` is a list.
  - DESIGN-FOR-FAILURE: decode the data URL strictly; a non-data url or bad base64 → ValueError → a clear 4xx (NOT a 500); the summed inline bytes over the cap → 413 BEFORE forwarding (no huge request to Gemini).
  - Pure translation-layer change — unit-testable with NO live provider; joins make test-fast (no DB, no network).
Anchors the contract cites:
  - `_content_to_gemini_parts` · `_data_url_to_inline` · `_openai_to_gemini_request` (new max_inline_bytes kwarg) · the inlineData shape · the size-guard + data-URL-only rules · `gemini_inline_max_bytes` config.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Gemini understands inline images/short video — the OpenAI→Gemini request translation now maps a multimodal content-part array (text + image_url + video_url data URLs) to Gemini `inlineData`, with an inline size guard, while keeping string-content byte-identical.
Framings weighed: extend the existing `_openai_to_gemini_request` translation in-place with a content-part helper (chosen — pure translation, zero new infra/endpoint/dep, fully unit-testable, reuses chat/streaming/billing) · a dedicated `/v1/video/understand` endpoint (rejected — a new surface for what is just a chat request) · server-side frame extraction (rejected by Tin — needs ffmpeg, a heavy dep) · fetching remote media URLs (rejected — SSRF surface; data-URL-only for the MVP).
Must:
<must>
  - M1 — when a message `content` is a LIST, each part is translated: `{type:"text"}` → `{text}`; `{type:"image_url", image_url:{url:data-URL}}` → `{inlineData:{mimeType, data}}`; `{type:"video_url", video_url:{url:data-URL}}` → `{inlineData:{mimeType, data}}`. Order preserved within the message.
  - M2 — when `content` is a STRING, translation is BYTE-IDENTICAL to today (the existing frozen Gemini tests stay green).
  - M3 — the summed decoded inline bytes across the whole request are checked against `gemini_inline_max_bytes` (default 20 MiB; 0=unlimited); over-cap → 413 BEFORE the upstream call.
  - M4 — a data URL is decoded strictly (`data:<mime>;base64,<B64>`); mimeType is taken from the data URL; the base64 `data` is the part after the comma (no `data:` prefix).
  - M5 — applies to user messages and assistant-text messages; system messages remain text-only (Gemini systemInstruction is text); tool/functionResponse messages are unchanged.
</must>
Reject:
<reject>
  - a content-part `url` that is NOT a `data:` URL (e.g. http(s)://) -> a clear 4xx (ERR — "only inline data: URLs supported"); never fetched server-side (SSRF), never a 500.
  - a malformed data URL / invalid base64 -> a clear 4xx (same), not a 500.
  - summed inline bytes over the cap -> 413 (before forwarding).
  - an unknown part `type` -> a clear 4xx (don't silently drop media the user expects the model to see).
</reject>
After:
<after>
  - A Gemini chat request carrying a text part + an inline image (and/or short video) data URL is forwarded to Gemini as `inlineData` parts and the model can answer about the media; a string-content request is byte-identical to before; an over-cap or non-data-URL request is rejected with a clear 4xx, never a 500.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Gemini `inlineData` accepts video mimeTypes (video/mp4, …) inline for short clips within the request ceiling — lowest confidence because the exact inline video support/limits vary by model; if wrong (a model rejects inline video) Gemini returns its own 4xx which the existing `_gemini_error_to_openai` surfaces faithfully (no proxy crash). Cost if wrong: video understanding degrades to image-only until the Files API delta; images are unaffected. The translation itself is correct regardless.
  - [x] string content stays byte-identical — GUARANTEED by gating the new path on `isinstance(content, list)`.
  - [x] content passes through to the adapter unparsed — CONFIRMED (use_cases.py:610 only checks messages is a list).
  - [ ] the inline ceiling — using 20 MiB (Gemini's documented inline request ceiling) as the default cap; tunable via config.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Text + inline image translates to inlineData
  Given a user message content [{type:"text", text:"what is this?"}, {type:"image_url", image_url:{url:"data:image/png;base64,iVBOR..."}}]
  When _openai_to_gemini_request runs
  Then the user content parts are [{text:"what is this?"}, {inlineData:{mimeType:"image/png", data:"iVBOR..."}}] in order

Scenario: Inline short video translates to inlineData
  Given a content part {type:"video_url", video_url:{url:"data:video/mp4;base64,AAAA..."}}
  When translated
  Then it becomes {inlineData:{mimeType:"video/mp4", data:"AAAA..."}}

Scenario: String content is byte-identical (back-compat)
  Given a user message content "hello"
  When translated
  Then the user content is {role:"user", parts:[{text:"hello"}]} exactly as today
  And the frozen gemini_tool_use / json_mode / provider tests stay green

Scenario: Over-cap inline rejected before forward
  Given gemini_inline_max_bytes=8 and an inline payload decoding to 9 bytes
  When translated
  Then a 413 (ERR_PAYLOAD_INPUT_TOO_LONG) is raised and NO upstream request is made

Scenario: Non-data URL rejected (rejection)
  Given a content part {type:"image_url", image_url:{url:"https://evil.example/x.png"}}
  When translated
  Then a clear 4xx is raised (only data: URLs supported); the URL is never fetched
  And no upstream request is made

Scenario: Malformed data URL / bad base64 rejected (rejection)
  Given url "data:image/png;base64,!!!not-b64!!!"
  When translated
  Then a clear 4xx is raised, not a 500

Scenario: Unknown part type rejected (rejection)
  Given a content part {type:"audio_url", ...}
  When translated
  Then a clear 4xx is raised (media not silently dropped)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP surface — this rides POST /v1/chat/completions (model=gemini-*). Pure request-translation change.

_content_to_gemini_parts(content: str | list, *, max_inline_bytes: int, running_total: list[int]) -> list[dict]
  - str  -> [{"text": content}]                              (byte-identical to today)
  - list -> for each part by "type":
      "text"      -> {"text": part["text"]}
      "image_url" -> {"inlineData": _data_url_to_inline(part["image_url"]["url"], max_inline_bytes, running_total)}
      "video_url" -> {"inlineData": _data_url_to_inline(part["video_url"]["url"], max_inline_bytes, running_total)}
      other       -> raise ValueError("unsupported_content_part")
  - a part missing its text/url -> ValueError

_data_url_to_inline(url, max_inline_bytes, running_total) -> {"mimeType","data"}
  - must match ^data:(?P<mime>[^;,]+);base64,(?P<b64>.*)$  else ValueError("only_data_url_supported")
  - decoded = base64.b64decode(b64, validate=True)  (ValueError on bad b64)
  - running_total[0] += len(decoded); if max_inline_bytes>0 and running_total[0] > max_inline_bytes -> raise (mapped to 413)
  - returns {"mimeType": mime, "data": b64}   (Gemini wants the base64 string, not raw bytes)

Wiring in _openai_to_gemini_request(payload, *, default_max_tokens, max_inline_bytes):
  - new kwarg max_inline_bytes (the caller passes settings.gemini_inline_max_bytes); a single running_total list shared across all messages.
  - line 251 (assistant text): parts = _content_to_gemini_parts(msg.get("content",""), ...)
  - line 271 (user):           parts = _content_to_gemini_parts(msg.get("content",""), ...)
  - line 247 (system): UNCHANGED — system stays str(content) (Gemini systemInstruction is text-only).

Error mapping (in GeminiCompletionUpstream / the use-case): ValueError("only_data_url_supported"|"unsupported_content_part"|bad-b64)
  -> a clear 4xx ProblemError (e.g. ERR_UNSUPPORTED_CONTENT_PART, 400); the size-cap ValueError -> PAYLOAD_INPUT_TOO_LONG (413).
  (Mirror how the existing ValueError("tool_call_id_required") is surfaced — find + reuse that mapping path.)

Config (additive): gemini_inline_max_bytes: int = 20_971_520  (20 MiB; 0 = unlimited)
```

Status: FROZEN @ v1 — auto-approved (reuse-only MVP per Tin's checkpoint; pure translation, zero new infra/dep/endpoint; back-compat guaranteed by the list-gate). NOT a security HARD-STOP, but note the SSRF-avoidance (data-URL-only, never fetch remote) is a deliberate safety choice baked into the contract. 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] inline VIDEO support — Gemini inline video acceptance/limits vary by model; if a model rejects inline video, Gemini's own 4xx is surfaced faithfully (no proxy crash) and images still work. The TRANSLATION is correct regardless; live video is the Files-API delta. Cost: video may degrade to image-only until the delta.
  - [contract] SSRF avoidance — only `data:` URLs are honored; an http(s) url is REJECTED, never fetched server-side. If this were relaxed later it MUST add SSRF protection. Cost if wrong (if we fetched): SSRF. (Baked into the reject list.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral, no-DB/no-network — call `_openai_to_gemini_request` (and the helpers) directly; join make test-fast.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_text_plus_image_inline: list content → parts [{text},{inlineData:{mimeType:"image/png",data}}] in order.
  - test_video_inline: video_url data URL → {inlineData:{mimeType:"video/mp4",data}}.
  - test_string_content_byte_identical: "hello" → {role:"user",parts:[{text:"hello"}]} (assert equality with the pre-change shape).
  - test_over_cap_413: cap=8, 9-byte inline → raises the size-cap error (maps to 413); assert it raises BEFORE returning a body.
  - test_non_data_url_rejected: https url → ValueError(only_data_url_supported); never fetched.
  - test_bad_base64_rejected: "data:image/png;base64,!!!" → ValueError (not a crash).
  - test_unknown_part_type_rejected: {type:"audio_url"} → ValueError(unsupported_content_part).
  - test_frozen_gemini_suites_green: re-run tests/gemini_tool_use + tests/gemini_json_mode + tests/gemini_provider (string-content paths unchanged).
</test_plan>

Tests live in: `apps/gateway/tests/gemini_multimodal/test_gemini_multimodal.py` · MUST run red before Build. (no-DB → `uv run pytest tests/gemini_multimodal` + in make test-fast.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/gemini_multimodal/`
  (error_catalog: a new ERR_UNSUPPORTED_CONTENT_PART (400) if no existing 400 fits; reuse PAYLOAD_INPUT_TOO_LONG for the size cap. main.py: thread settings.gemini_inline_max_bytes into the GeminiCompletionUpstream constructor — the one-kwarg wiring, additive.)
Strategy (ordered batches): 1. add `_data_url_to_inline` + `_content_to_gemini_parts` helpers. 2. thread `max_inline_bytes` kwarg through `_openai_to_gemini_request` + wire the helper at the user + assistant-text branches (system unchanged); pass settings.gemini_inline_max_bytes from the GeminiCompletionUpstream caller. 3. config knob + error mapping (ValueError → clear 4xx, mirror the existing tool_call_id_required mapping). 4. no-DB tests.
Safety rule (feature-specific): BACK-COMPAT — the new path triggers ONLY when `content` is a list; a string stays byte-identical (re-run the frozen gemini suites). SSRF — only `data:` URLs; NEVER fetch an http(s) url. SIZE-CAP — sum decoded bytes across all parts; over-cap → 413 BEFORE the upstream call. No new network/dep.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; do NOT alter the frozen gemini string-content tests; allow-list packages only (stdlib base64/re only); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gemini_multimodal 22/22; the 3 FROZEN suites (gemini_provider/tool_use/json_mode) 38/38 UNCHANGED; make test-fast 228 (was 206 + 22 new, NO regression).
- [x] coverage did not decrease — 22 new behavioral tests on the translation + size guard.
- [x] no test or contract was altered during build — only new tests + additive code; the frozen Gemini suites were re-run, not edited.
- [x] the green was EARNED — I read `_content_to_gemini_parts` + `_data_url_to_inline` + `_map_translation_error` in full: str→[{text}] (byte-identical), list→per-part inlineData (decode strict, order preserved), unsupported/bad-url/over-cap → ValueError. The tests assert real shapes + raises, not vacuous. Back-compat proven by test_string_content_byte_identical AND the 38 untouched frozen tests.
- [x] concurrency / timing safe — pure synchronous translation runs ONCE outside the retry loop (complete/stream translate before any upstream contact); the running_total is a per-request local list, not shared state.
- [x] no exposed secrets / injection / unexpected deps — stdlib only (base64, re, binascii); grep of the diff shows NO httpx/requests/urllib/fetch in the new code → no SSRF; the error message is generic (no echo of the bad URL).
- [x] layering & dependencies follow CONVENTIONS.md — the change lives entirely in the Gemini adapter (infrastructure); the use-case + other adapters untouched; config knob additive; error code added to the catalog.
- [x] reviewed — full-auto self-review per Tin's "complete all milestones in auto mode": I read the helpers + the error-mapping (confirmed existing ValueErrors like tool_call_id_required RE-RAISE unchanged via bare `raise`) + verified scope (added main.py to §5). (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] a text+image list → [{text},{inlineData:{mimeType,data}}] in order — test_text_plus_image_inline + my read of _content_to_gemini_parts (append in loop order).
- [x] a string content → byte-identical to today — test_string_content_byte_identical + the 38 frozen tests green untouched.
- [x] over-cap inline → raises before forward — test_over_cap_raises; the running_total guard fires in translation, which runs before the retry loop / stream open.
- [x] a non-data (http/s) URL is rejected, never fetched — test_non_data_url_rejected; grep confirms no network call in the new code (SSRF-safe).
- [x] ValueErrors map to clear 4xx — inline_too_large→413, the 3 content-part errors→400; existing tool_call_id_required / response_format ValueErrors re-raise unchanged (confirmed in _map_translation_error).

### Deep checks
- [x] WIRING (code) — _data_url_to_inline ← _content_to_gemini_parts ← _openai_to_gemini_request (user + assistant-text branches) ← complete()/stream() (with _map_translation_error) ← GeminiCompletionUpstream(max_inline_bytes=settings.gemini_inline_max_bytes via main.py); exported in __all__; 22 tests exercise all branches.
- [x] DEAD-CODE (code) — no orphaned symbol; pyright 0 + ruff clean on gemini_upstream.py.
- [x] SEMANTIC — read the translation + error-mapping in full; back-compat + SSRF-avoidance + size-cap-before-forward confirmed.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
