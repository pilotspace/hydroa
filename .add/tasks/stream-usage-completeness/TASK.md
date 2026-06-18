# TASK: Guarantee exactly one billed usage record per stream even when the terminal usage frame is missing/partial

slug: stream-usage-completeness · created: 2026-06-18 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.stream._wrapped` (lines 1445-1512)
  — the streaming chat path. `_wrapped()` TEEs every SSE chunk into `collected: list[bytes]`, yields it, then
  AFTER the stream ends (line 1483-1494) calls `extract_usage_from_sse(collected)` → `extracted_usage` and
  fires EXACTLY ONE `_fire_record_with_raw(..., usage=extracted_usage, status=200, ...)`. The mid-stream
  error branch (line 1456-1467) fires its OWN single record (status=502) then `return`s — so it does NOT
  reach line 1484 (no double-bill). **Single-bill already holds; the gap is the CONTENT of the success record.**
- `apps/gateway/src/gateway/usage/domain/extractor.py:extract_usage_from_sse(chunks) -> dict|None` — pure;
  returns the `usage` dict from the LAST SSE frame carrying one (preserves nested `prompt_tokens_details` /
  `completion_tokens_details`, so the t1 tiered usage already survives the tee). Returns **None** when NO
  frame carries a usage dict (the terminal frame was omitted) → that None is the leak source.
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder._record_internal` (118-326)
  — with `usage=None` (per_token): prompt/completion/cached/reasoning all 0, `_safe_provider_cost(None)`=None
  → `compute_per_token_cost_usd(all zeros)` = **$0**, and the record is written with NO marker distinguishing
  "genuinely free" from "usage frame missing" → **the SILENT $0**. A PARTIAL frame (e.g. `total_tokens` 0 /
  missing `completion_tokens`) under-bills the same silent way.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record_with_raw` (295-340) + `_dispatch_record`
  (155-189) — the `UsageRecordExtras` typed seam (declared-capability filter via `supported_extras`); the
  record is `asyncio.ensure_future` fire-and-forget. New markers ride this seam (the t2 `cost_basis` precedent).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (cols 50-78) — has `raw` JSONB (50)
  + structured cols incl. t2's `cost_basis`/`provider_cost`. A new fallback marker is either a new additive
  column (t1/t2 migration precedent) OR a key inside `raw` (no migration). [CONTRACT DECISION — §1/§3.]

Context (working folder): chat TPM is POST-hoc — line 1496-1499 records TPM from the REAL `total_tokens`
AFTER the stream; `_enforce_governance` (629) computes **no pre-flight token estimate** for chat (only
embeddings passes `estimated_tokens=1`). So there is NO pre-flight estimate to fall back onto — the only
non-zero fallback count available is one DERIVED from the streamed content. No migration is forced (t4 can
ride `raw`); the test DB is localhost:5433 / Redis 6380. New suite `apps/gateway/tests/stream_usage_completeness/`.

Honors (patterns / conventions):
- **Documented fallback + WARN** (v9 gemini-embed precedent: `exact_tokens None → ceil(chars/4) estimate`
  with a logged warning; billing accuracy is best-effort, the product always ships). t4 mirrors this shape.
- **Accuracy is never an availability gate** (v12): a missing usage frame DEGRADES to a documented, flagged
  fallback record — it never fails or retries the stream (the bytes already reached the client).
- **Single fire-and-forget record** (the streaming seam invariant): exactly one record per stream; never block
  the response; swallow recorder errors.
- **Prefer the authoritative source** (t2/t3 precedent): a real, complete usage frame always wins unchanged
  (byte-identical); the fallback only fills the missing/partial case.
- **Additive marker via the typed extras seam** (t2 `cost_basis` precedent): a new flag rides
  `UsageRecordExtras` → recorder event dict → flusher, declared in `supported_extras`.

Anchors the contract cites: `CompletionUseCase.stream._wrapped` tee + the single `_fire_record_with_raw`,
`extract_usage_from_sse` → None / partial, `_record_internal` per_token $0 path, the new fallback marker on
the `UsageRecordExtras` seam, and the chosen fallback-count rule (flagged-$0 vs content-derived estimate).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: stream-usage-completeness — when a chat stream's upstream omits or partials the terminal `usage`
SSE frame, the single billed record is no longer a SILENT $0: it is stamped `usage_source=stream_fallback`
and a `stream_usage_frame_missing` WARN is logged, so the under-bill is observable and reconcilable.

Framings weighed:
- **flag a $0 fallback record + new `usage_source` column** (chosen, Tin 2026-06-18 via AskUserQuestion) —
  the stream site detects an incomplete frame and passes `usage_source="stream_fallback"`; cost stays $0 but
  the row is queryable/alertable. Smallest change, no heuristic token math in the money path, byte-identical
  for complete frames.
- bill a content-derived chars/4 estimate (rejected by Tin) — closes the under-bill but injects model-specific
  heuristic billing into the SSE tee (over/under-bill risk on chat).
- marker inside `raw` JSON, no migration (rejected by Tin) — lighter but not a first-class queryable column.

Must:
<must>
  - A chat stream whose upstream emits a COMPLETE terminal usage frame bills exactly as today — usage passed
    through unchanged, tiered `prompt_tokens_details`/`completion_tokens_details` surviving the tee,
    `usage_source="frame"`, NO warn. Byte-identical floor preserved.
  - A chat stream whose upstream OMITS the usage frame (no SSE frame carries a usage dict) still fires EXACTLY
    ONE billed record: status 200, cost $0, `usage_source="stream_fallback"`, and a `stream_usage_frame_missing`
    WARN — never a silent $0.
  - A chat stream whose usage frame is PARTIAL (a usage dict with no positive token count — total/prompt/
    completion all missing or 0) is treated the same as missing: `usage_source="stream_fallback"` + WARN.
  - Exactly ONE usage record per stream is preserved on every path (complete, missing, partial, mid-stream
    error) — derivation/flagging adds no extra record and skips none.
  - `usage_records` gains an additive `usage_source TEXT NOT NULL DEFAULT 'frame'` column; every existing
    caller (non-stream completions, embeddings, images, audio, cache hits, the 502 error record) defaults to
    `'frame'` with NO code change — the marker is opt-in via the typed extras seam.
  - `stream_usage_is_complete(usage)` is a pure, total predicate (never raises): True iff `usage` is a dict
    with at least one strictly-positive int among `prompt_tokens` / `completion_tokens` / `total_tokens`.
Reject:
<reject>
  - usage frame entirely absent (extractor → None) -> one record, `usage_source="stream_fallback"`, $0, WARN, 200.
  - usage dict present but no positive token count (partial) -> same stream_fallback treatment (not silent).
  - a malformed / non-dict usage value reaching the predicate -> `stream_usage_is_complete` returns False
    (→ fallback), never raises.
</reject>
After:
<after>
  - Every chat stream produces a ledger row whose `usage_source` is `frame` (real complete usage) or
    `stream_fallback` (missing/partial) — a $0 stream row is always one of those two, never an unexplained $0.
  - Operators can query/alert on `usage_source='stream_fallback'` to reconcile under-billed streams against
    provider invoices.
  - No HTTP-contract change, no change to the streamed bytes, no new request to the upstream.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ "complete" == any positive token count among prompt/completion/total — lowest confidence because a real
    provider COULD (rarely) send a usage frame with only `prompt_tokens_details` populated and a 0 top-level
    count; if wrong, such a frame is misflagged `stream_fallback`. Cost: a false-positive fallback marker on a
    rare frame shape (still bills the same $0 it would today; only the marker differs). Mitigated: the three
    top-level counts are present on every real OpenAI/OpenRouter usage frame.
  - [x] chat has NO pre-flight token estimate to reuse (confirmed in §0: TPM is post-hoc; governance passes
    none) — so a non-zero fallback would require content re-counting, which Tin rejected.
  - [x] the mid-stream-error path (status 502) stays `usage_source='frame'` (default) — it is already a
    non-silent error record distinguished by status, not a missing-frame fallback. Confirmed in §0.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SU1 complete usage frame bills unchanged (byte-identical)
  Given a chat stream whose upstream emits a terminal SSE frame with usage {prompt_tokens:10, completion_tokens:5, total_tokens:15}
  When the stream completes and the single usage record fires
  Then exactly one record is recorded with status 200, billed on those tokens (cost > 0)
  And usage_source == "frame" and NO stream_usage_frame_missing warning is logged

Scenario: SU2 missing terminal usage frame is flagged, never a silent $0
  Given a chat stream whose upstream emits content deltas but NO frame carrying a usage dict
  When the stream completes and the single usage record fires
  Then exactly one record is recorded with status 200 and cost == 0
  And usage_source == "stream_fallback" and a "stream_usage_frame_missing" WARNING is logged

Scenario: SU3 partial usage frame (no positive token count) is treated as fallback
  Given a chat stream whose terminal usage dict has total_tokens 0 and no positive prompt/completion count
  When the stream completes and the single usage record fires
  Then exactly one record is recorded, cost == 0, usage_source == "stream_fallback"
  And a "stream_usage_frame_missing" WARNING is logged

Scenario: SU4 tiered usage survives the tee on a complete frame
  Given a chat stream whose terminal usage frame carries prompt_tokens_details.cached_tokens and completion_tokens_details.reasoning_tokens
  When the stream completes and the single usage record fires
  Then the recorded usage preserves the cached_tokens and reasoning_tokens tiers (billed per t1)
  And usage_source == "frame"

Scenario: SU5 single-bill invariant on a missing-frame stream
  Given a chat stream whose upstream omits the usage frame
  When the stream is fully consumed
  Then the usage recorder is invoked EXACTLY once (not zero, not twice)
  And that one record carries usage_source == "stream_fallback"

Scenario: SU6 the new column is additive and defaults frame for non-stream callers
  Given the usage_records table after the migration
  When a non-stream record (a normal completion / embeddings / cache hit) is written without a usage_source
  Then the row's usage_source == "frame" (server default), and existing billing is byte-identical
  And the migration upgrade+downgrade round-trips with an empty autogenerate diff

Scenario: SU7 stream_usage_is_complete is a pure, total predicate
  Given the values None, {}, {"total_tokens":0}, {"prompt_tokens":0,"completion_tokens":0}, {"prompt_tokens":5}, {"total_tokens":15}, and a non-dict
  When stream_usage_is_complete is called on each
  Then it returns False, False, False, False, True, True, False respectively
  And it never raises
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal usage-completeness seam on the existing streaming chat path.
# NO HTTP-contract change, NO change to the streamed bytes, NO new upstream request.

## New pure predicate (usage/domain/extractor.py)
stream_usage_is_complete(usage: dict[str, object] | None) -> bool
  - True iff usage is a dict AND at least one of prompt_tokens / completion_tokens / total_tokens
    is a strictly-positive int (bool excluded). Else False. NEVER raises (total over bad input).

## CompletionUseCase.stream._wrapped — the post-stream single record (use_cases.py ~line 1483-1494)
  extracted_usage = extract_usage_from_sse(collected)        # UNCHANGED
  if stream_usage_is_complete(extracted_usage):
      usage_source = "frame"                                  # complete frame wins, byte-identical
  else:
      usage_source = "stream_fallback"
      _log.warning("stream_usage_frame_missing",
                   extra={"model": model_id, "tenant_id": str(tenant_id)})
  _fire_record_with_raw(usage_recorder, ..., usage=extracted_usage, status=200,
                        team_id=team_id, pii_masked=_stream_pii_masked,
                        usage_source=usage_source)            # NEW kwarg
  # The mid-stream-error 502 record and the TPM post-accounting are UNCHANGED.

## Recorder seam (the typed-extras path, t2 cost_basis precedent)
  - UsageRecordExtras += usage_source: str            (TypedDict, total=False)
  - _fire_record_with_raw(..., usage_source: str | None = None) -> forwards via extras when set
  - _record_internal(..., usage_source: str | None = None): resolved = usage_source or "frame";
    event_fields["usage_source"] = resolved
  - flusher reads _field("usage_source") or "frame" -> INSERT into the new column

## Schema (additive migration b8e4f1a7c2d5, revises a7d2e9c4f1b6 = t2 head)
  - usage_records += usage_source TEXT NOT NULL DEFAULT 'frame'
    (every historical + non-stream row reads true as a real 'frame'); downgrade drops it.
  - ORM UsageRecordRow += usage_source: Mapped[str] = mapped_column(Text, nullable=False,
    server_default="frame")

## Invariants (frozen)
  - A COMPLETE frame is byte-identical to today: usage passed through, tiered tiers survive, no WARN,
    usage_source='frame'. Provider-cost / tiered branches in the recorder are untouched.
  - Missing OR partial (no positive token count) -> exactly ONE record, cost $0, usage_source='stream_fallback'
    + 'stream_usage_frame_missing' WARN, status 200. Accuracy is never an availability gate.
  - Exactly one usage record per stream on every path (single-bill). usage_source defaults 'frame' for ALL
    non-stream callers (no code change) — the byte-identical floor for the whole rest of the system.
  - stream_usage_is_complete is pure + total (never raises); the marker is the ONLY new write.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-18 (flagged-$0 fallback + new usage_source column, both
chosen via AskUserQuestion). Lowest-confidence flag surfaced at the freeze: "complete == any positive
top-level token count" [spec] — a rare prompt_tokens_details-only frame would misflag stream_fallback (still
bills the same $0; only the marker differs). Changing this frozen contract = change request back to SPECIFY.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the new predicate + the stream resolution branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - SU1 test_complete_frame_bills_unchanged_source_frame: stream a terminal usage frame {10,5,15} via a fake
    upstream + SpyRecorder / assert one record, cost>0, usage_source=="frame", no WARN.
  - SU2 test_missing_frame_flagged_zero_not_silent: stream content deltas with NO usage frame / assert one
    record, status 200, cost 0, usage_source=="stream_fallback", caplog has "stream_usage_frame_missing".
  - SU3 test_partial_frame_treated_as_fallback: terminal usage {total_tokens:0} / assert usage_source==
    "stream_fallback" + WARN (one record).
  - SU4 test_tiered_usage_survives_tee: terminal frame with prompt_tokens_details.cached_tokens +
    completion_tokens_details.reasoning_tokens / assert the recorded usage preserves both tiers,
    usage_source=="frame".
  - SU5 test_single_bill_on_missing_frame: missing-frame stream / assert SpyRecorder.call_count == 1.
  - SU6 test_usage_source_column_additive_default_frame: a NON-stream record (normal completion / embeddings)
    persists with usage_source=="frame" via the live DB flusher; + migration upgrade/downgrade round-trip +
    empty autogenerate diff (DB test).
  - SU7 test_stream_usage_is_complete_table: pure unit table over the predicate (None/{}/{total:0}/
    {prompt:0,completion:0}/{prompt:5}/{total:15}/non-dict -> F,F,F,F,T,T,F); assert NONE raise.
</test_plan>

Tests live in: `apps/gateway/tests/stream_usage_completeness/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/domain/extractor.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/domain/ports.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/stream_usage_completeness/`
Strategy (ordered batches):
  1. Migration b8e4f1a7c2d5 (usage_source TEXT NOT NULL DEFAULT 'frame') + ORM column; `make migrate`.
  2. extractor.py: pure stream_usage_is_complete(usage) predicate.
  3. recorder.py + ports.py: UsageRecordExtras += usage_source; _record_internal resolves (default 'frame') +
     event field; flusher reads + INSERTs the column.
  4. use_cases.py _wrapped: compute usage_source from the predicate, WARN on fallback, pass the new kwarg.
Safety rule (feature-specific): complete frame stays byte-identical (usage_source default 'frame', no WARN);
  the marker is the ONLY new write; exactly one record on every path; recorder errors stay swallowed.
Code lives in: `apps/gateway/src/`
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

- [x] all tests pass — SU suite 18/18 (SU1–SU6 + SU7's 12 params incl. bool/float/negative); full gateway
      suite 1181 passed / 1 skipped / 1 xfailed; the ONLY failures are 14–16 `tests/edge/*` e2e cases that
      need the live Docker+Envoy+TLS stack (httpx.ConnectError — stack down), env-only, unrelated to billing.
      Migrations suite 6 passed (b8e4f1a7c2d5 upgrade-from-empty + autogenerate empty-diff).
- [x] coverage did not decrease — new predicate + stream-resolution branch fully exercised; extractor.py 93%
      (the 2 misses are pre-existing extract lines, not the new predicate); SU subset 18 green.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched. Test files WERE edited during
      VERIFY (SU7 +3 refute-read params: bool/float/negative) → tamper tripwire legitimately re-crossed via
      `phase tests` → `advance` (re-snapshot at tests→build) → `advance` (build→verify), clean, no heal burned.
- [x] the green was EARNED — adversarial refute-read (sonnet subagent) returned EARNED-GREEN-WITH-NITS: no
      BLOCKER/HIGH, all 8 contract invariants confirmed, predicate never raises across 24 inputs. 3 NITs all
      CLOSED: (1) SU6 docstring now states the create_all schema path + that the migration FILE is verified by
      tests/migrations/; (2) SU6 asserts the 'frame' row bills cost>0 (guards a complete-frame $0 regression);
      (3) SU7 now covers float (`{total_tokens:15.0}`→False) + negative (`{prompt_tokens:-1}`→False) + bool-with-
      real-int (`{prompt_tokens:True,completion_tokens:5}`→True). No overfit / vacuous assert / stubbed logic.
- [x] concurrency / timing safe — the fallback adds ZERO new IO: it is a pure in-memory predicate over the
      already-tee'd `collected` list, then one string kwarg on the SAME single fire-and-forget record. No new
      await, no upstream call, recorder errors stay swallowed; single-bill invariant unchanged on every path.
- [x] no exposed secrets / injection / unexpected deps — no new dependency (predicate is stdlib-only); the WARN
      logs only model_id + tenant_id (already logged elsewhere), no payload/secret; INSERT uses a bound
      `:usage_source` param (no string interpolation).
- [x] layering & dependencies follow CONVENTIONS.md — predicate in usage/domain (pure), marker rides the
      existing UsageRecordExtras typed seam → recorder event → flusher → ORM column (the t2 cost_basis path,
      replicated exactly); use_cases stays the orchestrator. No new cross-layer edge.
- [x] reviewed — auto-resolved under `autonomy: auto` on complete evidence (no security finding, no
      concurrency/architecture residue, autonomy not lowered). Refute-read + this battery are the evidence.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `stream_usage_is_complete` referenced by use_cases._wrapped + SU7; `usage_source` flows
      ports.UsageRecordExtras → _fire_record_with_raw → recorder.record/_record_internal (event_fields) →
      flusher._field → INSERT → ORM UsageRecordRow.usage_source. Confirmed via serena + the SU6 DB round-trip
      (both 'frame' and 'stream_fallback' rows persisted and read back).
- [x] DEAD-CODE (code) — no orphan: every new symbol (predicate, kwarg, column, event field) is on the live
      path; ruff check clean on touched paths (no unused import/var).
- [x] SEMANTIC — n/a (code task); the migration docstring + ORM contract comment read in full, consistent
      with §3 (additive TEXT NOT NULL DEFAULT 'frame', instant on PG 11+, downgrade drops).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy: auto — complete evidence, no security/concurrency/architecture residue) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `usage_records.usage_source='stream_fallback'` rows per
provider/model (SU2/SU3 as the live monitor) — a rising fallback rate means an upstream stopped sending
terminal usage frames and real revenue is being under-billed at $0; alert + reconcile against the provider
invoice. Also watch the `stream_usage_frame_missing` WARN rate as the leading signal.
Spec delta for the next loop: the $0-fallback marker makes the under-bill OBSERVABLE but does not RECOVER the
revenue. If production shows a material fallback rate for a given provider, the next loop should weigh a
content-derived token estimate (the option Tin rejected for now) GATED to that provider, or a post-hoc
reconciliation job that re-prices stream_fallback rows from the provider's billing API.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [SDD · folded] A streaming client DISCONNECT (GeneratorExit raised through `_wrapped` before the terminal
  frame) currently bills $0 with NO usage_source marker at all — the post-stream record block at use_cases.py
  ~1483 is skipped when the generator is closed early, so this is a SILENT $0 distinct from the missing-frame
  case this task closed. Evidence: §0 trace + manual read of `_wrapped`'s finally/GeneratorExit handling; out
  of this task's frozen scope (no scenario). Candidate next-loop task: stamp `usage_source='client_disconnect'`
  (or fold into stream_fallback) on the disconnect path so EVERY $0 stream row is explained, not just
  missing/partial frames.
- [TDD · folded] Refute-read NIT-3 (predicate table missing float/negative/bool-with-real-int rows) shows a
  pure-total-predicate test table should ENUMERATE the type-confusion axis (bool/float/negative/None/non-dict),
  not just the value axis (0 vs positive). Evidence: SU7 shipped green with 9 params, refute-read found the
  3 missing type cases; all now covered (12 params). Foundation: add "type-confusion row per non-int input
  class" to the pure-predicate test checklist.
- [ADD · folded] Editing a declared test file during VERIFY (to close refute-read NITs) requires the sanctioned
  tripwire re-cross (`phase tests` → `advance` ×2) — doing it in-place would burn a monotonic heal attempt.
  Evidence: re-crossed clean this task. Foundation: the refute-read→fix loop should always step back to `tests`
  before editing, never edit-in-verify. (Reconfirms the v25 tamper-tripwire ordering learning.)
