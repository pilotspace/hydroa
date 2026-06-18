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
- [ ] stt-duration-cap            depends-on: none — `derive_duration_seconds` trusts the audio header, so a corrupt/lying `data`-chunk size over-derives an absurd duration → over-bills `per_second`. Clamp the derived (and upstream-reported) duration to a product-chosen configurable maximum + WARN on clamp; a normal-length file is byte-identical.
- [ ] stt-nonfinite-passthrough   depends-on: none — an inf/nan `duration` (or any non-finite float) in the upstream STT response body still 500s on response serialization (`allow_nan=False`), independent of billing. Sanitize non-finite floats before echoing the upstream body so the transcription returns a valid 200. Response-passthrough robustness, not the money path.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A client that disconnects mid-stream still produces EXACTLY ONE ledger row, flagged with a disconnect `usage_source`, never an unexplained silent $0.   (verify: pytest apps/gateway/tests/stream_disconnect_billing)   (← stream-disconnect-billing)
- [ ] An STT call whose audio header declares an absurd duration is billed at most the configured cap, not the header's value (and a normal file is unchanged).   (verify: pytest apps/gateway/tests/stt_duration_cap)   (← stt-duration-cap)
- [ ] An STT upstream response carrying an inf/nan duration returns a valid 200 JSON body (non-finite sanitized), not a 500.   (verify: pytest apps/gateway/tests/stt_nonfinite_passthrough)   (← stt-nonfinite-passthrough)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
