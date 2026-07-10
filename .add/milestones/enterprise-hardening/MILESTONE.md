# MILESTONE: Enterprise Hardening

goal: Every confirmed blocking defect in the 2026-07-02 enterprise-readiness diagnostic — revenue-integrity, resilience, realtime governance, and security — is fixed, tested red→green, and verified.
rationale: new-major (a new theme — "harden the AI gateway to enterprise-grade + monetization-integrity" — that no active milestone covers). Bundles what ADD would normally split into a roadmap (revenue · durability · governance · security · pricing are each milestone-sized lines); kept as ONE milestone per Tin's explicit "draft all into one milestone, implement in parallel + auto." Per Tin's 2026-07-02 scope choice, ONE commercial task ships here too: tiered/per-model rate cards (the monetization lever, internal-only). Deferred commercial GAPs (invoicing/dunning — needs Stripe/external infra; margin reporting; budget reserve-then-settle) go to a future queued `monetization-commercial` milestone. B1 (stream-alias→$0) already shipped as fast task `stream-alias-billing` (PR #53, awaiting merge — this milestone's fork base); this milestone closes the rest.
stage: mvp · status: active · created: 2026-07-02T04:42:17+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Fix the confirmed diagnostic defects — B6 cache-hit alias→$0 (revenue) · B3 per-provider circuit breakers (resilience) · B4+B5 usage-flusher durability (Redis-failure billing loss) · B2 realtime-relay governance + usage/audit · S1 signup invite-only + routing-write authz split · S2/S3/S4 edge input hardening (XFF/SSRF/body-size) — PLUS one monetization feature: tiered/per-model rate cards (replaces the flat per-tenant markup_pct). Each ships a frozen contract + red test + verify gate.
Out: Deferred commercial GAPs — invoicing/dunning (Stripe — external infra), margin/revenue reporting, atomic budget reserve-then-settle (H10) → future `monetization-commercial` milestone (ASK before building). Also OUT: the lower-severity clean-mirror defects (H1/H5/H7/H8/M2-M8 etc.) — folded opportunistically only if a task already touches that file, never as scope.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Billing truth = append-only `usage_records`; never bill an alias.** An alias has no `pricing_snapshots` row → cost resolves to $0. Every charged (status=200) record keys on the SERVED candidate, captured never recomputed (extends frozen F7 / the B1 invariant).
- **Design for failure on every IO seam** (CLAUDE.md): explicit timeout, bounded retry, circuit-breaker, and a durable/rollback fallback — no unbounded or fail-silent IO.
- **Security tasks HARD-STOP at verify** — never auto-passed. `signup-and-routing-authz` + `edge-input-hardening` force a human verify gate even under `autonomy: auto`. S1-S4 fixes were pre-authorized by Tin (invite-only signup default; routing-write behind an ops permission) — that clears the *build* HARD-STOP, not the *verify* review.
- **Change-requests re-open Specify** — B3/B4 change documented invariants ("single app-wide breaker", "recorder never raises into the proxy path"); B2 is the flagged governance ASK. Each is specified from scratch, human-approved at its own freeze — never a silent behavior change.
- **Parallel BUILD, serial INTEGRATION** (streams.md): workers fork from the post-B1 base; the orchestrator owns ALL shared writes (this file, state.json, every `advance`/`gate <slug>`); a worker owns only its `.add/tasks/<slug>/` + its src/tests; merge back only src/tests/its-own-task-dir.

## Shared / risky contracts (freeze these first)
- served-candidate-billing on the cache-hit path (extends B1/F7) -> owning task `cache-alias-billing`
- per-provider circuit-breaker resilience contract (changes single-breaker invariant; frozen `streaming_resilience` tests) -> `provider-circuit-breakers`
- usage-recorder durability invariant (durable fallback + Redis timeout + PEL reclaim; changes "never raise into proxy path") -> `usage-flusher-durability`
- realtime-relay governance + usage/audit contract -> `realtime-relay-governance`
- edge input-validation limits (XFF last-hop parse · SSRF deny 169.254/16 + fe80::/10 · request body-size cap) -> `edge-input-hardening`
- signup-mode default + routing-write authz split (invite-only; ops permission) -> `signup-and-routing-authz`
- tiered rate-card pricing model (per-model + per-tier markup; replaces scalar markup_pct; billing + catalog both consume it) -> `tiered-rate-cards`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] cache-alias-billing        depends-on: none                 — B6: cache-HIT through an alias bills the served candidate, not the alias ($0 leak). `use_cases.complete()` cache path; capture served id before the early `_fire_record_cached` return. (revenue; near-mirror of B1)
- [ ] usage-flusher-durability   depends-on: none                 — B4+B5: usage events survive a Redis blip (durable fallback + bounded Redis timeout) and a crash mid-flush (XAUTOCLAIM PEL reclaim). `usage/application/{recorder,flusher}.py`. (durability; change-request)
- [ ] realtime-relay-governance  depends-on: none                 — B2: `/v1/realtime/relay` enforces governance (authz/rate-limit) and emits usage + audit rows like every other route. `proxy/{api,application,domain}` realtime modules. (governance; ASK — Tin approves relay scope at freeze)
- [ ] edge-input-hardening       depends-on: none                 — S2+S3+S4: XFF last-hop parse (no rate-limit spoof) · SSRF allow-list denies IMDS ranges · request body-size cap. agent_oauth routers · `oidc_admin_router` · `concurrency_guard`. (security; HARD-STOP verify)
- [ ] signup-and-routing-authz   depends-on: none                 — S1: signup invite-only by default · routing-config write behind an ops permission (not any OWNER). `tenants/api/*` · `proxy/api/routing_admin_router` + routing-config infra. (security; HARD-STOP verify)
- [ ] provider-circuit-breakers  depends-on: cache-alias-billing  — B3: per-provider breakers so one provider outage doesn't 502 all alias chat; `complete()` catches CircuitOpenError (sibling of UpstreamUnavailableError) and falls over. `use_cases.py` + `fallback_router.py` + `streaming_resilience.py` + `deps.py`. (resilience; serialized after B6 — shared `use_cases.py`)
- [ ] tiered-rate-cards          depends-on: usage-flusher-durability  — Monetization: replace scalar `markup_pct` with per-model + per-tier rate cards; billing (`usage/recorder.py`, `cost_recovery.py`) + catalog (`catalog/*`) + `tenants/orm.py` + admin API + migration. (feature; serialized after B4/B5 — shared `recorder.py`/`cost_recovery.py`)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A cache-HIT chat request through a model-group alias records usage on the served candidate (non-zero cost), never the alias   (← cache-alias-billing)
- [ ] A Redis outage during flush does not permanently drop billing, and a crash mid-flush leaves no usage event stranded forever   (← usage-flusher-durability)
- [ ] A realtime-relay session is authorized + rate-limited and produces a usage row and an audit row   (← realtime-relay-governance)
- [ ] A spoofed `X-Forwarded-For` cannot defeat per-IP OAuth rate limits; an SSRF to 169.254.0.0/16 or fe80::/10 is blocked; an oversized request body is rejected with a bounded error   (← edge-input-hardening)
- [ ] A newly signed-up anonymous user cannot become OWNER and overwrite routing config; signup is invite-only by default; routing-write requires the ops permission   (← signup-and-routing-authz)
- [ ] One provider's outage fails over to a sibling for alias chat instead of 502-ing all traffic; `complete()` survives CircuitOpenError   (← provider-circuit-breakers)
- [ ] An admin can set per-model / per-tier markup (a rate card); a billed request charges that model's rate, not a single flat percentage   (← tiered-rate-cards)

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
- [ ] Open one PR per task branch (or a stacked series) from the Close ship-review above; Tin reviews + merges each
- [ ] Confirm PR #53 (B1 stream-alias-billing) merged first — this milestone's fork base
- [ ] Full `make ci` green on the integrated branch once the shared test Postgres is healthy
- [ ] Bundle into the next release cut (release.md) with milestone attribution; Tin tags / deploys
