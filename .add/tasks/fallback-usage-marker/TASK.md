# TASK: Stamp credential_source (platform|byok) on the usage record

slug: fallback-usage-marker · created: 2026-07-16 · stage: production
milestone: platform-key-default
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/application/use_cases.py`:
  - `_resolve_platform_fallback(...)` — task-1 helper; calls `mark_platform_fallback()` right after the
    platform scope is bound. THE single seam all 4 verbs pass through when fallback fires → the one
    publish point for the new `_credential_source_ctx`.
  - `_dispatch_record(usage_recorder, *, tenant_id, key_id, model, usage, status, extras=None)` (:433) —
    the ONE fire-and-forget dispatch. Already folds request-scoped values in from contextvars
    (`_tier_served_ctx`, `_cc_attribution_ctx`) filtered against `usage_recorder.supported_extras`. New
    read site for `_credential_source_ctx`.
  - `_cc_attribution_ctx` / `_publish_cc_attribution()` (:364/:369) — the EXACT publish-once-consume idiom
    to mirror for `_credential_source_ctx` (a contextvar, set once, read in `_dispatch_record`, filtered
    against `supported_extras` — a v1 fake without the capability silently gets nothing).
- `apps/gateway/src/gateway/proxy/application/recorder.py` → `usage/application/recorder.py`:
  - `RecordingUsageRecorder.supported_extras: frozenset[str]` (:85) — declares accepted extras; add
    `"credential_source"`.
  - `RecordingUsageRecorder.record / record_with_outcome / _record_internal` (:116/:194/:272) — add
    `credential_source: str | None = None` kwarg threaded through all three; stamp into `raw_payload`.
  - `raw_payload` build (:~585) — the JSONB extras seam where `request_id` / `cc_session_id` are stamped
    ONLY-when-present (the FROZEN `usage_records` table takes no new column). `credential_source` follows
    the identical only-when-present idiom.
- `apps/gateway/src/gateway/proxy/domain/ports.py`:
  - `UsageRecordExtras(TypedDict, total=False)` (:38) — add `credential_source: str` field + docstring.
- Signal source (task 1, READ-only here):
  `apps/gateway/src/gateway/proxy/domain/credential_context.py:served_via_platform_fallback()` /
  `mark_platform_fallback()` — the request-scoped "served via platform fallback" flag.
Context (working folder): milestone `platform-key-default` task 2 (task 1 `platform-credential-fallback`
  is done/PASS; it already exposes `served_via_platform_fallback()` + `mark_platform_fallback()`).
Honors (patterns / conventions): the contextvar publish-once / consume-in-`_dispatch_record` idiom
  (`_cc_attribution_ctx`, `_tier_served_ctx`); FROZEN `usage_records` → additive keys ride the `raw` JSONB
  extras seam, never a new column; `supported_extras` capability filtering (v1 fakes unaffected).
Seams consulted: usage-record raw-JSONB extras seam (same as request_id / cc_session_id).
Anchors the contract cites: `_dispatch_record`, `_resolve_platform_fallback`, `_credential_source_ctx`
  (new), `RecordingUsageRecorder.record` + `.supported_extras`, `UsageRecordExtras`, `raw_payload`.
Issues/Risks (→ feed §1):
- ⚠ ORDERING: in `stream()`, `reset_provider_credential` (:4071) fires BEFORE the terminal
  `_fire_record_with_raw` (:4088), which clears `served_via_platform_fallback()` → reading the flag AT
  dispatch time returns False for streamed fallback rows. MUST publish a dedicated contextvar at
  RESOLUTION time (never reset mid-request), not read the credential-scoped flag at dispatch.
- Byte-identity: every non-fallback row + existing recorder test must stay unchanged → the marker is
  stamped ONLY for fallback rows (absence ≡ byok), never stamped as a literal on every row.
- Cross-request leakage: each request is its own asyncio Task (context copied at task creation), so a
  set-only-on-fallback contextvar with default None cannot leak platform→byok across requests (same
  guarantee `_credit_hold_ctx` / `_tier_served_ctx` already rely on).
Related intent: milestone exit criterion 6 — "Usage served by the platform credential is marked
  `credential_source=platform` and still counts against the requesting tenant's own budget"; GLOSSARY
  term platform-fallback credential (task 1). Attribution + budget stay the requesting tenant's (task 1
  invariant, unchanged here).
Ground SHA: 3c27af5

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: credential_source usage-record marker — stamp `credential_source=platform` on the usage record
of any request served by the platform-fallback credential, so platform-subsidized usage is
distinguishable for reporting. Attribution + budget/rate stay the requesting tenant's, unchanged.
Framings weighed:
- Dedicated contextvar published at RESOLUTION time, consumed in `_dispatch_record` (chosen) — the only
  framing that survives the stream-path ordering trap (§0: reset fires before the terminal record).
- Read `served_via_platform_fallback()` directly inside `_dispatch_record` (rejected) — WRONG for the
  streaming terminal row: the credential scope is already reset by then.
- Read the flag inside the recorder itself (rejected) — the recorder runs fire-and-forget after reset;
  and it couples the usage subsystem to the proxy credential contextvar.
Must:
<must>
  - M1 — When a request is served via the platform-fallback credential, its usage record carries
    `credential_source="platform"` (stamped inside the `raw` JSONB extras, NOT a new column).
  - M2 — When a request is served by the tenant's OWN key (no fallback), its usage record carries NO
    `credential_source` key — byte-identical to today (absence ≡ byok).
  - M3 — A platform-fallback request's usage record still attributes tenant_id = the REQUESTING tenant,
    and its advisory spend counter still keys on the requesting tenant (budget unchanged — task-1 invariant).
  - M4 — The marker is published ONCE at credential-resolution time (the `_resolve_platform_fallback`
    seam) into a request-scoped contextvar that is never reset mid-request, so it is correct on BOTH the
    non-stream and stream terminal-record paths.
  - M5 — The extra is capability-filtered: a recorder without `"credential_source"` in `supported_extras`
    (v1-Protocol test fakes) receives only the base kwargs — byte-identical to today.
  - M6 — Covers all 4 verbs (chat/embeddings/images/audio) through the single shared seam, with zero new
    call sites in the non-chat use cases (they already route through `_dispatch_record`).
</must>
Reject:
<reject>
  - R1 — recorder lacks the capability (no `"credential_source"` in `supported_extras`) -> the extra is
    silently dropped; base record is byte-identical (no error, no crash).
  - R2 — no fallback fired (own key served) -> contextvar stays default None -> `_dispatch_record` adds
    nothing -> no `credential_source` key on the row.
</reject>
After:
<after>
  - A platform-fallback-served usage row is queryable as platform-subsidized (raw->>'credential_source'
    = 'platform'); every own-key row and every pre-existing row reads as byok (key absent).
  - Cost attribution + spend counters are unchanged for every row (marker is provenance-only).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Each proxied request runs in its own asyncio Task so a set-only-on-fallback contextvar (default None)
    cannot leak platform→byok across requests — lowest confidence because it rests on the ASGI-server
    per-request-task guarantee; if wrong: a byok row could be mis-marked platform. MITIGATED: this is the
    SAME guarantee `_credit_hold_ctx`/`_tier_served_ctx` already depend on, and a concurrency test
    (two interleaved requests, one fallback one own-key) pins it.
  - [x] The marker value set {"platform"} + absence≡byok satisfies the milestone's "platform | byok"
    reporting intent — confirmed: absence-encodes-default is the established idiom (pii_masked, cached).
  - [x] `_resolve_platform_fallback` is reachable/editable by task 2 — confirmed: it lives in use_cases.py
    (task 2 scope); adding a `.set()` beside `mark_platform_fallback()` is additive, weakens no task-1 contract.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: platform-fallback row is marked   # M1, M3
  Given a keyless tenant whose request is served by the platform-fallback credential
  When the usage record is dispatched
  Then the recorded raw payload has credential_source == "platform"
  And tenant_id on the record is the REQUESTING tenant (not the platform tenant)

Scenario: own-key row is unmarked   # M2, R2
  Given a tenant with its own provider key (no fallback)
  When the usage record is dispatched
  Then the recorded raw payload has NO "credential_source" key
  And the row is byte-identical to a pre-marker record

Scenario: streaming fallback row is marked despite mid-request credential reset   # M4
  Given a keyless tenant served by the platform-fallback credential on the STREAMING path
  And the credential scope is reset before the terminal usage record is dispatched
  When the terminal usage record is dispatched
  Then the recorded raw payload still has credential_source == "platform"
  And the marker did not depend on served_via_platform_fallback() being read at dispatch time

Scenario: capability-absent recorder drops the extra   # M5, R1
  Given a recorder whose supported_extras does NOT include "credential_source"
  When a platform-fallback usage record is dispatched
  Then only the base kwargs reach the recorder (no credential_source)
  And no error is raised and the base record is byte-identical to today

Scenario: no cross-request leak under interleave   # M4 (concurrency)
  Given one request served by fallback and a concurrent request served by its own key
  When both dispatch usage records
  Then the fallback row is marked "platform" and the own-key row is unmarked
  And neither request's marker bleeds into the other
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal seam contract (no HTTP surface — usage recording is fire-and-forget).

NEW contextvar  (use_cases.py):
  _credential_source_ctx: contextvars.ContextVar[str | None] = ContextVar(default=None)
  # published ONCE inside _resolve_platform_fallback(...) via _credential_source_ctx.set("platform"),
  # immediately after mark_platform_fallback(); never reset mid-request. Default None ⇒ byok.

_resolve_platform_fallback(...)  (use_cases.py, task-1 helper — additive edit):
  ... existing body ... mark_platform_fallback(); _credential_source_ctx.set("platform")  # <- added

_dispatch_record(usage_recorder, *, tenant_id, key_id, model, usage, status, extras=None) -> None:
  # additive read, mirrors the _cc_attribution_ctx block:
  cs = _credential_source_ctx.get()
  if cs is not None and "credential_source" in supported:
      kwargs["credential_source"] = cs

RecordingUsageRecorder.record / record_with_outcome / _record_internal:
  ... existing kwargs ..., credential_source: str | None = None   # threaded through all three
  supported_extras += {"credential_source"}
  # in _record_internal raw_payload build, ONLY-when-present:
  if credential_source is not None:
      raw_payload["credential_source"] = credential_source

UsageRecordExtras(TypedDict, total=False):  credential_source: str   # + docstring entry

Behavior:
  fallback-served request  -> usage_records.raw ->> 'credential_source' == "platform"
  own-key / pre-existing   -> 'credential_source' key ABSENT (≡ byok)
  recorder w/o capability  -> extra dropped; base record byte-identical
Schema: usage_records is FROZEN — NO new column. The marker rides the existing `raw` JSONB
  extras seam (identical idiom to request_id / cc_session_id). tenant_id, cost_usd, spend
  counters UNCHANGED (requesting tenant), marker is provenance-only.
```

Glossary deltas: credential_source: provenance marker on a usage record — "platform" when the row was
  served by the platform-fallback credential, absent (≡ "byok") when served by the tenant's own key.
Least-sure flag surfaced at freeze: [spec] cross-request contextvar isolation (§1 ⚠) — a set-only-on-
  fallback `_credential_source_ctx` (default None) must not leak platform→byok across concurrent
  requests; rests on the ASGI per-request-Task guarantee. Mitigated: SAME guarantee `_credit_hold_ctx`/
  `_tier_served_ctx` already depend on; pinned by the interleave concurrency test (§4).
Status: FROZEN @ v1 — approved by auto (project-lead)
Reported: no

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (module-level for the touched recorder/use_cases seams)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_platform_fallback_row_marked: arrange a captured-record recorder + publish _credential_source_ctx
    "platform" (via the real _resolve_platform_fallback seam) / act _dispatch_record / assert raw payload
    credential_source=="platform" AND tenant_id==requesting tenant · covers M1, M3
  - test_own_key_row_unmarked: arrange no fallback (ctx default None) / act _dispatch_record / assert NO
    credential_source key in the captured raw payload (byte-identical) · covers M2, R2
  - test_streaming_fallback_marked_after_credential_reset: arrange publish ctx "platform" THEN call
    reset_provider_credential (simulating the stream ordering) / act _dispatch_record / assert
    credential_source=="platform" still stamped (proves it does NOT read served_via_platform_fallback()
    at dispatch) · covers M4
  - test_capability_absent_drops_extra: arrange a fake recorder with supported_extras lacking
    "credential_source" + ctx "platform" / act _dispatch_record / assert only base kwargs captured, no
    error · covers M5, R1
  - test_no_cross_request_leak_interleave: arrange two asyncio Tasks — one sets ctx "platform", one leaves
    default / act both dispatch / assert fallback row marked, own-key row unmarked, no bleed · covers M4
  - test_recorder_stamps_credential_source_in_raw: arrange RecordingUsageRecorder.record_with_outcome with
    credential_source="platform" against a fake redis capturing XADD fields / act / assert
    json.loads(raw)["credential_source"]=="platform" AND spend counter keyed on requesting tenant · covers M1, M3
  - test_recorder_omits_credential_source_when_none: arrange record_with_outcome with credential_source=None
    / act / assert "credential_source" NOT in json.loads(raw) · covers M2
</test_plan>

Tests live in: `apps/gateway/tests/fallback_usage_marker/` · 8 tests, RED @ 3c27af5 for the right reason
  (ImportError: `_credential_source_ctx` absent from use_cases.py). MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py`
  `apps/gateway/src/gateway/usage/application/recorder.py`
  `apps/gateway/src/gateway/proxy/domain/ports.py`
  `apps/gateway/tests/fallback_usage_marker/`
Strategy (ordered batches):
  1. ports.py — add `credential_source: str` to `UsageRecordExtras` + docstring.
  2. recorder.py — add `"credential_source"` to `supported_extras`; thread `credential_source: str | None
     = None` through record → record_with_outcome → _record_internal; stamp `raw_payload` only-when-present.
  3. use_cases.py — add `_credential_source_ctx` (default None); `.set("platform")` inside
     `_resolve_platform_fallback` right after `mark_platform_fallback()`; read it in `_dispatch_record`
     (filtered against `supported`), mirroring the `_cc_attribution_ctx` block exactly.
Persona (required): generic
Spawn isolation (default): shared-tree — small additive single-developer change, no parallel writers;
  driven inline with red/green TDD (Rule 3).
Known-problem fixes:
  - stream ordering trap → publish at resolution time into a never-reset contextvar (NOT read the
    credential-scoped flag at dispatch).
  - byte-identity → only-when-present stamping; capability-filtered extra; no new column.
Strategy actually used: as planned (3 batches, in order). Also reflowed 2 PRE-EXISTING E501 long lines
  (ports.py:550 task-1 Protocol docstring · use_cases.py _resolve_platform_fallback call) — both in §5
  scope, formatting-only, non-semantic; NOT introduced by this task (CI is a no-op per org-billing-0step-ci
  so they were never caught). use_cases.py still carries 2 unrelated pre-existing ruff-format drifts
  (lines ~2506/~3195, other tasks' code) left untouched — reformatting them is scope creep.
Safety rule (feature-specific): marker is provenance-only — MUST NOT touch tenant_id, cost_usd, or any
  spend counter; attribution stays the requesting tenant's (task-1 invariant).
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 9/9 task2 green; regression task1(22)+usage(23)+proxy+task2 = 96 passed / 0 fail
- [x] coverage did not decrease — new seams fully exercised (7 tests cover M1–M6/R1–R2); additive-only
- [x] no test or contract was altered during build — only NEW test dir added; §3 FROZEN untouched
- [x] the green was EARNED, not gamed — refute-read EARNED (below); tests assert real raw-JSONB content +
      real dispatch kwargs + reset-survival + interleave isolation against real seams, no vacuous asserts
- [x] concurrency / timing of the risky operation is safe — cross-request contextvar isolation pinned by
      test_no_cross_request_leak_interleave (separate asyncio Tasks copy context); marker never reset,
      set-only-on-fallback default None → no platform→byok bleed
- [x] no exposed secrets, injection openings, or unexpected dependencies — marker is a literal "platform"
      string; no secret in the chain; no new package
- [x] layering & dependencies follow CONVENTIONS.md — reuses the established contextvar publish-once /
      consume-in-_dispatch_record idiom (_cc_attribution_ctx); recorder stays decoupled from the proxy
      credential contextvar (value passed IN, not read)
- [x] a person reviewed and approved the change — auto-gated (auto autonomy, non-security marker); Tin's
      standing "PASS — keep moving" for milestone platform-key-default; spot-audit backstop

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a platform-fallback-served usage record's raw JSONB has credential_source=="platform" — confirmed by
      test_recorder_stamps_credential_source_in_raw (json.loads(XADD raw)["credential_source"]=="platform")
- [x] an own-key / pre-existing record has NO credential_source key (byte-identical) — confirmed by
      test_recorder_omits_credential_source_when_none + test_dispatch_omits_credential_source_when_ctx_none
- [x] the marker survives the stream-path credential reset — confirmed by
      test_dispatch_survives_credential_reset (served_via flag False, marker still "platform")
- [x] tenant_id stays the requesting tenant (budget unchanged) — confirmed: raw["tenant_id"]==requester;
      diff shows credential_source only appended to raw_payload, cost_usd/spend counters untouched

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_credential_source_ctx` set at use_cases.py:1396 (_resolve_platform_fallback),
      read at _dispatch_record; `credential_source` threaded record→record_with_outcome→_record_internal
      and stamped in raw_payload; "credential_source" in supported_extras + UsageRecordExtras. All 4 verbs
      route through _dispatch_record (non-chat use cases import _fire_record_* — zero new call sites).
- [x] DEAD-CODE (code) — no orphan: the ctx has one set + one read; the recorder kwarg is forwarded at
      each layer; the TypedDict field + supported_extras entry are both consumed by the filter.
- [x] SEMANTIC — n/a (code task).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every §3 symbol resolves in the current tree — `_dispatch_record`, `_resolve_platform_fallback`,
      `_credential_source_ctx`, `RecordingUsageRecorder.record`/`.supported_extras`, `UsageRecordExtras`,
      `raw_payload` all present and referenced (grep-confirmed during build).
- [x] no anchor moved/renamed since Ground SHA 3c27af5 (same working tree; task1 uncommitted alongside).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self · adversarially checked: (1) does the marker ever alter cost/attribution? NO — diff shows
  credential_source only appended to raw_payload; cost_usd/tenant_id/incrbyfloat spend keys untouched.
  (2) can "platform" be set for a non-fallback request? NO — single set site inside
  _resolve_platform_fallback, reached only on ProviderKeyMissing + kill-switch ON; own-key path returns
  earlier. (3) does the stream terminal record read a stale/cleared flag? NO — dispatch reads the
  never-reset _credential_source_ctx, proven by test_dispatch_survives_credential_reset. (4) cross-request
  leak? NO — per-request asyncio Task copies context; interleave test green. (5) capability filter real?
  YES — a fake without "credential_source" in supported_extras drops it (test green, no error).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self
1. Security: CLEAR — no secret/auth surface; marker is a literal "platform"; attribution unchanged; the
   task-1 confused-deputy boundary (platform-tenant cache owner) is untouched by this provenance marker.
2. Concurrency: CLEAR — set-only-on-fallback never-reset contextvar, default None; per-request-Task
   context copy prevents cross-request bleed (interleave test green); no shared mutable state added.
3. Architecture: CLEAR — reuses the established publish-once/consume-in-_dispatch_record idiom; recorder
   remains decoupled (value passed in, not read from the proxy contextvar); FROZEN usage_records honored
   (raw JSONB seam, no new column).
Verdict: PASS
Residue: none
Binding: advisory — data/provenance marker (non-security; attribution + budget unchanged)

### GATE RECORD
Reported: yes — SHAPE/SUMMARY/EVIDENCE rendered in the turn narration before this outcome recorded
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a
Reviewed by: auto (project-lead) — auto-gated under autonomy:auto; Tin standing "PASS — keep moving" · date: 2026-07-16

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by auto (project-lead))
- [AI] build — strategy used: as planned (3 batches, in order). Also reflowed 2 PRE-EXISTING E501 long lines (ports.py:550 task-1 Protocol docstring · use_cases.py _resolve_platform_fallback call) — both in §5 scope, formatting-only, non-semantic; NOT introduced by this task (CI is a no-op per org-billing-0step-ci so they were never caught). use_cases.py still carries 2 unrelated pre-existing ruff-format drifts (lines ~2506/~3195, other tasks' code) left untouched — reformatting them is scope creep.
- [AI] verify — gate PASS (reviewed by auto (project-lead) — auto-gated under autonomy:auto; Tin standing "PASS — keep moving")

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

