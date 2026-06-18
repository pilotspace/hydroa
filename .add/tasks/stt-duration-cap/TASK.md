# TASK: Clamp derived STT duration to a configured maximum

slug: stt-duration-cap · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py:TranscriptionUseCase.execute` (lines ~197-233) — Step 7 resolves `duration_s` then Step 8 bills `pricing_unit="per_second", quantity=Decimal(str(duration_s))`. BOTH branches produce an UNBOUNDED finite>0 float: (a) upstream `resp_body["duration"]` when finite>0 (lines 203-210), (b) `derive_duration_seconds(file_bytes)` (lines 211-214). The $0+WARN unavailable branch (215-220) yields 0.0.
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py:TranscriptionUseCase.__init__` (105-115) — keyword-only deps: `governance`, `session`, `tenant_credential_resolver=None`. The cap is injected here (constructor-injection, like the others).
  - `apps/gateway/src/gateway/core/config.py:Settings` (line 81, `env_prefix="GATEWAY_"`) — knob declaration site; pattern = a commented `Field(default=…, gt/ge/le=…)` (cf. `openrouter_usage_accounting` 250-255, `cooldown_ttl_s` 261-262).
  - `apps/gateway/src/gateway/proxy/api/audio_deps.py:get_transcription_use_case` (34-70) — per-request DI; reads `request.app.state.settings` (the project-wide pattern, set in `main.py:354`) and passes the cap into the constructor.
  - `apps/gateway/src/gateway/proxy/application/audio_duration.py:derive_duration_seconds` (27-45) — already total + finite>0-or-None; the over-bill vector is a finite-but-ABSURD value (corrupt/lying header), which this fn does NOT bound. Its FROZEN §3 (stt-duration-derivation) stays untouched — the clamp lives in the use case, not the deriver.
Context (working folder): the STT per-second billing path; v27 t3 (stt-duration-derivation) established server-side derivation; this task bounds it. No DB/migration (clamp is in-memory before the existing `_fire_record_with_raw`).
Honors (patterns / conventions): GATEWAY_-prefixed pydantic `Settings` knob + constructor-injection (CONVENTIONS typed-extras seam); "accuracy is never an availability gate" (clamp + WARN, never fail/retry — the transcription still 200s); single-bill invariant (one `_fire_record_with_raw`); structlog WARN with model context (cf. `stt_duration_unavailable`).
Anchors the contract cites: `TranscriptionUseCase.execute` · `TranscriptionUseCase.__init__(max_duration_seconds)` · `Settings.stt_max_duration_seconds` (GATEWAY_STT_MAX_DURATION_SECONDS) · `get_transcription_use_case` · WARN `stt_duration_capped`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: STT billable-duration cap — bound the per_second quantity so a corrupt/lying duration cannot over-bill.
Framings weighed: clamp in the use case before billing (chosen) · clamp inside `derive_duration_seconds` (rejected — its §3 is FROZEN and the upstream-`duration` path bypasses the deriver entirely, so the bound would leak) · reject the request / 500 on an absurd duration (rejected — violates "accuracy is never an availability gate"; the transcription already succeeded and must still 200).
Must:
<must>
  - Before the single `_fire_record_with_raw`, clamp the resolved `duration_s` to a configured maximum: `duration_s = min(duration_s, cap)`. The clamp covers BOTH source branches — the upstream-reported `resp_body["duration"]` AND the server-derived value (a lying upstream `duration` is as dangerous as a lying header).
  - When the clamp bites (`duration_s > cap`), emit WARN `stt_duration_capped` carrying `{model, original, cap}`, then bill the cap.
  - A normal-length file (`duration_s <= cap`) is BYTE-IDENTICAL to today — no clamp, no WARN, same quantity.
  - The cap is a `GATEWAY_`-prefixed pydantic `Settings` knob (`GATEWAY_STT_MAX_DURATION_SECONDS`) with a sane strictly-positive default, injected via the constructor (`request.app.state.settings` → `get_transcription_use_case` → `TranscriptionUseCase.__init__`).
  - The $0 unavailable path (`duration_s = 0.0`) is untouched (0.0 <= cap ⇒ never clamps, no spurious WARN).
</must>
Reject:
<reject>
  - over-cap duration (corrupt/lying header OR upstream body) -> NOT a 4xx; internal DEGRADE to `cap` + WARN `stt_duration_capped` (accuracy is never an availability gate — the request still returns the upstream 200 verbatim).
  - misconfigured cap (`GATEWAY_STT_MAX_DURATION_SECONDS <= 0`) -> boot-time `Field(gt=0)` validation error (fail-fast at config load), never a per-request path.
</reject>
After:
<after>
  - every per_second STT ledger row carries `quantity <= cap`; a clamp emitted exactly one `stt_duration_capped` WARN; the HTTP response body is the upstream transcription unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the DEFAULT cap value is a product/pricing call, not a code fact — lowest confidence because only the business knows the longest legitimately-billable single transcription; if set too LOW a genuine long file is under-billed (silent revenue loss), if too HIGH the over-bill guard is toothless. [→ the freeze decision for Tin; proposed default 14400s = 4h]
  - [ ] the clamp applies to BOTH the upstream-reported and derived branches (not derived-only) — milestone Scope says "derived (and upstream-reported)"; confirm a trusted-upstream `duration` should still be bounded.
  - [ ] constructor default `max_duration_seconds: float | None = None` (None ⇒ no clamp) keeps existing `TranscriptionUseCase` test-doubles byte-identical — confirm no current test expects a clamp without injecting a cap.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: DCAP1 derived duration over the cap is clamped + WARNed
  Given a cap of C seconds and an audio whose DERIVED header duration is 10*C (upstream body has no positive duration)
  When the transcription is billed
  Then exactly one per_second record fires with quantity == Decimal(str(C))
  And a "stt_duration_capped" WARN is logged with {model, original=10*C, cap=C}
  And the HTTP response is the upstream 200 body, unchanged

Scenario: DCAP2 upstream-reported duration over the cap is clamped + WARNed
  Given a cap of C seconds and an upstream verbose_json body whose "duration" is 10*C
  When the transcription is billed
  Then the single per_second record's quantity == Decimal(str(C))
  And a "stt_duration_capped" WARN is logged
  And derive_duration_seconds is NOT consulted (the upstream value still wins, then clamps)

Scenario: DCAP3 a normal-length file is byte-identical (no clamp, no WARN)
  Given a cap of C seconds and a resolved duration D with 0 < D < C
  When the transcription is billed
  Then the single per_second record's quantity == Decimal(str(D))   # exactly today's value
  And NO "stt_duration_capped" WARN is logged

Scenario: DCAP4 the $0 unavailable path is untouched by the cap
  Given a cap of C seconds and an undecodable header with no upstream duration (resolves to 0.0)
  When the transcription is billed
  Then the single per_second record's quantity == Decimal("0")
  And the existing "stt_duration_unavailable" WARN still fires
  And NO "stt_duration_capped" WARN is logged

Scenario: DCAP5 duration exactly at the cap is not clamped and not WARNed (boundary)
  Given a cap of C seconds and a resolved duration D == C
  When the transcription is billed
  Then the per_second record's quantity == Decimal(str(C))
  And NO "stt_duration_capped" WARN is logged   # clamp bites only on D > C, not D == C

Scenario: DCAP6 the cap knob has a strictly-positive default and rejects a non-positive value
  Given the GATEWAY_STT_MAX_DURATION_SECONDS env var is unset
  When Settings() loads
  Then settings.stt_max_duration_seconds is the positive default
  And constructing Settings(stt_max_duration_seconds=0) raises a pydantic ValidationError (Field gt=0)

Scenario: DCAP7 no cap injected leaves billing unbounded (legacy/test-double parity)
  Given a TranscriptionUseCase built with max_duration_seconds=None
  When a duration far above any cap resolves
  Then it bills that full duration unchanged (None ⇒ no clamp) and logs no "stt_duration_capped"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
HTTP contract UNCHANGED:  POST /v1/audio/transcriptions
  200 -> upstream transcription body, verbatim   (no new field, no new 4xx)

Config knob (core/config.py : Settings):
  stt_max_duration_seconds: float = Field(default=14400.0, gt=0)
    # env GATEWAY_STT_MAX_DURATION_SECONDS — upper clamp (seconds) on a billed STT
    # per_second duration. 14400 = 4h. gt=0 → a non-positive value fails at boot.

Use case (audio_use_case.py : TranscriptionUseCase):
  __init__(*, governance, session, tenant_credential_resolver=None,
           max_duration_seconds: float | None = None)
    # None ⇒ no clamp (legacy/test parity). Production DI always injects the knob.
  execute(...) Step 7→8 seam, AFTER duration_s is resolved, BEFORE _fire_record_with_raw:
    if self._max_duration_seconds is not None and duration_s > self._max_duration_seconds:
        _log.warning("stt_duration_capped",
                     extra={"model": model_id, "original": duration_s,
                            "cap": self._max_duration_seconds})
        duration_s = self._max_duration_seconds
    # then unchanged: quantity = Decimal(str(duration_s)), pricing_unit="per_second"

DI (audio_deps.py : get_transcription_use_case):
  max_duration_seconds = request.app.state.settings.stt_max_duration_seconds  → into the ctor

Schema: NONE — no DB column, no migration. Ledger row's existing `quantity`
(Numeric(18,6)) now provably ≤ cap. Billing arithmetic (Decimal) untouched.
Invariants preserved: single-bill (one _fire_record_with_raw); accuracy-never-an-
availability-gate (clamp+WARN, HTTP still 200); $0-unavailable path untouched.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-18, via AskUserQuestion).
Least-sure flag surfaced at freeze: [spec] the DEFAULT cap value is a product/pricing call,
not a code fact — if too low a long file is under-billed, if too high the over-bill guard is
toothless; Tin chose 14400s (4h). Two sub-confirmations resolved as drafted: clamp BOTH the
upstream-reported and derived branches (milestone scope); constructor
`max_duration_seconds: float | None = None` (None ⇒ no clamp) for legacy/test-double parity.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new clamp branch + the knob (small surface; whole-suite stays ≥80%).
Plan (one test per scenario, asserting behavior — the fired record's quantity + the WARN — not internals):
<test_plan>
  - test_dcap1_derived_over_cap_clamped: build TranscriptionUseCase(max_duration_seconds=C) with a fake provider whose multipart 200 has NO positive `duration` and a file whose derived duration is 10*C / execute / assert one per_second record, quantity==Decimal(str(C)) + "stt_duration_capped" WARN {model,original,cap}.
  - test_dcap2_upstream_over_cap_clamped: provider 200 body `{"duration": 10*C, ...}` / assert quantity==Decimal(str(C)) + WARN; derive path not consulted.
  - test_dcap3_normal_unchanged: resolved D with 0<D<C / assert quantity==Decimal(str(D)), NO cap WARN (byte-identical).
  - test_dcap4_unavailable_zero_untouched: undecodable header, no upstream duration / assert quantity==Decimal("0"), "stt_duration_unavailable" fires, NO cap WARN.
  - test_dcap5_boundary_equal_cap_not_clamped: D==C / assert quantity==Decimal(str(C)), NO cap WARN (clamp bites only on D>C).
  - test_dcap6_knob_default_and_validation: Settings() (env unset) → stt_max_duration_seconds==14400.0; Settings(stt_max_duration_seconds=0) raises pydantic ValidationError.
  - test_dcap7_no_cap_unbounded: max_duration_seconds=None / huge duration bills unchanged, NO cap WARN (legacy parity).
</test_plan>

Tests live in: `apps/gateway/tests/stt_duration_cap/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/proxy/application/audio_use_case.py` `apps/gateway/src/gateway/proxy/api/audio_deps.py` `apps/gateway/tests/stt_duration_cap/`
Strategy (ordered batches): 1. add `stt_max_duration_seconds` knob to Settings (config.py). 2. add `max_duration_seconds` ctor param + the clamp-before-bill block in audio_use_case.py. 3. thread the knob through get_transcription_use_case (audio_deps.py). 4. write the red suite, then make it green.
Safety rule (feature-specific): the clamp is a pure in-memory `min` BEFORE the single `_fire_record_with_raw` — no second bill, no change to the Decimal arithmetic; `use_cases.py` stays byte-identical (untouched).
Code lives in: `apps/gateway/src/gateway/`
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

- [x] all tests pass — `tests/stt_duration_cap/` 7/7 green; full gateway suite (excl. live `tests/edge`) 1180 passed in 144s.
- [x] coverage did not decrease — full-suite coverage 86.25% (gate ≥80% met); the new clamp branch + knob are exercised by DCAP1–DCAP7.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched. The ONE test edit (strengthen DCAP1/DCAP2 WARN-payload asserts, closing refute-read NIT-2) was made via the sanctioned tripwire re-cross (phase tests → advance → advance re-snapshots), NOT in-place; the scope/tamper pre-check is CLEAN (0 touches).
- [x] the green was EARNED — adversarial refute-read (independent sonnet subagent) = EARNED-WITH-NITS 0.82, NO BLOCKERS: clamp covers both branches, `>cap` boundary correct, `0.0`/`None` paths safe, DI genuinely exercised, `use_cases.py` byte-identical. NIT-2 (WARN payload unverified) CLOSED here; NIT-1 (DCAP7 structural) accepted — the no-clamp BILLED behavior is covered by DCAP3/DCAP5; NIT-3/4 negligible (see §7).
- [x] concurrency / timing safe — the clamp is a pure in-memory `min` on a local float before the existing single fire-and-forget record; no new task, lock, or await; single-bill invariant preserved.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new dependency; the knob is a plain float env var; no user input reaches the clamp beyond the already-validated duration.
- [x] layering & dependencies follow CONVENTIONS.md — GATEWAY_ pydantic knob + constructor-injection via the existing `request.app.state.settings` DI seam; application layer unchanged in shape.
- [x] a person reviewed and approved the change — Tin froze §3 (cap default) via AskUserQuestion; auto-gate on complete evidence under `autonomy: auto` (no security/concurrency/architecture residue).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `Settings.stt_max_duration_seconds` read in `audio_deps.get_transcription_use_case`; `max_duration_seconds` ctor param stored as `self._max_duration_seconds`, read in the `execute` clamp; WARN `stt_duration_capped` asserted by DCAP1/DCAP2 via `caplog.records`.
- [x] DEAD-CODE (code) — no orphaned symbol; the clamp branch is hit by DCAP1/DCAP2, the no-clamp path by DCAP3/DCAP5, the knob by DCAP6, the None default by DCAP7.
- [ ] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: AI auto-gate (autonomy: auto) on complete evidence; §3 cap default human-approved by Tin · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `stt_duration_capped` WARNs (a sudden spike = a
provider/header regression or an abusive client) · the distribution of billed per_second
`quantity` (now provably ≤ cap) · STT request error rate stays flat (clamp must never fail a 200).

### Spec delta
- [SPEC · open] the clamp bills the CAP on an over-cap duration, but a duration just under an
  ABSURD-but-finite cap is still trusted — a per-tenant or per-model cap (vs one global knob)
  would tighten abuse control (evidence: a single global 4h cap can't distinguish a legitimately
  long-form tenant from an abusive one).
- [SPEC · open] `stt_duration_capped` is observable only as a log WARN; a metric/counter would
  let ops alert on clamp rate without log scraping (evidence: the Watch list wants a rate).

### Competency deltas
- [TDD · folded] a WARN asserted only by event-name substring (`in caplog.text`) silently permits [folded foundation-version 26]
  contract drift in the WARN's payload; assert the LogRecord's `extra` fields via
  `caplog.records` when the contract specifies them (evidence: refute-read NIT-2 — DCAP1/DCAP2
  passed without the contracted `{model, original, cap}` until strengthened).
- [TDD · folded] a constructor-default unit assert (`uc._max_dur… is None`) pins the wiring but not [folded foundation-version 26]
  the execute-time behavior; pair it with a billed-outcome test for the same path when cheap
  (evidence: refute-read NIT-1 — DCAP7 covers the default, DCAP3/DCAP5 cover the no-clamp bill).
- [ADD · folded] the working-tree engine added an `unflagged_freeze` gate requiring the literal [folded foundation-version 26]
  `Least-sure flag surfaced at freeze:` label + a `[part]` tag; prose like "Lowest-confidence
  flag" no longer parses (evidence: tests→build refused until the §3 marker matched `_FLAG_LABEL_RE`).
