# TASK: gateway conversations domain (store + REST + tenant isolation)

slug: conversations-backend · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/conversations/` (NEW domain, mirrors gateway/audit/ layout) — `infrastructure/conversations_orm.py` (ConversationRow + ConversationMessageRow on `gateway.core.db.Base`), `application/repository.py` (ConversationRepository, tenant-scoped CRUD), `api/router.py` (`conversations_router`), `api/schemas.py` (pydantic in/out), `api/deps.py` (get_conversation_repository + the authenticate dep).
  - `apps/gateway/migrations/versions/<rev>_conversations.py` (NEW) — create `conversations` + `conversation_messages`, chained `down_revision="f2a4c6e8b0d3"` (current head). Indexes per §3.
  - `apps/gateway/src/gateway/main.py` (MODIFY) — `app.include_router(conversations_router)` (mirror the other include_router calls ~line 819-824).
  - `apps/gateway/tests/conversations/` (NEW) — DB-backed tests (mirror tests/audio_endpoints/conftest.py for the app+client+seeded-key fixtures); the dev Postgres :5433 is UP.
Context (working folder):
  - Auth seam: `KeyAuthenticator.authenticate(raw_key: str) -> AuthzResult` (key_authenticator.py) — the SAME authenticate the proxy/governance uses (Bearer `sk-` key). `AuthzResult` (keys/domain/entities.py:67, frozen dataclass) has `tenant_id: UUID` + `key_id: UUID` (+ optional fields). Conversations needs ONLY authenticate (no model/budget governance). The authenticator is on `app.state` (governance builds from it); expose a deps factory that pulls it (or builds a KeyAuthenticator from app.state like governance does).
  - DB: `get_session(request) -> AsyncSession` (core/db.py:73, `request.app.state.sessionmaker()`). ORM rows subclass `gateway.core.db.Base`; an additive table needs the model in `__table_args__` AND the migration (v30 lesson). Tables auto-create in tests via `Base.metadata.create_all` (conftest).
  - The chat message shape is `{role, content}` (role ∈ user|assistant|system) — match it so a stored thread can replay into `/v1/chat/completions` messages.
  - Raw key dependency: `get_raw_api_key` (proxy/api/deps.py) yields the Bearer token (used by audio_router). 401 when absent.
Honors (patterns / conventions):
  - TENANT-ISOLATION (security HARD invariant): EVERY query filters `tenant_id == authz.tenant_id`; a cross-tenant or unknown id → 404 (never 403/200 — no existence leak). This is the freeze-first security surface.
  - DESIGN-FOR-FAILURE: FK cascade (messages die with their conversation), indexes for list + message-order, pagination bounds (cap limit), content length cap, soft-delete (deleted_at) so a delete is reversible/auditable; DB errors surface as 5xx (no partial write — single transaction per request).
  - Additive: `/v1/chat/completions` + all existing routers untouched; new domain only. AuthzResult contract unchanged (read-only use of tenant_id/key_id).
Anchors the contract cites:
  - `ConversationRow` / `ConversationMessageRow` · `ConversationRepository` (tenant-scoped methods) · `conversations_router` (the 5 endpoints) · `KeyAuthenticator.authenticate` → `AuthzResult.tenant_id` · the 404-on-cross-tenant rule.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a tenant-scoped, API-key-authenticated `/v1/conversations` store — create/list/get/append/delete persistent conversation threads in a new `gateway/conversations/` domain, with STRICT tenant isolation. The platform's first "remote session" primitive.
Framings weighed: a new dedicated domain with its own ORM + repository + router authenticated by the proxy's KeyAuthenticator (chosen — mirrors gateway/audit, API-accessible to agents, reuses the sk- auth) · fold it into the admin/JWT surface (rejected — admin JWT excludes API-key agents; wrong audience) · dashboard-BFF-only persistence (rejected — not API-accessible; the platform vision wants remote sessions for any key holder).
Must:
<must>
  - M1 — `POST /v1/conversations` (auth: Bearer sk- key) creates a conversation owned by the authenticated `tenant_id`/`key_id`; optional `title`; returns `{id, title, created_at, updated_at}`.
  - M2 — `GET /v1/conversations` lists ONLY the authenticated tenant's non-deleted conversations, newest-updated first, paginated (`limit` default 50 cap 200, `offset`); returns items + (optional) message_count.
  - M3 — `GET /v1/conversations/{id}` returns the conversation + its messages in created order — ONLY if it belongs to the tenant; else 404.
  - M4 — `POST /v1/conversations/{id}/messages` appends `{role, content}` (role ∈ user|assistant|system, content non-empty) to the tenant's conversation, bumps its updated_at; returns the stored message; cross-tenant/unknown id → 404.
  - M5 — `DELETE /v1/conversations/{id}` soft-deletes (sets deleted_at) the tenant's conversation (idempotent-ish: a deleted/unknown id → 404); a deleted conversation no longer lists or GETs.
  - M6 — TENANT ISOLATION: every endpoint filters by `authz.tenant_id`; another tenant's id is indistinguishable from a missing id (404). Auth absent/invalid → 401.
</must>
Reject:
<reject>
  - no/invalid Bearer key -> 401 (KeyAuthenticator raises; mirror the proxy's auth error).
  - cross-tenant or unknown conversation id -> 404 (NOT 403 — no existence leak).
  - invalid role (not user|assistant|system) or empty content -> 422.
  - limit/offset out of bounds -> clamp (limit≤200, offset≥0); never an unbounded scan.
</reject>
After:
<after>
  - An API key holder can create a conversation, append messages, read it back with messages in order, list their conversations, and delete it; another tenant cannot see or touch it (404); `/v1/chat/completions` + other routers are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ exposing the authenticator to a non-proxy router cleanly — lowest confidence because the auth seam is wired for the proxy path; if the KeyAuthenticator isn't trivially reachable on app.state, the deps factory must build it like governance does; if wrong: a little extra wiring (no behavior risk). Mitigation: mirror exactly how governance/audio obtains the authenticator.
  - [x] AuthzResult exposes tenant_id + key_id — CONFIRMED (entities.py:67).
  - [x] dev Postgres is up for DB-backed tests — CONFIRMED (:5433).
  - [ ] soft-delete vs hard-delete — chose SOFT (deleted_at) for auditability/reversibility; a hard purge is a deferred delta.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Create and read back a conversation
  Given an authenticated tenant key
  When POST /v1/conversations {title:"T"} then GET /v1/conversations/{id}
  Then the conversation is returned with title "T" and an empty message list

Scenario: Append messages and read them in order
  Given a conversation owned by the tenant
  When two messages are appended (user then assistant)
  Then GET /v1/conversations/{id} returns both messages in created order
  And the conversation updated_at advanced

Scenario: List is tenant-scoped, newest first
  Given the tenant has two conversations
  When GET /v1/conversations
  Then only this tenant's conversations are returned, most-recently-updated first

Scenario: Tenant isolation (the security invariant)
  Given conversation C owned by tenant A
  When tenant B (a different key) GETs / DELETEs / appends to C's id
  Then every call returns 404 — indistinguishable from a missing id
  And C is unchanged

Scenario: Soft delete hides the conversation
  Given a conversation owned by the tenant
  When DELETE /v1/conversations/{id}
  Then it no longer appears in GET list and GET /{id} returns 404

Scenario: Auth + validation rejections
  Given the conversations API
  When a request has no Bearer key -> 401
  And an append with role "bot" or empty content -> 422
  And these never create/modify a row
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ALL routes auth: Authorization: Bearer sk-...  → KeyAuthenticator.authenticate(raw_key) → AuthzResult{tenant_id,key_id}
                 (absent/invalid → 401). EVERY query filters tenant_id == authz.tenant_id.

POST   /v1/conversations              {title?: str}                  -> 201 {id, title, created_at, updated_at}
GET    /v1/conversations?limit&offset                                -> 200 {data:[{id,title,created_at,updated_at,message_count}], ...}  (limit≤200 default 50, newest updated_at first, deleted_at IS NULL)
GET    /v1/conversations/{id}                                        -> 200 {id,title,created_at,updated_at,messages:[{id,role,content,created_at}]} | 404
POST   /v1/conversations/{id}/messages {role: user|assistant|system, content: str(non-empty)} -> 201 {id,role,content,created_at} | 404 | 422
DELETE /v1/conversations/{id}                                        -> 204 (soft: deleted_at=now) | 404

Schema (NEW, migration down_revision="f2a4c6e8b0d3"):
  conversations(
    id UUID PK default gen, tenant_id UUID NOT NULL, key_id UUID NOT NULL,
    title TEXT NULL, created_at timestamptz default now, updated_at timestamptz default now,
    deleted_at timestamptz NULL)
    INDEX ix_conversations_tenant_updated (tenant_id, updated_at DESC) WHERE deleted_at IS NULL
  conversation_messages(
    id UUID PK default gen, conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, created_at timestamptz default now)
    INDEX ix_conversation_messages_conv_created (conversation_id, created_at)
  (index in BOTH the ORM __table_args__ AND the migration — v30 lesson.)
Access: one AsyncSession transaction per request (get_session); append bumps conversations.updated_at in the same txn.
Validation: role ∈ {user,assistant,system}; content stripped non-empty; content length cap (e.g. 1_000_000 chars) → 422.
```

Status: FROZEN @ v1 — auto-approved EXCEPT the tenant-isolation security invariant, which is built to the frozen 404-rule and INDEPENDENTLY refute-verified at the gate (full-auto; new additive domain; no existing path touched; reuses the proven KeyAuthenticator) 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] TENANT ISOLATION is the make-or-break: a missing tenant_id filter on ANY of the 5 endpoints = a cross-tenant data leak. Mitigation: the repository takes tenant_id on EVERY method (no un-scoped query exists) + an explicit cross-tenant 404 test + a gate refute-read. Cost if wrong: HIGH (data leak) — hence the independent verify.
  - [spec] authenticator reachability on app.state — if not directly exposed, build it in the deps factory like governance does (wiring only).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — DB-backed (httpx ASGITransport + real Postgres :5433); mirror tests/audio_endpoints/conftest.py for app+client+seeded-key fixtures (seed ≥2 tenant keys for the isolation test).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_create_and_get: POST then GET /{id} → title + empty messages; created_at present.
  - test_append_and_order: append user+assistant → GET returns both in order; updated_at advanced past created_at.
  - test_list_tenant_scoped_newest_first: tenant A has 2 convos + tenant B has 1 → A's GET list returns exactly A's 2, newest updated first, NOT B's.
  - test_tenant_isolation_404: tenant B GET/DELETE/append on A's id → 404 each; A's convo still intact (verified via A's GET).
  - test_soft_delete_hides: DELETE → 204; subsequent GET /{id} → 404 and it's absent from the list.
  - test_auth_and_validation: no Bearer → 401; append role="bot" → 422; append content="" → 422; assert no row created (list count unchanged).
</test_plan>

Tests live in: `apps/gateway/tests/conversations/test_conversations.py` · MUST run red (missing implementation) before Build. (DB-backed → NOT in make test-fast; run via `uv run pytest tests/conversations`.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/conversations/` · `apps/gateway/migrations/versions/` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/conversations/`
Strategy (ordered batches): 1. ORM (conversations + conversation_messages) + migration (head f2a4c6e8b0d3). 2. repository (tenant-scoped CRUD) + schemas. 3. router (5 endpoints, authenticate dep, 404-on-cross-tenant) + main.py include. 4. DB-backed tests (incl. the tenant-isolation 404 test).
Safety rule (feature-specific): TENANT ISOLATION — the repository accepts `tenant_id` on EVERY method and EVERY query filters on it; there is NO method that reads/writes a conversation without the tenant_id; a cross-tenant/missing id returns None → the router maps to 404 (never 403). One transaction per request (append + updated_at bump atomic). No raw SQL string interpolation (bound params only).
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — conversations suite 35/35 (DB-backed, Postgres :5433); no-DB `make test-fast` 206/206 (no regression).
- [x] coverage did not decrease — 35 behavioral tests added (new domain had 0 before); no-DB suite unchanged at 206.
- [x] no test or contract was altered during build — only NEW tests added; the §3 contract is unchanged. The expired-key test + the 4 refute fixes STRENGTHEN, never weaken.
- [x] the green was EARNED, not gamed — independent refute-read (sonnet) on the tenant-isolation surface: tenant isolation UPHELD (no cross-tenant leak reachable). It ALSO surfaced 4 real issues missed by earned-green → ALL FIXED: (1) BLOCKER duplicate alembic revision id a1b2c3d4e5f6 (collided with health_alerting) → fresh c4d6e8f0a2b4, single linear head, migration renders valid DDL offline; (2) MEDIUM expired-key bypass (authenticate checks revoke not expiry) → expiry gate added in _authenticate + test_expired_key_is_rejected_401; (3) LOW append updated_at bump TOCTOU → added tenant_id + deleted_at guard to the UPDATE; (4) LOW ORM index direction → DESC aligned to migration.
- [x] concurrency / timing of the risky operation is safe — one AsyncSession per request (append message + updated_at bump in the same transaction/flush); the bump UPDATE is now tenant+active-scoped (closes the concurrent-soft-delete race the refute flagged).
- [x] no exposed secrets, injection openings, or unexpected dependencies — bound params only (no SQL string interpolation); no new packages; tenant_id sourced ONLY from the authenticated AuthzResult (never request body/query).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors gateway/audit domain layout (api/router + infrastructure/orm + infrastructure/repository); additive only.
- [x] reviewed — full-auto self-review + independent refute-read (sonnet) of the security surface; tenant isolation UPHELD; all surfaced findings fixed and re-verified. (Outward PR/push deferred to Tin.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] cross-tenant id → 404 (never 403/200), no existence leak — confirmed by test_cross_tenant_get/append/delete_returns_404 + the refute (CLAIM 2 UPHELD: all "not mine" paths return the same 404 ProblemError body).
- [x] every repository method is tenant-scoped — confirmed by reading repository.py: create/list_active/get_by_id/soft_delete/append_message ALL take keyword-only tenant_id; every SELECT/UPDATE filters `tenant_id == <authed>` (the bump UPDATE now too).
- [x] migration applies + chains to the real head — confirmed by `alembic heads` (single head c4d6e8f0a2b4) + offline SQL render (CREATE TABLE conversations/conversation_messages, DESC index, ON DELETE CASCADE, version_num f2a4c6e8b0d3→c4d6e8f0a2b4).
- [x] expired key rejected — confirmed by test_expired_key_is_rejected_401 (force-expire in DB → 401).
- [x] role/content validation → 422; soft-delete hides from GET+list — confirmed by test_reject_invalid_role / test_reject_empty_content / test_soft_delete_* .

### Deep checks
- [x] WIRING (code) — conversations_router registered in main.py (include_router + ORM side-effect import); 35 tests exercise all 5 endpoints end-to-end (real auth → repo → DB).
- [x] DEAD-CODE (code) — no orphaned symbol; pyright 0 errors + ruff clean on the new domain + tests + migration.
- [x] SEMANTIC — refute-read of the tenant-isolation invariant read repository/router/migration in full; verdicts cited above.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") + independent refute-read (sonnet, tenant-isolation UPHELD; 1 blocker + 3 findings surfaced & fixed) · date: 2026-06-26

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
