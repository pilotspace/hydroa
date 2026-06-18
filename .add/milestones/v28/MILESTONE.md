# MILESTONE: Billing & passthrough robustness

goal: every streamed or transcription call that consumed real upstream work is billed or explicitly flagged — no remaining silent $0, and no non-finite value can enter the ledger or the response body
rationale: new-major (Tin, 2026-06-18 via AskUserQuestion: "Billing follow-ups first" → "Approve all 3 tasks"). v27 made every *normal* proxied call billed at the provider's true per-tier cost; this milestone closes the three edge cases v27 explicitly DEFERRED to its OBSERVE residue — the residual silent-$0 on stream/disconnect and the non-finite-value hardening on the STT path. Took the v28 slot ahead of the UI↔BE coverage program (renumbered v28→v29) so the dashboard surfaces land on a fully-correct billing base. Serves the standing production goal's "accurate, billable cost tracking" half.
stage: production · status: active · created: 2026-06-18

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  flag/bill a mid-stream client DISCONNECT (GeneratorExit before the terminal usage frame) so it is
     never an unexplained silent $0; clamp a derived STT duration to a sane configured maximum so a
     corrupt/lying audio header cannot over-bill; sanitize non-finite (inf/nan) floats in an upstream STT
     response body so a transcription returns a valid 200 instead of a 500 on serialization.
Out: the UI↔BE dashboard-coverage program (renumbered to v29); ANY content-derived token *estimate* for an
     unbilled stream (v27 rejected heuristic token math in the money path); new providers or modalities;
     retroactive re-billing of historical rows; changes to the exact Decimal arithmetic or the cost column.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Accuracy is never an availability gate** (v12/v27, preserved): a disconnect, a corrupt audio header, or
  a non-finite upstream value DEGRADES to a documented, flagged, bounded fallback — it never fails or retries
  the request (the bytes/transcription already reached, or will reach, the client).
- **Every $0 is EXPLAINED** (v27, preserved): a $0 stream row always carries a `usage_source` that names why
  (`frame` | `stream_fallback` | the new disconnect marker) — never an unexplained zero.
- **The `usage_source` typed-extras seam** (v27 `stream-usage-completeness`): a new stream-billing marker
  rides the existing `UsageRecordExtras` → recorder event → flusher → `usage_records.usage_source` column;
  the disconnect task reuses this seam (no new migration unless a distinct marker value needs one — it does not).
- Glossary deltas (new terms): **client-disconnect (billing)** · **duration cap** · **non-finite sanitization**.

## Shared / risky contracts (freeze these first)
- **Disconnect billing policy** (flag-$0 with a distinct `usage_source` value vs counting the partial
  `collected` SSE content) -> owning task `stream-disconnect-billing`. This is the one genuine contract
  decision in the milestone; the two STT tasks are independent and carry no shared seam.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] stream-disconnect-billing   depends-on: none — a mid-stream client disconnect raises GeneratorExit through `CompletionUseCase.stream._wrapped` BEFORE the post-stream record block, so the stream is currently billed $0 with NO marker. Fire exactly ONE flagged record on the disconnect path (`usage_source='client_disconnect'`, or fold into `stream_fallback`) so every $0 stream row is explained. Reuses the v27 usage_source seam.  ✓ gate=PASS (DC1–DC7 green; `usage_source='client_disconnect'`, complete frame still bills `frame`).
- [x] stt-duration-cap            depends-on: none — `derive_duration_seconds` trusts the audio header, so a corrupt/lying `data`-chunk size over-derives an absurd duration → over-bills `per_second`. Clamp the derived (and upstream-reported) duration to a product-chosen configurable maximum + WARN on clamp; a normal-length file is byte-identical.  ✓ gate=PASS (DCAP1–DCAP7 green; GATEWAY_STT_MAX_DURATION_SECONDS default 14400s/4h, clamp+`stt_duration_capped` WARN).
- [x] stt-nonfinite-passthrough   depends-on: none — an inf/nan `duration` (or any non-finite float) in the upstream STT response body still 500s on response serialization (`allow_nan=False`), independent of billing. Sanitize non-finite floats before echoing the upstream body so the transcription returns a valid 200. Response-passthrough robustness, not the money path.  ✓ gate=PASS (NF1–NF6 + v27 sd8 green; pure `sanitize_non_finite` → null replacement at Step 8b after the single billing fire, `stt_nonfinite_sanitized` WARN; closes v27 t3's deferred passthrough-500).

## Exit criteria (observable; map each to the task that delivers it)
- [x] A client that disconnects mid-stream still produces EXACTLY ONE ledger row, flagged with a disconnect `usage_source`, never an unexplained silent $0.   (verify: pytest apps/gateway/tests/stream_disconnect_billing)   (← stream-disconnect-billing)
- [x] An STT call whose audio header declares an absurd duration is billed at most the configured cap, not the header's value (and a normal file is unchanged).   (verify: pytest apps/gateway/tests/stt_duration_cap)   (← stt-duration-cap)
- [x] An STT upstream response carrying an inf/nan duration returns a valid 200 JSON body (non-finite sanitized), not a 500.   (verify: pytest apps/gateway/tests/stt_nonfinite_passthrough)   (← stt-nonfinite-passthrough)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/proxy : the three deferred-edge fixes. (1) `CompletionUseCase.stream._wrapped` now fires ONE flagged record on `GeneratorExit`/`CancelledError` (`usage_source='client_disconnect'`, or `frame` if the terminal usage frame already arrived). (2) `TranscriptionUseCase` clamps the billable duration to `GATEWAY_STT_MAX_DURATION_SECONDS` (default 14400s/4h) + `stt_duration_capped` WARN. (3) NEW pure `json_sanitize.sanitize_non_finite` wired at Step 8b of `TranscriptionUseCase.execute` → null-replaces inf/nan in the echoed body + `stt_nonfinite_sanitized` WARN. One new migration (b8e4f1a7c2d5, v27 — reused, no new column this milestone); one new config knob; `use_cases.py`/`audio_router.py` byte-identical.
- tooling : untouched.
- skill   : untouched.
- book    : untouched.

### Cross-task evidence   (one row per task)
- stream-disconnect-billing : gate=PASS · tests=DC1–DC7 green · residue=none (follow-up SPEC·open: a non-GeneratorExit silent close path is still theoretical — covered by the disconnect handler but not separately tested).
- stt-duration-cap          : gate=PASS · tests=DCAP1–DCAP7 green (WARN payload `.original`/`.cap`/`.model` asserted) · residue=none.
- stt-nonfinite-passthrough : gate=PASS · tests=NF1–NF6 + v27 sd8 green (full suite 1186 green, excl tests/edge live-stack) · residue=none (SPEC·open: sibling passthrough routers images/embeddings/proxy share the allow_nan=False risk — separate task).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): criterion 1 ← stream-disconnect-billing (DC1–DC7); criterion 2 ← stt-duration-cap (DCAP1–DCAP7); criterion 3 ← stt-nonfinite-passthrough (NF1–NF6 + sd8).
- goal: every streamed or transcription call that consumed real upstream work is billed or explicitly flagged (no silent $0), and no non-finite value can enter the ledger or the response body. Proof: a mid-stream disconnect now records exactly one `client_disconnect`-flagged row; a non-finite upstream duration falls through to a derived/clamped finite ledger quantity (sd8) AND is null-sanitized out of the echoed body (NF1/NF4) — the ledger and the response are both non-finite-free, on a full 1186-green suite.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
