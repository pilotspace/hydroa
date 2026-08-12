# TASK: Persist provider generation id on the disconnect ledger row

slug: provider-generation-id-capture · created: 2026-06-22 · stage: production
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
  - `apps/gateway/src/gateway/usage/domain/extractor.py` — add `extract_generation_id_from_sse(chunks)`
    mirroring `extract_usage_from_sse` (pure, joins chunks, parses `data: {...}`, returns the SSE `id`).
  - `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` — add `provider_generation_id: str`
    (typed extras seam; `usage_source` is the exact template added the same way in v27).
  - `apps/gateway/src/gateway/proxy/application/use_cases.py` — `_fire_record_with_raw` (add param →
    extras); the t5 disconnect handler extracts the gen-id from `collected` and passes it on the
    client_disconnect record.
  - `apps/gateway/src/gateway/usage/application/recorder.py` — `supported_extras` (+field), `record`/
    `_record_internal` (param), `event_fields` (+`"provider_generation_id"`).
  - `apps/gateway/src/gateway/usage/application/flusher.py` — read the field + add to the INSERT.
  - `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — add nullable column.
  - `apps/gateway/migrations/versions/` — Alembic migration adding `provider_generation_id TEXT NULL`.
Context (working folder): the v27 `usage_source` addition is the byte-for-byte template for this additive
  extras→event→column threading (provider-cost-reconciliation / stream-usage-completeness migrations).
Honors (patterns / conventions): append-only ledger (this ONLY adds a nullable column + populates it at
  insert — NO update/delete) · typed extras capability seam (filtered by supported_extras) · empty string
  encodes NULL in the Redis event (team_id/provider_cost precedent) · pure extractor (no IO).
Anchors the contract cites: `extract_generation_id_from_sse` · `UsageRecordExtras.provider_generation_id`
  · `usage_records.provider_generation_id` column · the t5 disconnect record call.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Capture the provider's generation id onto the client-disconnect ledger row — the rail that
  inline recovery (t6.2b) + the sweep (t6.3) ride to look up the authoritative cost later.
Framings weighed:
  - **additive nullable column, threaded via the typed extras seam** (chosen) — mirror v27 `usage_source`
    end-to-end: extractor → UsageRecordExtras → _fire_record_with_raw → recorder event → flusher INSERT
    → column. Append-only preserved (write-at-insert only). Disconnect rows store the SSE `id`; all other
    rows leave it NULL.
  - store it in `raw` JSONB instead of a column — rejected: the sweep must query it efficiently
    (WHERE provider_generation_id IS NOT NULL); a JSONB probe is the wrong access pattern.
Must:
<must>
  - `extract_generation_id_from_sse(collected)` returns the SSE `id` string from the disconnect chunks
    (pure, joins chunks, tolerant of partial/garbage frames), or None when absent.
  - On a client disconnect, the fired client_disconnect record carries `provider_generation_id` = that
    id (when present); the column lands on the row through record → stream → flusher INSERT.
  - Adding the column is purely additive + nullable (NULL on every existing/normal row); no UPDATE/DELETE
    is introduced (append-only ledger preserved); complete/normal-end paths set it NULL.
  - The extras seam stays backward-compatible: recorders that don't declare provider_generation_id in
    supported_extras silently ignore it (v1-Protocol fakes unaffected).
</must>
Reject:
<reject>
  - no SSE `id` present in the collected disconnect chunks -> provider_generation_id is None/NULL (no row
    change; recovery simply has nothing to look up — not an error).
</reject>
After:
<after>
  - A client-disconnect row written for a stream that carried an `id` has that id in
    usage_records.provider_generation_id; every other row has NULL.
  - No billing amount changes (cost_usd/provider_cost untouched); no ledger row is ever mutated.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the OpenRouter SSE chunks carry the generation id in a top-level `id` field on the data frames
    (OpenAI-compatible shape) — lowest confidence because verified from the wire format/docs, not a live
    capture on this account; if wrong: provider_generation_id stays NULL and recovery no-ops (safe — t6.2b
    falls back to today's flagged $0). Mitigation: extractor returns None gracefully; fold into t6 live-verify.
  - [x] the typed extras seam filters unknown fields by supported_extras — confirmed in _dispatch_record.
  - [x] empty-string-encodes-NULL is the established event convention — confirmed (team_id/provider_cost).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Extractor pulls the SSE id from collected chunks
  Given collected SSE chunks whose data frames carry "id":"gen-abc"
  When extract_generation_id_from_sse(collected) is called
  Then it returns "gen-abc"

Scenario: Extractor returns None when no id present
  Given collected chunks with no id field (or only [DONE]/garbage)
  When extract_generation_id_from_sse is called
  Then it returns None
  And it does not raise

Scenario: Disconnect record carries the generation id
  Given a client disconnect whose collected chunks carry "id":"gen-xyz"
  When the disconnect record is fired
  Then the recorded call's provider_generation_id == "gen-xyz"

Scenario: The id lands on the ledger column
  Given a recorder event with provider_generation_id="gen-1"
  When the flusher inserts it
  Then usage_records.provider_generation_id == "gen-1" for that row
  And a row with no id stored has provider_generation_id NULL

Scenario: Adding the column does not change billing or mutate rows
  Given the migration is applied
  When a normal completion row is written
  Then provider_generation_id is NULL and cost_usd/provider_cost are unchanged
  And no UPDATE/DELETE is issued against usage_records
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
INTERNAL plumbing — no HTTP surface change.

extract_generation_id_from_sse(chunks: list[bytes]) -> str | None
  — pure; joins chunks; returns the `id` from the first data frame that has one; None otherwise.

UsageRecordExtras (TypedDict, total=False): + provider_generation_id: str
RecordingUsageRecorder.record(..., provider_generation_id: str | None = None)
  — threads to event_fields["provider_generation_id"] = provider_generation_id or ""  (""=NULL)
  — supported_extras += {"provider_generation_id"}
flusher INSERT: + provider_generation_id column (""→NULL via the existing empty-string convention)

Schema: usage_records += provider_generation_id TEXT NULL  (additive, nullable, append-only — no
  index in this task; the sweep's query index lands with t6.3). Migration down_revision = current head.
```

Status: FROZEN @ v1 — approved by Tin Dang (AUTO, autonomy:auto). Additive capture rail for the
append-correction recovery model (Tin's 2026-06-22 decision); no billing change, append-only preserved.
Least-sure flag surfaced at freeze: [spec] the gen id is in the SSE `id` field — verified from wire
format not a live capture; cost: NULL + recovery no-ops (safe fallback to today's behavior). Mitigation:
graceful None + t6 live-verify.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the extractor + the capture threading.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_extract_generation_id_present: chunks with "id":"gen-abc" → returns "gen-abc". (extractor unit)
  - test_extract_generation_id_absent: no id / [DONE] / garbage → None, no raise. (extractor unit)
  - test_extract_generation_id_split_across_chunks: id frame split across byte chunks → still found.
  - test_disconnect_record_carries_generation_id: in stream_disconnect_abort harness, a disconnect whose
    collected chunks carry an id → MarkerSpyRecorder.last_call["provider_generation_id"] == that id.
  - test_flusher_persists_generation_id: recorder event w/ provider_generation_id → row column set; empty
    → NULL. (flusher suite, needs Postgres — mirrors existing flusher tests)
</test_plan>

Tests live in: `apps/gateway/tests/provider_generation_id_capture/test_provider_generation_id_capture.py`
(extractor + disconnect-capture units) and an added case in the existing flusher suite. MUST run red first.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/domain/extractor.py` `apps/gateway/src/gateway/proxy/domain/ports.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/migrations/versions/`
Strategy (ordered batches): 1. extractor helper. 2. ports TypedDict field. 3. recorder (supported_extras
  + param + event_fields). 4. flusher INSERT. 5. orm column. 6. Alembic migration (head=current). 7.
  use_cases: extract gen-id in the disconnect handler + pass to _fire_record_with_raw (param→extras).
Safety rule (feature-specific): purely additive + nullable; NO UPDATE/DELETE on usage_records; no billing
  amount change; empty-string encodes NULL in the event (existing convention).
Code lives in: the scope files above.
Constraints: do NOT change any test or the contract; allow-list packages only; mirror v27 usage_source.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 5/5 new (tests/provider_generation_id_capture/); full suite **1277 passed**
      (incl. the migrations-consistency suite validating the new revision).
- [x] coverage did not decrease — extractor + capture chain covered; net +5 tests.
- [x] no test or contract was altered during build — contract FROZEN @ v1 untouched; no re-cross needed.
- [x] the green was EARNED — adversarial refute-read (general-purpose sonnet) traced ALL 10 hops of the
      field's journey (extractor→use_cases→_fire_record_with_raw→_dispatch_record→recorder→event→flusher→
      column→orm→migration): VERDICT NOT-REFUTED, no dropped field, no migration branch, append-only intact,
      v1-fake backward-compat preserved, DB round-trip test SELECTs the column back (not vacuous). 3 NITs,
      all confirmed-correct (forward-scan intentional; pii_masked asymmetry pre-existing; _settle 2-tick ok).
- [x] concurrency / timing safe — capture is a pure extract in the existing fire-and-forget path; no new await/lock.
- [x] no exposed secrets / injection / unexpected deps — gen-id is an opaque token; flusher uses parameterized SQL.
- [x] layering & dependencies follow CONVENTIONS.md — additive extras seam exactly as v27 usage_source.
- [x] a person reviewed and approved the change — contract FROZEN @ v1 by Tin (AUTO); refute-read + this gate.

### Build expectations — what "correct" looks like
- [x] disconnect row with an id-carrying stream → provider_generation_id set; normal row → NULL —
      confirmed by test_flusher_persists_generation_id (SELECT shows "gen-1" and None).
- [x] the captured id reaches the record() call on the GeneratorExit path — confirmed by
      test_disconnect_record_carries_generation_id (last_call["provider_generation_id"]=="gen-xyz").
- [x] no billing change — refute confirmed cost_usd/cost_basis/provider_cost computed before event_fields;
      the DB test's normal row still bills >0.
- [x] migration down_revision == prior head b8e4f1a7c2d5; ORM column matches (Text, nullable) — refute confirmed.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — extract_generation_id_from_sse consumed in the disconnect handler;
      provider_generation_id threaded through every layer (refute traced all 10 hops); migration in chain.
- [x] DEAD-CODE (code) — no orphan; the field is read end-to-end (extractor → column).
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang (AUTO, autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): count of client_disconnect rows WITH vs WITHOUT a
provider_generation_id (NULL rate = how often the id wasn't in the stream → unrecoverable).

### Spec delta
- [SPEC · seeded] t6.2b openrouter-cost-recovery: inline fire-and-forget recovery — for an OpenRouter
  client_disconnect row with a provider_generation_id, call get_generation (retry-until-ready, bounded),
  and APPEND an 'openrouter_recovered' correction row (cost_basis='provider', delta of real−already-billed)
  so the customer is billed the real amount. (Tin: append-correction model, preserves append-only.)
- [SPEC · seeded] t6.3 openrouter-recovery-sweep: periodic backstop — find client_disconnect rows with a
  provider_generation_id and NO matching 'openrouter_recovered' row; back-fill via get_generation;
  add the query index (partial, WHERE provider_generation_id IS NOT NULL) + dedup (NOT EXISTS check).
- [SPEC · open] live-verify the SSE `id` shape on a real OpenRouter stream (capture currently relies on
  the documented OpenAI-compatible shape) — fold into the t6 live pass.

### Competency deltas
- [ADD · folded] a 10-hop additive field threading is best de-risked by a refute-read that traces EVERY hop [folded foundation-version 28]
  (the silent-drop failure mode hides between Redis event and Postgres column) — refute confirmed all 10.
- [SDD · folded] mirroring an existing field end-to-end (v27 usage_source) is the cheapest safe way to add a [folded foundation-version 28]
  ledger column — same extras seam, same NULL-encoding, same migration shape (evidence: byte-for-byte template).
