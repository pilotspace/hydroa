# MILESTONE: OpenAI API-surface parity

goal: any application built on the OpenAI SDK can point its base URL at Hydroa and its Responses/Files/Moderations/Images-edit/Usage calls work — tenant-scoped, exactly billed, honoring every existing governance gate
rationale: sub-milestone — R4 of the Tin-approved roadmap refresh (2026-07-24, AskUserQuestion: "R4 parity first"), feeds release 0.12.0.
  Bucket `sub-milestone`: a slice of the standing "OpenAI-compatible gateway" theme (PROJECT.md goal), too big for one task (6 tasks).
  Relationships: **extends** the chat ChatTranslator seam (v9–v11) with the /v1/responses wire; **extends** v45's
  artifacts/ObjectStore seam into an OpenAI-wire /v1/files surface; **extends** ml-moderation-layer (its evaluator becomes a
  client-facing /v1/moderations endpoint); **extends** the images seam (edits/variations); **relates-to** queued v58 (real
  provider Batch integration — files-api's `input_file_id` wiring is the shared edge; v58 stays queued, untouched here).
  Code-verified absent 2026-07-24: no /v1/responses, /v1/files, /v1/moderations, images edits/variations, or tenant-facing
  usage API anywhere in apps/gateway/src.
stage: production · status: active · created: 2026-07-24T03:27:26+00:00
relations: extends: v45, ml-moderation-layer, proxy-correctness · relates-to: v58

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/PLAN.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  /v1/responses (stateless translation + streaming SSE events) · responses state (`store`,
     `previous_response_id`, GET/DELETE /v1/responses/{id}) · /v1/files + multipart upload
     (list/get/delete/content, purposes: batch·vision·user_data; batches accept `input_file_id`) ·
     /v1/moderations (existing ML-moderation evaluator, metered) · /v1/images/edits +
     /v1/images/variations · tenant-facing usage/costs read API (OpenAI organization-usage-style,
     over usage_records).
Out: fine-tuning API · vector stores / file_search managed RAG (→ R5 0.13.0) · Assistants API
     (deprecated upstream — never build) · Realtime sessions REST compat (relay already covers WS) ·
     code-interpreter/container tools · streaming image generation · per-alias routing / cost
     optimizer (→ R6) · prompt registry / evals (→ R7) · any change to v58's batch-provider scope ·
     console/dashboard pages for these surfaces (API-first; UI is a follow-up milestone).

## Ground   (shared real-code context — gathered ONCE; every task's specify projects from this)
Touches (shared files · symbols): apps/gateway/src/gateway/proxy/api/ (router.py chat seam ·
  images_router.py · messages_router.py precedent for a second native wire) · proxy/infrastructure/
  ml_moderation_evaluator.py · artifacts/ (api/router.py, infrastructure/repository.py, objectstore/) ·
  conversations/ (state-store precedent) · batches/ (input_file_id consumer) · usage/ (usage_records
  ledger + recorder) · core (governance choke points: chat + non-chat NonChatGovernance).
Anchors: ChatTranslator seam + extract_usage_from_sse (v9 frozen) · pricing_unit dispatcher
  (per_token·per_image·per_second·per_character, v7) · ObjectStore port (v45/object-store-port) ·
  ml-moderation evaluator with isolated CircuitBreaker + `unchecked` verdict (ml-moderation-layer) ·
  tenant-scoped repository idiom (tenant_id on every row, 404-never-leak) · append-only usage_records.
Honors (conventions): every proxied request → exactly one usage record, billed on the SERVED model ·
  no outbound IO without timeout+bounded-retry+breaker · byte-identical default path (a request not
  using a new surface engages zero new plumbing) · ZDR/retention/residency/guardrails compose on any
  NEW payload store · fail-closed on security, honest degradation everywhere · Envoy edge: new /v1
  routes must ride the existing ext_authz gate.
Issues/Risks (shared): responses statefulness is a NEW payload-at-rest store → must compose with ZDR
  (a ZDR tenant's `store:true` is REFUSED loud — 403 `ERR_ZDR_PAYLOAD_BLOCKED`, pre-dial; Tin's
  decision 2026-07-24 superseded the drafted "metadata-only" wording, which would have quietly
  stored a reduced payload for a tenant that contracted for none) + retention sweeper +
  payload-capture inventory, and state isolation is
  security-sensitive (cross-tenant `previous_response_id` probing must 404) · /v1/files content is a
  second user-payload store with the same obligations · moderations billing needs a real per-token
  count from the provider verdict path · images edits are multipart (new content-type handling on a
  JSON-only seam) · usage API must never expose another tenant's rows (keyset pagination over an
  indexed tenant_id scan).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Response object** (glossary NEW): the /v1/responses resource — id `resp_*`, `output` item list,
  optional stored state. Maps onto ONE chat-completions round-trip through the EXISTING router/
  billing path; it is a wire dialect, never a second inference path (mirrors /v1/messages ingress).
- **File object** (glossary NEW): OpenAI-wire file (`file-*`, purpose, bytes, filename) stored via the
  existing ObjectStore port; artifacts remain a distinct playground surface — no artifact/file merge.
- Billing rule: every new surface bills through the existing pricing_unit dispatcher — no new billing
  mechanism; a surface with no provider cost (files CRUD, usage API) writes NO usage record.
- Anti-enumeration: all new /v1 unauthenticated/cross-tenant failures use the existing uniform 401/404
  postures (sso-login-oracle-closure lesson: one contracted terminal code, never an alternation).

## Shared / risky contracts (freeze these first)
- Responses wire shape (request/response/SSE event names + chat-seam mapping) -> owning task responses-api-core
- File object schema + purpose vocabulary (batches consumes `input_file_id`) -> owning task files-uploads-api

## Tasks (breadth-first decomposition; detail lives in each PLAN.md)
- [x] responses-api-core       depends-on: none                — stateless /v1/responses (POST, stream + non-stream) translated onto the chat seam; input items ⇄ messages, output items, usage + billing identical to chat
- [x] responses-state-store    depends-on: responses-api-core  — `store:true` persistence, `previous_response_id` chaining, GET/DELETE /v1/responses/{id}; tenant-isolated, ZDR/retention-composing  [sensitivity: security]
- [x] files-uploads-api        depends-on: none                — /v1/files CRUD + content + multipart upload on the ObjectStore port; purposes; /v1/batches accepts `input_file_id`
- [x] moderations-endpoint     depends-on: none                — /v1/moderations exposing the ML-moderation evaluator (isolated breaker, `unchecked` honesty), metered per_token
- [x] image-edits-variations   depends-on: none                — /v1/images/edits + /v1/images/variations (multipart) on the images seam, billed per_image on returned entries
- [x] tenant-usage-costs-api   depends-on: none                — tenant-scoped OpenAI-style usage/costs read API over usage_records (time-bucketed, filterable, keyset-paginated)  [sensitivity: data]

## Exit criteria (observable; map each to the task that delivers it)
- [x] An OpenAI-SDK `client.responses.create(...)` (stream + non-stream) against Hydroa's base URL returns a valid Response with correct usage, and one usage_record billed on the served model        (← responses-api-core)  (verify: `cd apps/gateway && uv run pytest tests/responses_api_core/ -q` all green)
- [x] `responses.create(store=true)` then `previous_response_id` continues the conversation; another tenant GETting that id receives 404; a ZDR tenant's `store:true` is refused loud 403 pre-dial (Tin's decision 2026-07-24, supersedes the drafted metadata-only wording)        (← responses-state-store)  (verify: `cd apps/gateway && uv run pytest tests/responses_state_store/ -q` all green + dual adversarial security verify recorded in its GATE RECORD)
- [x] `client.files.create(purpose="batch")` → the file drives a /v1/batches job via `input_file_id`; upload/list/get/content/delete all work tenant-scoped        (← files-uploads-api)  (verify: `cd apps/gateway && uv run pytest tests/files_uploads_api/ -q` all green)
- [x] `client.moderations.create(...)` returns category verdicts; a moderation-provider outage yields an honest error per failure_mode, never a fabricated "safe"        (← moderations-endpoint)  (verify: `cd apps/gateway && uv run pytest tests/moderations_endpoint/ -q` all green)
- [x] `client.images.edit(...)` and `client.images.create_variation(...)` return images and bill per returned image        (← image-edits-variations)  (verify: `cd apps/gateway && uv run pytest tests/image_edits_variations/ -q` all green)
- [x] A tenant admin can pull their own daily token/cost series via the API with an API key, and can never see another tenant's rows        (← tenant-usage-costs-api)  (verify: `cd apps/gateway && uv run pytest tests/tenant_usage_costs_api/ -q` all green incl. the cross-tenant isolation tests)

## Strategy   (AI-drafted WITH the human — the optimized task plan; SOFT/advisory like a task's Build-strategy; drafted-blank for a micro/--fast milestone)
- Approach (sequencing): freeze-first + parallel waves — the two shared contracts (responses wire, file schema) freeze before anything builds; everything else is independent.
- Freeze-first: responses-api-core §3 (the wire + seam mapping) and files-uploads-api §3 (file schema + purpose vocabulary).
- Waves (parallel): wave-1 = responses-api-core ∥ files-uploads-api ∥ moderations-endpoint ∥ image-edits-variations ∥ tenant-usage-costs-api (independent seams); wave-2 = responses-state-store (behind responses-api-core's frozen wire; dual adversarial verify — security).
- Tradeoffs weighed: (a) one mega "responses" task vs core+state split — split chosen so the security-sensitive state store gets its own contract + dual verify without stalling the stateless wire; (b) merging files into artifacts vs a parallel OpenAI-wire surface — parallel chosen: artifacts is a playground UI contract, files is SDK-compat; both share the ObjectStore port; (c) building fine-tuning now (needs files) — deferred to R5 to keep this milestone one outcome.

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Cross-task review the AI fills — the evidence behind the EXISTING milestone-done gate, NOT a new approval.

### Ship by domain   (what changed, per bounded context)
- gateway proxy : NEW /v1/responses (responses_router + openai_responses_ingress on the ChatTranslator seam) · NEW responses_store/ bounded context (stored responses, chaining, cascade DELETE, ZDR-403) · NEW /v1/moderations (moderations_router + use-case, per-tenant breaker on the ml-moderation evaluator) · /v1/images/edits+variations on the images seam
- gateway files : NEW files/ bounded context (/v1/files multipart CRUD on ObjectStore) + additive batches input_file_id
- gateway usage : NEW usage/openai_usage_* read API (bucketed usage/costs, keyset paginated, tenant-scoped)
- migrations : 4 new (moderations seed · dall-e-2 seed · files table · stored_responses table), re-chained to one head c7e0a4b2d9f1; both table manifests updated
- tooling / skill / book : untouched (Tin's in-flight .add engine edits ride separately, not part of this branch's feature diff)

### Cross-task evidence   (one row per task)
- responses-api-core     : gate=PASS · tests=18 green (incl. split-usage-frame regression) · residue=none (healed: terminal usage from joined frames)
- responses-state-store  : gate=PASS · tests=22 green · residue=none · DUAL security verify CLEAR + store-failure coverage added on Tin HARD-STOP
- files-uploads-api      : gate=PASS · tests=23 green · residue=none (healed: /v1/files body-cap → contracted 413; bounded read)
- moderations-endpoint   : gate=PASS · tests=22 green · residue=none · Tin HARD-STOP → CR-1 per-tenant breaker, security-refuted CLEAR
- image-edits-variations : gate=PASS · tests=15 green (+14 generations regression) · residue=none
- tenant-usage-costs-api : gate=PASS · tests=23 green · residue=none (healed: fromtimestamp overflow → contracted 422)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (6/6 criteria ↔ 6 PASS rows; each criterion carries a green-suite verifier)
- goal: any OpenAI-SDK app points its base URL at Hydroa and Responses/Files/Moderations/Images-edit/Usage calls work, tenant-scoped + exactly billed — proven by 6/6 gated suites + pre-merge sweep (6 new suites 122/122, adjacent regressions 201/201, guardrails 36/36 serial).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> AI-written steps for THIS milestone (hints, not engine commands); MERGE is one small step; the human runs the cut.
- [ ] run the FULL gateway suite (chunked ≤ -n 6 per the 12-core lesson) + dashboard suite before PR
- [ ] open one PR from the ship-review; Tin reviews + merges (review lands as a comment — GitHub blocks self-approve)
- [ ] live SDK smoke: official `openai` Python SDK pointed at the edge — responses/files/moderations/images/usage happy paths
- [ ] `add.py release 0.12.0` after merge (human runs tag/publish/deploy)
