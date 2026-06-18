# TASK: Derive STT audio duration server-side so per_second billing is accurate without verbose_json

slug: stt-duration-derivation · created: 2026-06-17 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/audio_use_case.py:TranscriptionUseCase.execute` (lines 108-204)
  — the STT pipeline. `file_bytes = await file_field.read()` (line 157) ALREADY has the raw audio in hand.
  Step 7 (line 189): `duration_s = float(resp_body.get("duration") or 0.0)` — duration only exists when the
  caller passed `response_format=verbose_json`; otherwise 0.0 → Step 8 bills `quantity=Decimal("0")` at
  `pricing_unit="per_second"` → SILENT $0. THIS is the leak. I insert a server-side derivation between
  read and billing: prefer `resp_body["duration"]` when present (authoritative), else derive from `file_bytes`.
- NEW module (proposed) `apps/gateway/src/gateway/proxy/application/audio_duration.py:derive_duration_seconds(
  data: bytes) -> float | None` — pure, content-sniffing header decoder; None when undecodable (caller → $0).
- `apps/gateway/tests/audio_endpoints/` — FROZEN sibling suite. AU2 (duration=12.5 → quantity 12.5) and
  **AU2b** (no upstream duration → quantity == Decimal("0")) pin today's behavior. AU2b uploads
  `FAKE_AUDIO_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt "` (conftest.py:61) — a TRUNCATED/INVALID WAV (no data
  chunk, zero sizes). A robust decoder returns None for it → $0 fallback → **AU2b stays GREEN, untouched**.
  No frozen-test conflict: derivation only fires on genuinely decodable headers (my own tests supply those).

Context (working folder): STT path billed per_second since the pricing-units milestone. `_fire_record_with_raw`
+ `pricing_unit`/`quantity` seam (the same recorder path t2 just extended) carries the cost. No migration —
this task changes a COUNT SOURCE, not the schema. Test DB localhost:5433, Redis 6380 db 9. New suite
`apps/gateway/tests/stt_duration_derivation/` (per the milestone exit-criterion verify line).

Honors (patterns / conventions):
- **Accuracy is never an availability gate** (v12): an undecodable / corrupt / unknown-format header
  DEGRADES to the documented $0 + WARN fallback (today's behavior for the absent case) — never fails the
  transcription. The product (the transcript) always ships.
- **Prefer the authoritative source** (t2 precedent): upstream `duration` (when verbose_json) wins; derive
  only when it's absent. Same prefer-X-fallback-Y shape as provider-cost.
- **Pure, fail-safe extractor** (`_safe_tier`/`_safe_provider_cost` precedent): `derive_duration_seconds`
  never raises; bad bytes → None.
- **No heavy dependency** (milestone constraint): decode the container header ourselves / via a light
  pure-Python reader — NOT ffmpeg/pydub/librosa. [DEPENDENCY CHOICE FLAGGED FOR THE FREEZE — see §1.]
- **Content-sniff, don't trust filename/content_type** (AU2b uploads WAV-ish bytes under filename audio.mp3 /
  audio/mpeg — the bytes can lie; key derivation on magic bytes).
- **Allow-list gate**: `make allowlist` (scripts/check_allowlist.py) gates imports; any new dep updates
  pyproject `dependencies` + the allowlist.

Anchors the contract cites: `TranscriptionUseCase.execute` step 7, `derive_duration_seconds`, the
`resp_body["duration"]`-preferred / file-derived / None→$0 decision, `pricing_unit="per_second"` +
`quantity`, the FROZEN AU2b $0 fallback (preserved by the undecodable-header path).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: stt-duration-derivation — when the STT upstream does not report `duration` (the caller omitted
`response_format=verbose_json`), derive the audio duration server-side from the uploaded file header so the
`per_second` bill is accurate instead of a silent $0.

Framings weighed:
- **prefer upstream `duration`; else derive from the file header via tinytag; else $0+WARN** (chosen) —
  inserts a pure `derive_duration_seconds(bytes) -> float | None` between the file read and the bill;
  smallest change to the frozen STT flow, byte-identical when upstream reports duration, fail-safe fallback
  identical to today when the header is undecodable. Tin chose tinytag (light pure-Python) for coverage.
- hand-rolled multi-format header parser (rejected by Tin) — ~300 lines of binary parsing in a money path.
- always re-derive, ignore upstream duration (rejected) — discards the authoritative model-reported value
  and risks a derived/upstream mismatch on the common verbose_json path (would perturb AU2's 12.5).

Must:
<must>
  - When the upstream response carries a valid POSITIVE `duration` number, bill on it UNCHANGED (today's
    behavior; AU2 with duration=12.5 stays byte-identical) — `quantity = Decimal(str(duration))`,
    pricing_unit="per_second". This is the authoritative source; never overridden by derivation.
  - When the upstream `duration` is absent / zero / non-numeric, derive the duration from the uploaded
    `file_bytes` via `derive_duration_seconds`; if it returns a positive float, bill THAT
    (`quantity = Decimal(str(derived))`) — closes the silent-$0 leak for decodable audio.
  - `derive_duration_seconds(data: bytes) -> float | None`: returns tinytag's `.duration` as a float when
    it is a finite POSITIVE number; returns None for any undecodable / unsupported / corrupt / zero /
    None / non-finite case. NEVER raises (wraps tinytag in try/except).
  - The transcription itself ALWAYS ships (status + body unchanged); duration derivation only affects the
    billed `quantity`, never the response or success.
  - Exactly ONE usage record per transcription (single-bill invariant preserved); derivation adds no extra record.
Reject:
<reject>
  - upstream duration absent AND header undecodable / unsupported format (incl. the AU2b truncated WAV,
    webm, empty, garbage) -> derived None -> quantity = Decimal("0") + WARN `stt_duration_unavailable`
    (today's $0 fallback, now logged) — request still 200. (Preserves the FROZEN AU2b assertion.)
  - tinytag raises on a malformed/short header -> caught -> None -> $0 fallback (never propagates).
  - a negative / zero / NaN / inf duration from tinytag -> treated as undecodable -> None -> $0 fallback
    (never bill a non-positive or non-finite second-count).
  - filename / content_type claim a format the bytes are not -> derivation keys on the BYTES (tinytag
    sniffs content), not the lying header.
</reject>
After:
<after>
  - A transcription submitted WITHOUT verbose_json, with a decodable audio file, bills a non-zero
    `per_second` quantity equal to the file's true duration (to the recorder's numeric precision).
  - A transcription WITH verbose_json bills exactly as before (upstream duration, byte-identical).
  - An undecodable upload bills $0 with a `stt_duration_unavailable` WARN and still returns the transcript.
  - `tinytag` is a declared runtime dependency on the allow-list; no native/heavy dependency added.
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ tinytag's `.duration` is accurate ENOUGH to bill on for the real-world formats (esp. VBR MP3 / m4a) —
    lowest confidence because container-header duration can differ slightly from the exact decoded length
    for VBR without a Xing/Info header. If wrong: a small per-call over/under-bill on some VBR files (cents),
    never a correctness HARD-STOP; mitigated because upstream `duration` (when present) always wins, and the
    bill is "best-effort-exact with a recorded fallback" per the milestone's accuracy-not-availability rule.
  - [ ] tinytag 2.2.1 reads duration from an in-memory `BytesIO` via `TinyTag.get(file_obj=..., tags=False,
    image=False)` — CONFIRMED at ground (synthetic 1s WAV → 1.0; truncated/empty/garbage → None/raise).
  - [ ] Deriving on the request path (in-memory bytes already read) is cheap enough — CONFIRMED bounded by
    the already-loaded file size; header-only read for most formats; billing itself stays fire-and-forget.
  - [ ] No ledger schema change / no duration-source column — observability via the WARN log + the existing
    raw payload (which holds the upstream body). Confirmed: milestone scopes t3 to the COUNT source, not schema.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: no verbose_json, decodable file -> derived non-zero per_second bill
  Given an STT request whose upstream body has NO duration
  And the uploaded file is a valid 3-second WAV
  When the transcription is billed
  Then exactly one usage record fires with pricing_unit="per_second" and quantity == Decimal("3.0")
  And the response is 200 with the upstream body unchanged

Scenario: verbose_json present -> upstream duration wins, byte-identical
  Given an STT request whose upstream body reports duration = 12.5
  And the uploaded file is a valid 3-second WAV (a different length)
  When the transcription is billed
  Then quantity == Decimal("12.5") (upstream wins; derivation is NOT consulted)
  And the response is 200

Scenario: no verbose_json, undecodable header -> $0 fallback + warn (AU2b preserved)
  Given an STT request whose upstream body has NO duration
  And the uploaded file is the truncated bytes b"RIFF\x00\x00\x00\x00WAVEfmt "
  When the transcription is billed
  Then quantity == Decimal("0")
  And a stt_duration_unavailable warning is logged
  And the response is still 200 (transcript ships)

Scenario: derive_duration_seconds is fail-safe over bad input
  Given assorted byte inputs (empty, garbage, truncated, a valid WAV)
  When derive_duration_seconds is called on each
  Then the valid WAV returns its positive float duration
  And empty/garbage/truncated all return None and NONE raise

Scenario: non-positive / non-finite tinytag duration -> None
  Given a file tinytag would report with duration 0 / negative / NaN / inf
  When derive_duration_seconds is called
  Then it returns None (never bills a non-positive or non-finite second-count)

Scenario: tinytag is a declared, allow-listed dependency
  Given the project dependency manifest and the import allow-list
  When the allow-list check runs
  Then tinytag is present in both (no heavy/native dependency added)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal billing-count seam on the existing STT path. No HTTP-contract change, no schema change.

## New pure decoder (proxy/application/audio_duration.py)
derive_duration_seconds(data: bytes) -> float | None
  - tag = TinyTag.get(file_obj=BytesIO(data), tags=False, image=False)   # wrapped in try/except
  - any Exception (UnsupportedFormatError, parse error, …) -> None
  - d = tag.duration; valid iff isinstance(d,(int,float)) and not bool and math.isfinite(d) and d > 0
  - valid   -> float(d)
  - else    -> None
  - NEVER raises.

## TranscriptionUseCase.execute — duration resolution (replaces the line-189 single read)
  raw = resp_body.get("duration")
  if isinstance(raw,(int,float)) and not isinstance(raw,bool) and math.isfinite(raw) and raw > 0:
      duration_s = float(raw)                       # upstream authoritative (verbose_json), finite-guarded
  else:
      derived = derive_duration_seconds(file_bytes) # server-side header derivation
      if derived is not None:
          duration_s = derived                      # closes the leak
      else:
          duration_s = 0.0
          _log.warning("stt_duration_unavailable", extra={"model": model_id, ...})   # documented $0 fallback
  # Step 8 unchanged: _fire_record_with_raw(..., pricing_unit="per_second", quantity=Decimal(str(duration_s)))

## Dependency
  pyproject [project].dependencies += "tinytag>=2.2,<3"   # pure-Python, no native deps
  scripts/check_allowlist.py allow-list += tinytag        # import-gate entry

## Invariants (frozen)
  - Upstream-reported duration ALWAYS wins when FINITE and positive — verbose_json path byte-identical
    (AU2 = 12.5). A non-finite upstream duration (inf/nan) is NOT authoritative: it falls through to
    derivation, mirroring the decoder's isfinite guard (prevents a Decimal("Infinity") bill — refute Finding 1).
  - Undecodable header -> Decimal("0") + WARN, request still 200 — the FROZEN AU2b assertion is preserved
    (its truncated WAV decodes to None). No audio_endpoints test is touched.
  - Exactly one usage record per transcription (single-bill). No new ledger column, no migration.
  - derive_duration_seconds is pure + total (never raises); content-sniffed, filename ignored.
  - DEFERRED (refute Finding 2, observe follow-up): no UPPER magnitude cap on the billed duration — a
    lying/corrupt header (huge declared data chunk) can over-derive. Harm is tenant-self-inflicted; a
    sane ceiling needs a product max, so it is logged as a follow-up delta, not built here.
```

Status: FROZEN @ v2 — change-request approved by Tin Dang 2026-06-17 (add math.isfinite to the upstream
guard; refute-read Finding 1). v1: FROZEN @ 2026-06-17 (dependency: tinytag, chosen via AskUserQuestion).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the new decoder + the resolution branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - SD1 test_no_verbose_decodable_file_bills_derived_duration: POST STT, upstream body NO duration, upload a
    synthetic valid 3s WAV / spy recorder / quantity==Decimal("3.0"), pricing_unit per_second, status 200
  - SD2 test_verbose_json_upstream_duration_wins: upstream duration=12.5, upload a 3s WAV / quantity==Decimal("12.5")
    (derivation NOT consulted), status 200
  - SD3 test_undecodable_header_zero_fallback_with_warn: upstream NO duration, upload truncated AU2b bytes /
    quantity==Decimal("0"), caplog has "stt_duration_unavailable", status 200
  - SD4 test_derive_duration_seconds_failsafe_table: unit table over derive_duration_seconds — valid WAV→3.0,
    empty→None, garbage→None, truncated→None; assert NONE raise
  - SD5 test_non_positive_or_nonfinite_duration_is_none: monkeypatch tinytag to report 0 / -1 / nan / inf →
    derive_duration_seconds returns None each
  - SD6 test_tinytag_is_declared_and_allowlisted: assert "tinytag" in pyproject [project].dependencies AND in
    the allow-list (import gate) — pins the dependency contract
  - SD7 test_single_bill_invariant: decodable derivation still fires EXACTLY one usage record
</test_plan>

Tests live in: `apps/gateway/tests/stt_duration_derivation/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/audio_duration.py` `apps/gateway/src/gateway/proxy/application/audio_use_case.py` `apps/gateway/pyproject.toml` `apps/gateway/scripts/check_allowlist.py` `apps/gateway/tests/stt_duration_derivation/`
Strategy (ordered batches):
  1. Add tinytag to pyproject [project].dependencies + the check_allowlist.py allow-list; `uv sync`.
  2. New audio_duration.py: pure derive_duration_seconds(bytes) -> float | None (tinytag BytesIO, try/except,
     finite-positive guard).
  3. audio_use_case.py: add module logger; replace the line-189 duration read with the prefer-upstream →
     derive → $0+WARN resolution. No other step changes (single-bill, status/body untouched).
Safety rule (feature-specific): upstream positive duration ALWAYS wins (verbose_json path byte-identical);
  derivation only fills the absent case; derive_duration_seconds never raises so the request always ships.
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

- [x] all tests pass — full gateway suite **1162 passed**, 19 deselected, exit 0 (t3 SD1–SD8 + frozen audio_endpoints AU2/AU2b green).
- [x] coverage did not decrease — 86% total (≥80% gate; unchanged from t2's 86%). New decoder + resolution branch fully exercised (SD1/SD3/SD4/SD5/SD7/SD8).
- [x] no test or contract was altered during build — the `math.isfinite` upstream guard was a Tin-approved CHANGE-REQUEST (re-froze §3 @ v2; new test SD8 added in the tests phase, RED→GREEN), NOT a build-time edit. Build touched only `src/` (audio_duration.py, audio_use_case.py) + pyproject + allowlist.
- [x] the green was EARNED — adversarial refute-read (sonnet, 9 attack points) = **EARNED-GREEN-WITH-NITS**, 0 regressions, no frozen test touched, every invariant confirmed. 2 nits fixed (stale docstrings; redundant `pytestmark` → killed 9 async warnings); Finding 1 (inf) HARDENED + pinned by SD8; Finding 2 (no magnitude cap) DEFERRED as a follow-up delta (§7).
- [x] concurrency / timing safe — derivation is a synchronous pure decode on the already-read `file_bytes`; no new IO, no new task. Single fire-and-forget usage record preserved (SD7).
- [x] no exposed secrets, injection openings, or unexpected dependencies — tinytag is pure-Python, allow-listed (`check_allowlist: OK`); decode is read-only over in-memory bytes (no shell/exec/file-write); filename ignored (content-sniffed).
- [x] layering & dependencies follow CONVENTIONS.md — new pure module under `proxy/application`; the use case depends inward on it; no cross-layer leak.
- [x] a person reviewed and approved the change — Tin Dang approved the §3 contract (v1) AND the inf-hardening change-request (v2) via AskUserQuestion (2026-06-17); gate auto-recorded under `autonomy: auto` on complete evidence.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `derive_duration_seconds` is imported + called in `TranscriptionUseCase.execute` step 7 (else-branch); `_log` + `math.isfinite` referenced; all confirmed live by SD1 (derive path), SD3 (warn path), SD8 (isfinite path).
- [x] DEAD-CODE (code) — the old `float(resp_body.get("duration") or 0.0)` line is gone; no orphaned symbol; tinytag import used only in the decoder.
- [x] SEMANTIC — n/a (code change).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract v1 + inf-harden change-request v2, via AskUserQuestion) · date: 2026-06-18
Note: §3/§5 name `scripts/check_allowlist.py` as the allow-list; the data file it reads is
`.add/dependencies.allowlist` (where `tinytag` was added). Same allow-list gate, satisfied; SD6 pins both.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `stt_duration_unavailable` WARN rate (undecodable uploads); the
share of no-`verbose_json` STT calls now billing a non-zero `per_second` quantity (the leak-closure signal).
Spec delta for the next loop: two deferred follow-ups (below) — a duration magnitude cap and inf/nan
response-passthrough — both about bounding/normalizing a non-finite-or-absurd duration from EITHER side.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] An inf-via-HTTP test is confounded: Starlette renders the echoed body with `allow_nan=False`,
  so the response RAISES before any status assert. Pin the LEDGER instead — `pytest.raises(ValueError)` on the
  call + poll the `asyncio.ensure_future` usage-record spy for the billed `quantity`. (evidence: SD8 RED showed
  `Decimal('Infinity')` in the spy, GREEN after the guard.)
- [SDD · open] A frozen contract can carry a guard ASYMMETRY: the decoder spec had `math.isfinite`, the
  upstream-branch spec did not → an inf upstream billed `Decimal('Infinity')`. The refute-read (Finding 1)
  caught it; the fix was a CHANGE-REQUEST re-freezing §3 @ v2, not a silent edit. Mirror an invariant across
  every sibling code path when freezing.
- [ADD · open] The verify-gate adversarial refute-read paid off again: 2 real findings on a fully-green build
  (isfinite gap + no over-bill cap). EARNED-GREEN ≠ flawless. (evidence: refute-read on this task.)
- [ADD · open] FOLLOW-UP (Finding 2, deferred by Tin): no UPPER magnitude cap on the billed duration — a
  lying/corrupt audio header (huge declared `data` chunk) over-derives; tinytag trusts the header. Harm is
  tenant-self-inflicted; a sane ceiling needs a product-chosen max. Not built here; revisit as a change-request.
- [ADD · open] FOLLOW-UP (separate, pre-existing): an inf/nan `duration` in the upstream STT body still 500s
  on response serialization (allow_nan=False), independent of billing. Response-passthrough robustness, out of
  this billing task's scope; candidate to sanitize non-finite floats before echoing the upstream body.
