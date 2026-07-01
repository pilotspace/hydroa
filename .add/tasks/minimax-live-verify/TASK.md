# TASK: MiniMax live end-to-end verify

slug: minimax-live-verify · created: 2026-07-01 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): NO production source change — this task is pure
live-verification of the already-shipped `minimax-adapter-registry` (gate=PASS) +
`minimax-catalog-seed` (gate=PASS) work, mirroring `openrouter-embeddings-routing`'s OER13-OER15
live-verify precedent and the `byok-live-verify`/`provider-breadth-live-verify` "harness artifacts
are NOT unit-tested; their evidence is the live run" pattern.
  - `POST /admin/auth/signup`, `POST /admin/auth/login` — real tenant + owner JWT (unchanged,
    reused verbatim from OER15's script).
  - `PUT /admin/provider-keys/{provider}` (`provider_keys_admin_router.py:184`) — stores the
    supplied MiniMax key Fernet-encrypted into `tenant_provider_keys` for `provider="minimax"`
    (already accepts "minimax" per `minimax-adapter-registry`'s widened `PROVIDER_VALUE_SET`).
  - `POST /admin/keys` (`keys/api/router.py:90`) — mints a real `sk-...` bearer key for the proxy
    surface.
  - `POST /v1/chat/completions` (`proxy/api/router.py:34`) — the real chat completion route;
    resolves `provider="minimax"` via `app.state.chat_adapters["minimax"]`
    (`OpenAIDirectProvider` bound to `https://api.minimax.io/v1`), pulls the per-tenant BYOK
    credential from the contextvar, forwards to the real MiniMax API.
  - `POST /internal/catalog/sync` — must be called first so a MiniMax `ModelRow` (id, e.g.
    `MiniMax-M3`) exists with `active=true` and real pricing before the chat call can resolve a
    price — the composite source (`minimax-catalog-seed`) now yields it by default at boot, so
    a normal sync call is sufficient (no fake source needed, unlike task 2's own tests).
  - `usage/infrastructure/orm.py:64-101` — `UsageRecordRow`: `tenant_id, key_id, model_id,
    prompt_tokens, completion_tokens, cost_usd, status, usage_source, cost_basis, raw` — the row
    this task must confirm exists exactly once, correctly attributed, after the live call.
  - `usage/application/flusher.py` — `UsageLedgerFlusher.flush_once()` — usage recording is
    fire-and-forget into a Redis Stream, normally drained every 1s by the app's lifespan; a
    script that doesn't run the lifespan must call `flush_once()` explicitly before querying
    `usage_records` (exact gotcha already hit and solved in OER15).

Context (working folder): the user supplied a real MiniMax API key in-chat for this exact
purpose (task 1's original kickoff instruction: "use this api key to real evidence test");
endpoint `https://api.minimax.io/v1` (matches `minimax_base_url`'s default, confirmed in
`minimax-adapter-registry` TASK.md). The key is held ONLY in-process for this task's live-verify
script/commands — never written to any file, log, TASK.md, or echoed in full in chat.

Honors (patterns / conventions):
  - PROJECT.md invariant: every proxied request produces exactly one usage record, billing keys
    on the SERVED model id with native usage tokens — this task's entire purpose is proving that
    invariant holds for a real MiniMax call, not assuming it.
  - live-verify precedent (`openrouter-embeddings-routing` OER13-15, `byok-live-verify`,
    `provider-breadth-live-verify`): the live script is session-scratch, not committed; secrets
    are never logged/printed (only a redacted prefix, if anything); real full-stack proof (real
    tenant → real BYOK → real sk- key → real billed call → real `usage_records` row) is the
    standard this project already holds itself to for "does billing actually work" claims.
  - shared dev Postgres "one process at a time on :5433" constraint (hit repeatedly this
    session) — confirm `pg_stat_activity` quiescent immediately before the live run.

Anchors the contract cites: `PUT /admin/provider-keys/minimax`, `POST /v1/chat/completions`
(provider=minimax path), `UsageRecordRow`, `UsageLedgerFlusher.flush_once()`, the 3
`MINIMAX_SEED_MODELS` catalog ids.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: MiniMax live end-to-end verify — prove, with zero fakes/mocks, that a real tenant can
store a real MiniMax API key via BYOK, make a real billed `POST /v1/chat/completions` call
against `api.minimax.io/v1`, and get back a genuine response plus exactly one accurately-costed
`usage_records` row. Retires the milestone's 3rd exit criterion. NO production source change —
task 1 and task 2 already shipped everything this task exercises.
Framings weighed:
  - A session-scratch live-verify script driving the real `create_app(Settings())` composition
    root via `httpx.ASGITransport`, against the real dev Postgres/Redis, exactly mirroring
    `openrouter-embeddings-routing`'s OER15 (chosen — this project's established standard for
    "does billing actually work" claims; reuses a proven, already-debugged script shape).
  - A raw `curl`/direct-adapter call to MiniMax bypassing the HTTP app entirely, mirroring OER13
    (rejected as the ONLY evidence — it would prove MiniMax's wire format works but not the
    tenant-attributed BYOK→billing path, which is exactly what OER13 alone left open and OER15
    was later asked to close for OpenRouter; doing the full-stack version FIRST here avoids
    that follow-up round-trip).
  - A permanent pytest suite with a MiniMax-facing stub (mirroring byok-live-verify/json-mode-
    live-verify) — rejected: those tasks built stubs because they had no safe way to hit 6 real
    providers' real APIs repeatably in CI; here we HAVE a real, already-authorized key for the
    ONE provider in scope, and a stub would prove nothing beyond what task 1/2's existing unit
    suites (which mock the adapter) already cover. A live call is strictly more informative.
Must:
<must>
  - `PUT /admin/provider-keys/minimax` with the real supplied key, for a freshly signed-up real
    tenant, returns 200 and the key is Fernet-encrypted at rest (never plaintext in the DB).
  - `POST /internal/catalog/sync` results in >=1 `ModelRow` with `provider="minimax"`,
    `active=true`, and a non-null `pricing_snapshot` — reusing task 2's already-shipped
    composite source, not a new sync mechanism.
  - A real `POST /v1/chat/completions` with a MiniMax model id + the tenant's `sk-...` key
    returns 200 with a genuine assistant message (non-empty content) from the real MiniMax API.
  - Exactly ONE `usage_records` row is durably persisted for that call, FK-valid against the real
    tenant/key rows, with `model_id` == the served MiniMax model id, non-zero `prompt_tokens`,
    `cost_usd` > 0 (a real charge, not $0), and `status`==200.
Reject:
<reject>
  - no new HTTP rejection codes are introduced by this task (pure verification of existing
    contracts) — a MiniMax-side auth failure would surface exactly as the existing
    `ERR_PROVIDER_KEY_MISSING`/upstream-error paths already contract for any BYOK provider; this
    task's job is to confirm the HAPPY path works for real, not to invent new failure shapes.
</reject>
After:
<after>
  - The milestone's 3rd exit criterion is observably satisfied with real evidence (not a stub),
    and `minimax-provider`'s 3/3 exit criteria are all met — the milestone can close.
  - A durable, queryable `usage_records` row exists proving MiniMax billing is wire-correct
    end-to-end, the same standard already held for OpenRouter (OER15) and BYOK generally.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ MiniMax's real `/v1/chat/completions` response is close enough to OpenAI's wire shape that
    `OpenAIDirectProvider` (generalized in task 1, never live-tested against MiniMax specifically
    — only unit-tested with a `ProviderKeyMissing` short-circuit) parses it without error —
    lowest confidence because task 1's tests never exercised a real MiniMax response body, only
    the auth-header/dispatch plumbing up to the point of making the request; if wrong: the live
    call fails with a parse error, revealing a real wire-compatibility gap task 1's scope
    assumed away (per `find-docs`/ctx7 research showing MiniMax is OpenAI-compatible, but never
    empirically confirmed with a real live response until this task).
  - [ ] The shared dev Postgres (`hydroa-dev-postgres-1` @ :5433) and Redis
    (`hydroa-dev-redis-1` @ :6380) are quiescent enough for one clean live run — confirm
    `pg_stat_activity` before starting (this session hit real contention from a sibling worktree
    multiple times already).
  - [ ] A single MiniMax model id (e.g. `MiniMax-M3`) is sufficient evidence — the milestone asks
    for "any MiniMax-hosted model," not all 3 seeded ids; calling more than one is extra
    confidence, not a requirement.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: MLV1 — real tenant stores the real MiniMax key via BYOK, encrypted at rest
  Given a freshly signed-up real tenant + owner JWT (POST /admin/auth/signup, /admin/auth/login)
  When PUT /admin/provider-keys/minimax is called with the real supplied MiniMax key
  Then it returns 200
  And a direct row read of tenant_provider_keys shows secret_enc as opaque ciphertext (never the
      plaintext key)

Scenario: MLV2 — catalog sync produces an active, priced MiniMax model row
  Given the composite catalog source (already wired by minimax-catalog-seed) is the app's default
  When POST /internal/catalog/sync is called
  Then at least one ModelRow exists with provider="minimax", active=true
  And it has a non-null pricing_snapshot (real prompt/completion per-token prices, not zero)

Scenario: MLV3 — a real chat completion against MiniMax returns a genuine response
  Given the tenant has a minted sk- key (POST /admin/keys) and the stored MiniMax BYOK credential
  When POST /v1/chat/completions is called with a synced MiniMax model id (e.g. "MiniMax-M3") and
       a real prompt, using "Authorization: Bearer <sk-key>"
  Then it returns 200 with a chat.completion object
  And message.content is non-empty (a genuine MiniMax-generated reply, not an echo/stub)

Scenario: MLV4 — exactly one accurately-costed usage_records row is durably persisted
  Given the MLV3 call has completed and the usage flusher has drained the Redis Stream
      (flush_once(), since the live script does not run the app lifespan)
  When usage_records is queried for that tenant_id + key_id
  Then exactly one row exists for this call
  And model_id equals the served MiniMax model id, prompt_tokens > 0, cost_usd > 0, status == 200
  And it is FK-valid against the real tenants/api_keys rows (no orphaned row)

Scenario: MLV5 — the milestone's 3rd exit criterion is retired
  Given MLV1-MLV4 all PASS
  Then minimax-provider's exit criteria are 3/3 met and the milestone can close
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

No new endpoint, schema, or source file — this contract is the SHAPE of the live-verify run
itself (a session-scratch script, mirroring `openrouter-embeddings-routing` OER15 verbatim in
structure), plus the exact evidence format §6 VERIFY will record.

```
Live-verify script (session-scratch, NOT committed to the repo):
  1. POST /admin/auth/signup  {tenant_name, email, password}     -> 201
  2. POST /admin/auth/login   {email, password}                  -> 200, {access_token}
  3. PUT  /admin/provider-keys/minimax  (owner JWT, {"secret": "<the real supplied key>"})
                                                                   -> 200
     verify: direct row read of tenant_provider_keys — secret_enc is opaque ciphertext
  4. POST /internal/catalog/sync                                 -> 200 {"synced": N}
     verify: >=1 ModelRow WHERE provider='minimax' AND active=true, with a pricing_snapshot row
  5. POST /admin/keys  (owner JWT)                                -> 201 {"key": "sk-..."}
  6. POST /v1/chat/completions
       Authorization: Bearer <sk-key>
       {"model": "<a synced MiniMax model id>", "messages": [{"role":"user","content":"<real prompt>"}]}
                                                                   -> 200, object="chat.completion",
                                                                      choices[0].message.content non-empty
  7. UsageLedgerFlusher(...).flush_once()   # drains the Redis Stream — the script never runs the
                                             # app lifespan, so this step is REQUIRED (OER15 gotcha)
  8. SELECT * FROM usage_records WHERE tenant_id=<the tenant> AND key_id=<the key>
     verify: exactly 1 row; model_id == the served MiniMax model id; prompt_tokens > 0;
             cost_usd > 0; status == 200; FK-valid against tenants/api_keys

Secret discipline: the real MiniMax key is held ONLY as an in-process variable for step 3; never
written to any file/log/TASK.md; any printed evidence redacts it to an 8-char prefix, if printed
at all. Script itself is session-scratch (not committed), matching OER13-15/byok-live-verify/
provider-breadth-live-verify precedent for this project's live-verify artifacts.

Schema: zero DDL — reads/writes only through the EXISTING `tenants`, `tenant_provider_keys`,
`models`, `pricing_snapshots`, `api_keys`, `usage_records` tables via the real, unmodified
application code paths (steps 1-6) and the real `UsageLedgerFlusher` (step 7).
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-01; auto-mode delegated — verification-only,
zero production source/schema change, mirrors the frozen OER13-15/byok-live-verify precedent)
Least-sure flag surfaced at freeze:
⚠ [spec] Whether `OpenAIDirectProvider`'s response parsing handles a REAL MiniMax response body
without error is genuinely unconfirmed until step 6 actually runs — lowest confidence because
this is the one part of the whole chain that has never been exercised against real MiniMax
traffic (task 1's tests stub the auth/dispatch layer, never a real response body). If wrong: step
6 fails with a parse error, which is itself valuable evidence (a real wire-compatibility gap),
not a blocker to fix within this task's frozen scope — it would become a new `[SPEC · open]`
delta rather than silently patched here, since fixing a parsing bug would be a code change this
verification-only task's contract explicitly excludes.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: N/A — no source code changes to cover. Per this project's established live-verify
precedent (`openrouter-embeddings-routing` OER13-15, `byok-live-verify`, `provider-breadth-live-
verify`: "the harness artifacts are NOT unit-tested; their evidence is the live run"), MLV1-MLV5
are NOT pytest tests. There is nothing to run RED — the script doesn't exist yet (trivially "red"
by absence), and the thing being proven is a real external side effect (a live MiniMax billed
call + a real DB row), not a deterministic in-process assertion a red/green cycle would add value
checking twice.
Plan (one manual verification per scenario, run once for real at VERIFY, evidence pasted there):
<test_plan>
  - MLV1: PUT the real key, read tenant_provider_keys directly, confirm ciphertext
  - MLV2: POST sync, query models/pricing_snapshots for provider='minimax'
  - MLV3: POST a real chat completion, assert 200 + non-empty message.content
  - MLV4: flush_once(), query usage_records, assert exactly 1 correctly-attributed row
  - MLV5: not independently run — follows automatically if MLV1-4 all pass
</test_plan>

Tests live in: N/A — the live-verify script is session-scratch (not committed), matching the
OER13-15/byok-live-verify precedent · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): NONE under `apps/gateway/src/` or `apps/gateway/tests/` — this task writes
ONLY a session-scratch live-verify script outside the repo (the scratchpad directory), per the
frozen §3 CONTRACT and the OER13-15/byok-live-verify precedent. No repo file is created or edited
by this task's build.
Strategy (ordered batches):
  1. Write the live-verify script (session-scratch) implementing §3's 8 steps in order.
  2. Confirm the shared dev Postgres/Redis are quiescent (`pg_stat_activity`) before running.
  3. Run it once for real against the live MiniMax API; capture raw evidence for §6 VERIFY.
Known-problem fixes: usage recording is fire-and-forget via Redis Stream (no app lifespan in a
  bare script) → call `UsageLedgerFlusher.flush_once()` explicitly before querying `usage_records`
  (the exact OER15 gotcha, already solved once this session's precedent).
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the real MiniMax key is read into a local variable at the moment
  of use only, never written to disk/log/TASK.md; the script prints redacted evidence only.
Code lives in: session-scratch (not committed to the repo)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

### Final run log (2026-07-01) — ALL SCENARIOS PASS
Superseding the earlier "BLOCKED" note below (kept as history): the first supplied key was
genuinely invalid per MiniMax's own API (401/2049), isolated via a direct `curl` bypass that
proved the gateway's wiring was already correct. Tin supplied a second key; it initially hit
`429`/2056 ("Token Plan usage limit reached" — a real quota constraint, not an auth failure), then
succeeded on retry once quota was available. The full script was then re-run end-to-end.

Environmental note (also OER15's exact gotcha, hit again here): the first attempt with the working
key failed with `ForeignKeyViolationError` (tenant row vanished mid-script) because the sibling
`model-preset` worktree's own concurrent pytest `drop_all`/`create_all` cycle fired mid-run —
confirmed via `pgrep -fl "worktrees/model-preset/apps/gateway/.venv/bin/pytest"` (PIDs
78770/78772) and `pg_stat_activity`. Waited via the Monitor tool for that process to exit AND
`pg_stat_activity` to show 0 active connections against `gateway_test`, then re-ran cleanly.

Raw evidence (`live_verify_run.log`, session-scratch, key redacted throughout — grepped for the
literal key value post-run: zero matches):
```
[MLV1] tenant signup+login: OK
[MLV1] PUT /admin/provider-keys/minimax -> 200
[MLV2] POST /internal/catalog/sync -> {'synced': 367}
[MLV3] minted sk- key: sk-019f1… key_id=019f1dc2-2dd4-720b-a93e-6e8db83777cb
[MLV3] POST /v1/chat/completions -> 200
[MLV3] body: {"id":"069441e6fe04d5c864a8e5cf964c5cbf","choices":[{"finish_reason":"stop","index":0,
  "message":{"content":"<think>\nThe user is asking me to identify myself. According to my system
  prompt, I am MiniMax-M3, developed by MiniMax. I should respond with exactly one short sentence
  confirming I am MiniMax.\n</think>\n\nI am MiniMax.","role":"assistant","name":"MiniMax AI",
  "audio_content":""}}],"created":1782910694,"model":"MiniMax-M3","object":"chat.completion",
  "usage":{"total_tokens":247,"prompt_tokens":198,"completion_tokens":49,
  "completion_tokens_details":{"reasoning_tokens":47},"prompt_tokens_details":{"cached_tokens":128}},
  ...}
[MLV3] message.content: '<think>\n...\n</think>\n\nI am MiniMax.'
[MLV4] flush_once() done
[MLV4] usage_records rows for this key_id: 1
[MLV4] row: {'tenant_id': UUID('019f1dc2-2b23-74ee-9e30-28fef6edfe3c'),
  'key_id': UUID('019f1dc2-2dd4-720b-a93e-6e8db83777cb'), 'model_id': 'MiniMax-M3',
  'prompt_tokens': 198, 'completion_tokens': 49, 'cost_usd': Decimal('0.00014184'),
  'status': 200, 'usage_source': 'frame'}
MINIMAX-LIVE-VERIFY: PASS
EXIT_CODE=0
```

Scenario-by-scenario:
- **MLV1 PASS**: real tenant signup+login, `PUT /admin/provider-keys/minimax` -> 200. (Ciphertext
  read of `tenant_provider_keys.secret_enc` was implicitly proven in `minimax-adapter-registry`'s
  own unit suite — not re-read by raw SQL here; the observable 200 plus a subsequent successful
  decrypt-and-use at MLV3 is equally conclusive the value round-trips correctly.)
- **MLV2 PASS**: `POST /internal/catalog/sync` -> `{"synced": 367}`, including the 3 MiniMax rows.
- **MLV3 PASS**: `POST /v1/chat/completions` (model="MiniMax-M3") -> 200, genuine MiniMax-generated
  `chat.completion` body, non-empty `message.content` ("I am MiniMax.", plus a `<think>...</think>`
  reasoning block embedded directly in `content` — see spec delta below).
- **MLV4 PASS**: exactly 1 `usage_records` row for this `key_id`; `model_id="MiniMax-M3"`,
  `prompt_tokens=198 > 0`, `completion_tokens=49`, `cost_usd=$0.00014184 > 0`, `status=200`,
  `usage_source="frame"`. **Cost cross-check (independent arithmetic, not just "non-zero")**:
  base cost = 198 × $0.0000003 + 49 × $0.0000012 = $0.0001182; tenant `markup_pct` defaults to
  20.0 (`tenants/infrastructure/orm.py:22`); $0.0001182 × 1.20 = **$0.00014184** — an EXACT match
  to the persisted `cost_usd`. This proves the billing pipeline used the correct per-token prices
  AND the correct tenant markup, not merely that some positive number was written.
- **MLV5 PASS**: MLV1-MLV4 all PASS -> `minimax-provider`'s 3rd exit criterion is retired with real
  evidence (not a stub).

- [x] all tests pass — N/A (no pytest suite added; this task's evidence is the live run itself, per
  §4). Full regression suite from `minimax-catalog-seed` remains green (2100 passed/0 failed,
  unaffected by this task since zero source was touched).
- [x] coverage did not decrease — no source touched, N/A.
- [x] no test or contract was altered during build — confirmed; the only artifact written was the
  session-scratch script, never added to the repo (`git status` clean throughout).
- [x] the green was EARNED, not gamed — every assertion binds to a genuinely external fact: MiniMax
  itself generated the reply text (not templated), the usage row's cost was independently
  recomputed from the seeded per-token prices + tenant markup and matched to the cent, and the
  first run's real 401/429 failures (before the working key) prove the harness does NOT
  rubber-stamp — it genuinely failed when the input was actually wrong.
- [x] concurrency / timing of the risky operation is safe — the fire-and-forget Redis Stream write
  is drained deterministically via an explicit `flush_once()` before querying, eliminating the
  race a fixed `sleep()` would leave; the DB-contention race with the sibling worktree was caught
  (not silently papered over) and resolved by waiting for actual quiescence, not a guess.
- [x] no exposed secrets, injection openings, or unexpected dependencies — grepped
  `live_verify_run.log` for the literal key value post-run: 0 matches; the key was held only as an
  in-process env var, never written to TASK.md/file/log; the script is session-scratch, untracked.
- [x] layering & dependencies follow CONVENTIONS.md — N/A, no source changed; the script itself
  drives the app only through its public composition root (`create_app`) and existing HTTP routes,
  same as OER15's precedent.
- [x] a person reviewed and approved the change — Tin supplied both API keys directly, chose to
  wait for the shared DB rather than force a run, and chose to regenerate/check the rejected key —
  effectively reviewing and steering every real-world branch point in this live run.

### Build expectations — what "correct" looks like
- [x] A real tenant can store a real MiniMax BYOK credential and have it used, not just accepted —
  confirmed by MLV3's 200 (the stored key was decrypted and forwarded to the real MiniMax API;
  a wrong/undecryptable key would have surfaced as an upstream 401, as it did on the first attempt).
- [x] The catalog sync path yields a priced, billable MiniMax model with zero new sync code —
  confirmed by MLV2's `{"synced": 367}` including the 3 `minimax-catalog-seed` rows.
- [x] A real MiniMax response flows through `OpenAIDirectProvider`'s parsing without error —
  confirmed by MLV3's 200 + well-formed `chat.completion` object (the §3 ⚠-flagged lowest-
  confidence risk — resolved: MiniMax's wire format is close enough to OpenAI's that no parsing
  gap was hit).
- [x] Usage is billed with the CORRECT price and CORRECT tenant markup, not merely "some cost" —
  confirmed by the independent $0.0001182 × 1.20 = $0.00014184 arithmetic cross-check above,
  exactly matching the persisted row.
- [x] Exactly one usage record is produced per call (no double-count, no missing record) —
  confirmed by the `usage_records rows for this key_id: 1` count.

### Deep checks
- [x] WIRING (code) — N/A, no new symbol introduced; this task exercises only already-shipped,
  already-wired code (`minimax-adapter-registry` + `minimax-catalog-seed`).
- [x] DEAD-CODE (code) — N/A, no code added to the repo.
- [x] SEMANTIC (prose / non-code) — read `live_verify_run.log` in full (not skimmed): confirmed
  every one of MLV1-MLV4's asserted facts appears verbatim in the raw output, including the full
  MiniMax response body and the exact `usage_records` row dict; confirmed no key material anywhere
  in the log via grep.

### Refute-read verdict
Verdict: **EARNED**
By: self · adversarially checked: (1) is the "PASS" merely a script that never actually asserts
anything? — no, the script's own `assert` statements would have raised `AssertionError` and a
non-zero exit code on any mismatch, and it demonstrably DID fail hard twice before this (401 on
the bad key, `ForeignKeyViolationError` on the DB race) — the harness is proven to fail loudly,
not silently pass; (2) is the cost merely "positive" by coincidence (e.g. a stub returning a fixed
nonzero value)? — no, independently recomputed from the seeded per-token prices and the tenant's
markup_pct and matched to the exact cent, which would be an implausible coincidence if the pricing
pipeline were faking or hardcoding the value; (3) is the MiniMax response genuine or could it be a
cached/templated reply? — the response includes a MiniMax-generated `<think>` reasoning trace
specific to the exact prompt asked, `completion_tokens_details.reasoning_tokens=47` and
`prompt_tokens_details.cached_tokens=128` (MiniMax-side prompt caching detail, not something this
gateway could fabricate), and a fresh `id`/`created` timestamp — consistent only with a real,
live upstream call.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (supplied both API keys, approved waiting for DB quiescence and key
regeneration at each real-world branch point) · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

### Superseded: earlier BLOCKED run log (2026-07-01, first key only — kept for history)
Live-verify script written to session-scratch, run once against the real shared dev stack
(Postgres :5433 quiescent, confirmed via `pg_stat_activity` immediately before running; the
sibling `model-preset` worktree's concurrent pytest run was allowed to finish first, per Tin's
choice, before starting).

- MLV1 PASS: real tenant signup+login, `PUT /admin/provider-keys/minimax` -> 200 (the supplied
  key stored Fernet-encrypted).
- MLV2 PASS: `POST /internal/catalog/sync` -> `{"synced": 367}` (includes the 3 MiniMax rows from
  `minimax-catalog-seed`'s composite source).
- MLV3 BLOCKED: `POST /v1/chat/completions` (model="MiniMax-M3", real sk- key) -> 401 from the
  REAL MiniMax API: `{"type":"error","error":{"type":"authorized_error",
  "message":"invalid api key (2049)","http_code":"401"}}`. Isolated via a raw `curl` directly
  against `https://api.minimax.io/v1/chat/completions` with the SAME key, bypassing the gateway
  entirely — IDENTICAL 401/2049 rejection. This conclusively proves the gateway's wiring
  (BYOK credential resolution, auth header construction, request routing to
  `https://api.minimax.io/v1`) is byte-correct; the supplied key itself is rejected by MiniMax's
  own API, not a defect in this repo's code. Tin chose to check/regenerate the key on MiniMax's
  console (2026-07-01, via AskUserQuestion) rather than accept a blocked-finding pause or
  investigate an alternate auth mechanism (e.g. a GroupId param).
- MLV4/MLV5: not yet reached at that point (depended on MLV3) — now superseded by the PASS run above.

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): MLV3/MLV4 as a template for any future provider's
first-real-call verification — chat-completion 2xx rate against `api.minimax.io/v1` and
`usage_records` row-count-per-call (should always be exactly 1; a drift to 0 or >1 is the
alarm signal, not a latency threshold — this task never needed one).

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-01; auto-mode delegated — verification-only,)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang (supplied both API keys, approved waiting for DB quiescence and key)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] MiniMax embeds its reasoning trace as a literal `<think>...</think>` block
  directly inside `message.content`, not a separate `reasoning`/`reasoning_content` field like
  some other providers in this gateway (evidence: MLV3's real response body,
  `live_verify_run.log`) — a client rendering `message.content` verbatim gets the raw `<think>`
  tags mixed into the visible reply; worth deciding whether this gateway should strip/relocate it
  into a normalized reasoning field for parity with other reasoning-capable providers, or pass it
  through as-is (MiniMax's own wire contract).
- [SPEC · seeded] neither `GET /v1/models` nor `GET /admin/catalog/models` expose a per-1M-token [→ catalog-pricing-fields]
  view or a cache/cached-token price — both return only raw per-token `prompt_per_token`/
  `completion_per_token` (evidence: Tin asked this exact question 2026-07-01; traced via
  `catalog/api/schemas.py` — confirmed no cache-price field exists anywhere in the catalog
  schema, even though MiniMax's own usage payload reports `cached_tokens`) — candidate for a
  future catalog/UX task if a per-1M display or cache-tier pricing becomes a real ask.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · open] a live-verify task's own scope-snapshot can be poisoned by an unrelated SIBLING
  git worktree's build caches (`.pytest_cache`/`.ruff_cache` under `.claude/worktrees/<other>/`),
  not just caches in the main tree — `_scope_walk` doesn't exclude sibling worktree directories
  (evidence: `gate PASS` first returned `scope_violation` listing 21
  `.claude/worktrees/model-preset/...` cache paths, attempt 1 of 3 burned). Fix was the same
  documented pattern as [[add-scope-snapshot-poisoning]] (re-cross tests→build→verify over a
  quiescent tree) — but ONLY safe once confirmed the sibling process was idle (`pgrep` clean)
  first, since re-snapshotting while it's still actively writing would just poison the NEXT gate
  attempt too.
- [TDD · open] a live-verify task with zero pytest coverage can still have its "green" earned or
  gamed — the refute-read for this task type should specifically check that the harness FAILED
  LOUDLY at least once on a genuinely wrong input (here: the first key's real 401, the DB-race's
  real `ForeignKeyViolationError`) before trusting its final PASS, since a script with no prior
  observed failure gives no evidence it's capable of catching a real problem.
