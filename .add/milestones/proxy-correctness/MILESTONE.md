# MILESTONE: Proxy Correctness

goal: Every gateway provider adapter maps provider responses to the OpenAI-compatible wire shape faithfully per the providers' published API docs — closing the real docs-vs-code deltas found by the adapter audit (finish_reason/stop_reason completeness, Anthropic in-stream error surfacing, OpenAI STT passthrough), with the Bedrock SigV4 'CRITICAL' recorded as a verified false-positive.
rationale: new-major — closes the standing "make sure all LLM proxy are correct as docs" directive. A read-only audit (5 parallel agents, one per adapter) compared each adapter's wire mapping to the provider's published API. Result: NO real blocker (the Bedrock "CRITICAL 403" claim was REFUTED — see Shared decisions), plus a cluster of genuine LOW/MED faithfulness deltas. This milestone fixes the real deltas via additive TDD on the gateway and records the refuted finding so it is never "fixed" wrongly.
stage: mvp · status: active · created: 2026-06-30T12:19:24+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Additive response-mapping / passthrough fixes in the gateway provider adapters so they faithfully translate provider responses to the OpenAI-compatible wire shape: (1) finish_reason/stop_reason completeness — Gemini content-policy finishReasons → content_filter, Bedrock model_context_window_exceeded → length, Anthropic refusal → content_filter; (2) Anthropic mid-stream `event: error` surfaced as a terminal error frame (reliability, mirrors the v35 agent-loop error-fidelity contract); (3) OpenAI STT forwards timestamp_granularities + chunking_strategy. All TDD (red-first), no new dependency, no migration.
Out: the Bedrock SigV4 service name (VERIFIED CORRECT — do NOT change, see Shared decisions); OpenAI stream_options.include_usage injection (already handled by the v27 usage_source=stream_fallback path — recorded as a delta, not changed here); OpenRouter usage:{include} staleness + HTTP-Referer/X-Title headers + native_tokens_reasoning/_completion_images capture (NITs, unused → deltas); Anthropic thinking_delta passthrough + message_delta input_tokens re-read (NIT/LOW → deltas); new provider features / new endpoints; any auth/security/billing change.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Bedrock SigV4 "CRITICAL" is a REFUTED false-positive.** The audit claimed `service="bedrock"` should be `"bedrock-runtime"` (alleged 403 on every call). REFUTED: AWS Bedrock Runtime's SigV4 *signing name* genuinely is "bedrock" (host prefix bedrock-runtime ≠ signing name; botocore's bedrock-runtime model has signingName=bedrock). The repo `tests/bedrock_sigv4/test_bedrock_sigv4.py` pins the signer byte-for-byte against AWS-published get-vanilla vectors (SV0). bedrock_sigv4.py SERVICE stays "bedrock" — un-touchable in this milestone.
- **Additive only.** Never weaken/delete an existing assertion; a real behavior change gets a NEW test (red-first). No new dependency, no migration, no security surface.
- **Mirror existing contracts.** The Anthropic in-stream error fix reuses the gateway's existing terminal-error-frame shape (v35), not a new error contract.

## Shared / risky contracts (freeze these first)
- **Provider finish_reason/stop_reason → OpenAI finish_reason mapping** (Gemini/Bedrock/Anthropic safety+length values) -> owning task `adapter-correctness-fixes`.
- **Streaming terminal-error frame on a mid-stream Anthropic `event: error`** (must match the existing v35 terminal-error contract, no hang/truncation) -> owning task `adapter-correctness-fixes`.
- **OpenAI STT optional-field passthrough allowlist** (`_STT_PASSTHROUGH_FIELDS`) -> owning task `adapter-correctness-fixes`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] adapter-correctness-fixes   depends-on: none   — the three additive TDD fixes (finish_reason completeness · Anthropic in-stream error → terminal frame · OpenAI STT passthrough); Bedrock SigV4 left untouched. (Built by a worktree-isolated backend agent off main.)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] Gemini content-policy finishReasons, Bedrock model_context_window_exceeded, and Anthropic refusal map to the correct OpenAI finish_reason (content_filter / length), each pinned by a test   (← adapter-correctness-fixes)
- [ ] An Anthropic mid-stream error event surfaces to the client as a terminal error frame (no silent "stop", no hang), pinned by a streaming test   (← adapter-correctness-fixes)
- [ ] OpenAI /v1/audio/transcriptions forwards timestamp_granularities + chunking_strategy upstream, pinned by a test   (← adapter-correctness-fixes)
- [ ] The Bedrock SigV4 "CRITICAL" is recorded as a verified false-positive and the signer is unchanged   (← adapter-correctness-fixes / this milestone doc)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (product) : <provider adapters — what shipped>
- tooling / skill / book : <untouched unless noted>

### Cross-task evidence   (one row per task)
- adapter-correctness-fixes : gate=<PASS> · tests=<n green> · residue=<deltas recorded>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate + the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Open a SEPARATE PR (gateway domain) from the proxy-correctness worktree branch → main; Tin reviews + merges. (NOT bundled with the dashboard playground PR.)
- [ ] No migration (additive response-mapping) — rides the normal gateway release.
- [ ] Record the remaining audit NITs (OpenRouter headers/usage staleness, Anthropic thinking/input_tokens, OpenAI stream_options) as §7 deltas for a later pass.
