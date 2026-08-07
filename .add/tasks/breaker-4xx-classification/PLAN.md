# PLAN: A 4xx never trips the circuit breaker — collapse the stragglers onto RetryPolicy

slug: breaker-4xx-classification · created: 2026-08-07 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A client error (4xx that is not 408 or 429) never counts toward a circuit
breaker and is never retried. Today a tenant who revokes or rotates a BYOK key eats five
401s, trips their OWN breaker, and then their CORRECTED traffic 502s for the cooldown.

Framings weighed:
- **Collapse the stragglers onto the rule the repo already has** (chosen). `RetryPolicy.
  classify_status` in `proxy/infrastructure/upstream_retry.py` ALREADY encodes exactly the
  right policy — 429/408/5xx count, every other 4xx is terminal-but-successful — and every
  provider routed through `execute_with_retry` already behaves correctly. The bug lives
  only in the four paths that BYPASS that seam and hand-roll
  `except httpx.HTTPStatusError -> on_upstream_error()`. So this is not "invent a 4xx
  rule"; it is "stop having two." Same move as the R5 heal that collapsed three `FOR
  UPDATE` copies onto one shared primitive.
- Invent a fresh predicate per call site — rejected: that is how the repo got two rules in
  the first place, and the next provider would make it three.
- Route the offenders through `execute_with_retry` itself — rejected as too big for this
  task. That executor is complete()-shaped (render callback, metrics vocabulary, deadline)
  and the offenders are a fine-tune poller, an embeddings batch and two Bedrock paths.
  Forcing them through it is a refactor with its own risk; sharing the CLASSIFICATION is
  the part that fixes the bug.
- Do nothing until R7 — rejected by Tin at the 2026-08-07 interview: it is a live
  availability defect, not residue.

Must:
<must>
  - M1 a 4xx that is not 408/429 does NOT count toward the breaker, on every path that
    currently mis-counts one: finetune submit · finetune poll · finetune cancel ·
    vector-store embeddings · bedrock embeddings · bedrock streaming
  - M2 5xx, 408, 429 and transport failures STILL count — the breaker keeps protecting
    against real upstream outages; this task must not weaken it
  - M3 a 4xx is not RETRIED — a 404 from the fine-tune poller must cost one call, not three
  - M4 the breaker predicate and `RetryPolicy.classify_status` agree for EVERY status in
    100..599 — one rule, provably, not two that drift
  - M5 the caller still sees the same outcome for a 4xx as it does today (same exception
    type / same status+body) — this task changes breaker ACCOUNTING, not the response
</must>
Reject:
<reject>
  - a 429 -> STILL counts toward the breaker. It is upstream backpressure, not a client
    mistake; treating it as benign would remove the one signal that a provider is shedding
    our load. Same for 408.
</reject>
After:
<after>
  - a tenant who rotates a BYOK key is not locked out of their own corrected traffic
  - todo #60 closed, including the two sites it does not name
</after>
Boundary: none — no new external input shape. The input space is the HTTP status set
(100..599) plus the transport-exception set already classified by `RetryPolicy`.
<assumptions>
  ⚠ That every one of these six sites WANTS the RetryPolicy semantics. Most likely wrong at
  `bedrock_upstream` streaming: a 4xx there is discovered after the stream is opened, and
  there may be a deliberate reason it was written `>= 400`. If wrong, the cost is a
  provider whose genuine error signal stops reaching the breaker — a protection gap, not a
  correctness bug — so the §4 suite gates each site SEPARATELY rather than asserting one
  global rule, and `git log -L` on the Bedrock lines is a direction-time check before the
  freeze.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding (read in-tree 2026-08-07, each site opened — not grepped):
- `proxy/infrastructure/upstream_retry.py::RetryPolicy.classify_status` — the rule that is
  already right: `429 -> "upstream_429"`, `408 -> "upstream_408"`, `>=500 -> "upstream_5xx"`,
  everything else `None`. `execute_with_retry` treats `None` as
  `breaker.record_success(); return render_response(resp)` — i.e. a terminal 4xx is a
  SUCCESSFUL round trip as far as breaker health goes. That is the semantics to share.
- `proxy/infrastructure/circuit_breaker.py::CircuitBreaker` — `on_upstream_error()` /
  `record_success()` / `call_allowed()` / `guard()`. BOTH offender modules already import
  from this file, so putting the shared predicate here adds no new coupling across
  bounded contexts. (Checked: `finetune/infrastructure/openai_client.py:35` and
  `vector_stores/infrastructure/embedding_client.py:23` both import CircuitBreaker.)
- Offenders — `except (httpx.TransportError, httpx.HTTPStatusError): breaker.on_upstream_error()`
  after a `resp.raise_for_status()`, so ANY 4xx lands in the breaker:
  `finetune/infrastructure/openai_client.py` submit · poll · cancel;
  `vector_stores/infrastructure/embedding_client.py::VectorStoreEmbeddingClient.embed`.
  Both are ALREADY per-tenant (`_TenantBreakerRegistry`, healed in R5 D1), so the blast
  radius is self-DoS, not cross-tenant — a smaller claim than todo #60 makes, stated
  accurately.
- Offenders NOT named in todo #60, found by reading every `status_code >= 400` site:
  `proxy/infrastructure/bedrock_embeddings.py` (`>= 400 -> on_upstream_error()`) and
  `proxy/infrastructure/bedrock_upstream.py` STREAM path (same). Both use a per-instance
  `self._breaker`.
- NOT offenders, though they match the same grep — each `_render` maps an error BODY while
  the breaker decision happens inside `execute_with_retry`: `anthropic_upstream` (~941),
  `gemini_upstream` (~907 and ~1074), `vertex_upstream` (~252), `bedrock_upstream` (~679).
  Opened individually and cleared; recorded here so a future reader does not re-flag them.
- ARCHAEOLOGY on the two Bedrock sites (the §1 ⚠, resolved BEFORE the freeze rather than
  assumed). `git log -L` on both line ranges:
  * `bedrock_embeddings.py` came from `1c26a71` (v20 task 5). Its message explains the
    `>= 400` as *"a >=400 invoke fails fast (returns the OpenAI-shaped error envelope on
    the FIRST failure, no partial list)"* — Titan has no batch endpoint, so a list input
    fans out to N sequential calls and the guard exists to stop that LOOP early. The
    fail-fast is about the fan-out; `on_upstream_error()` rode along inside the same `if`
    with no justification of its own. NOT a deliberate "4xx should trip the breaker" call.
  * `bedrock_upstream.py` stream came from `f3f6304` (v20 task 3), whose message says it
    *"mirrors AnthropicCompletionUpstream"*. AnthropicCompletionUpstream's stream path uses
    `>= 500` for the breaker. So this site DIVERGES from the very thing it claims to copy —
    the strongest evidence available that the `>= 400` was a slip, not a decision.
  Both cleared for inclusion; the ⚠ is downgraded accordingly and the §4 suite still gates
  each site separately.
- Out of scope, flagged not fixed: `objectstore/s3.py` counts EVERY non-404 `ClientError`
  (so an S3 403 from bad credentials trips the breaker) and `email/.../smtp_email_sender.py`
  counts every failure DELIBERATELY, with a comment saying so. Different subsystems, own
  decisions — a follow-up todo, not silent scope creep.

```
proxy/infrastructure/circuit_breaker.py   (the shared rule, one home)

  def status_counts_as_upstream_failure(status: int) -> bool
        True  for 408, 429, and every status >= 500
        False for every other status, including 2xx/3xx and every terminal 4xx
        The bool twin of RetryPolicy.classify_status(status) is not None.
        Gated by a test that walks 100..599 and asserts the two agree — one rule
        in two shapes, provably in sync, rather than one delegating awkwardly.

Call-site shape at each of the six offenders (accounting only; response unchanged):

  except httpx.HTTPStatusError as exc:
      if status_counts_as_upstream_failure(exc.response.status_code):
          breaker.on_upstream_error()
      else:
          breaker.record_success()     # the round trip SUCCEEDED; the request was bad
      raise / return  <- byte-identical to today
  except httpx.TransportError:
      breaker.on_upstream_error()      # unchanged

  Retry loops (finetune poll/cancel, embeddings): a status that is NOT
  counts_as_upstream_failure BREAKS the loop instead of consuming attempts (M3).
```

Target (measurable): the §4 suite runs RED before build and GREEN after, with a SEPARATE
test per offender site (six sites, not one global assertion). `make ci` stays green at its
current bar (4531 passed, 0 failed). Two outcomes asserted directly rather than by proxy:
a breaker fed 10 consecutive 401s is still CLOSED, and a fine-tune poll of a 404 job issues
exactly ONE upstream request.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Strategy: predicate first (with the 100..599 agreement test), then one call site at a time,
each with its own red test, so a site that turns out to WANT the old behaviour can be
dropped without unpicking the others.

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py` · `apps/gateway/src/gateway/finetune/infrastructure/openai_client.py` · `apps/gateway/src/gateway/vector_stores/infrastructure/embedding_client.py` · `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py` · `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` · `apps/gateway/tests/breaker_4xx_classification/`
Regression floor: full `make ci` — the breaker sits on every provider path, and the
existing suites for finetune, vector_stores, bedrock and upstream_retry are the ones that
would catch a semantics change I did not intend.
Persona (optional): `backend-architect` — "one rule, one home; a shared primitive beats a
consistent convention."

DECIDED by Tin, 2026-08-07 interview: `record_success()` on a terminal 4xx — follow the
existing `execute_with_retry` seam rather than introduce a third breaker verb. The healing
side-effect is accepted as the cost of having ONE rule. M4's 100..599 agreement test
therefore stands as the guard that the two seams never diverge again.

Least-sure flag surfaced at freeze: [contract] — the `record_success()` on a terminal 4xx.
That is what `execute_with_retry` already does (`classify_status -> None ->
breaker.record_success()`), so it is consistent rather than invented, but it is a real
choice with teeth: a tenant sending a steady stream of 401s will actively HEAL a breaker
that a genuine outage had half-opened. The alternative — count it as neither success nor
failure — needs a third breaker verb this codebase does not have. I am following the
existing seam; flagging it because "bad requests keep the breaker healthy" is a sentence
worth someone disagreeing with before it is frozen.

(The §1 ⚠ — whether all six sites want this — was the original candidate for this flag and
was RESOLVED at direction time by the `git log -L` archaeology recorded in §3 grounding:
neither Bedrock `>= 400` was a deliberate breaker decision, and one of them contradicts the
adapter it says it mirrors.)

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  ONE RULE
  - test_predicate_counts_only_408_429_and_5xx: 400/401/403/404/409/422 -> False;
    408/429/500/502/503 -> True; 200/204/301 -> False · covers: M1, M2, R
  - test_predicate_agrees_with_retry_policy_for_every_status: walk 100..599 and assert
    status_counts_as_upstream_failure(s) == (classify_status(s) is not None). This is the
    anti-drift test — the whole point is one rule, so it is asserted over the WHOLE domain
    rather than at sampled points · covers: M4

  PER SITE (six, deliberately separate — a site that turns out to want the old behaviour
  can be dropped without unpicking the others)
  - test_finetune_submit_401_does_not_open_the_breaker: ten consecutive 401s from a stub
    upstream; assert the tenant's breaker still allows calls afterwards. Ten, not five,
    so the test fails loudly if the threshold is ever raised rather than passing by
    luck · covers: M1
  - test_finetune_poll_404_costs_one_request: a stub counting requests; poll a job the
    provider does not know; assert exactly ONE upstream call, not three · covers: M3
  - test_finetune_cancel_409_does_not_open_the_breaker · covers: M1
  - test_embeddings_401_does_not_open_the_breaker: VectorStoreEmbeddingClient.embed
    against a 401; assert breaker still closed AND exactly one attempt (the retry loop
    must break, not burn its second attempt on a client error) · covers: M1, M3
  - test_bedrock_embeddings_400_does_not_open_the_breaker: the site todo #60 does not
    name; a Titan invoke returning 400 · covers: M1
  - test_bedrock_stream_403_does_not_open_the_breaker: the streaming site, whose >= 400
    diverges from the AnthropicCompletionUpstream it claims to mirror · covers: M1

  PROTECTION NOT WEAKENED (the regression direction — this task must not make the breaker
  worse at its actual job)
  - test_finetune_submit_500_still_opens_the_breaker: five consecutive 500s; assert the
    breaker OPENS · covers: M2
  - test_embeddings_transport_error_still_opens_the_breaker: httpx.ConnectError x5 ·
    covers: M2
  - test_429_still_opens_the_breaker: upstream backpressure must keep counting — the one
    4xx that is not a client mistake · covers: R

  RESPONSE UNCHANGED
  - test_4xx_still_reaches_the_caller_unchanged: same exception type / same status+body as
    before the change. This task alters ACCOUNTING only, and nothing else may move ·
    covers: M5
</test_plan>

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/breaker_4xx_classification/` · MUST run red (missing implementation) before Build.

RED CONFIRMED 2026-08-07 — 8 failed, 2 passed. Crucially the reds split into TWO kinds,
and the second kind is the strong evidence:
  * 3 x ImportError `status_counts_as_upstream_failure` — the contract symbol is absent.
  * 5 x the LIVE DEFECT, reproduced with exact numbers:
      - finetune submit, 10 x 401  -> `CircuitOpenError: Circuit breaker is open`
      - finetune cancel, 10 x 409  -> `CircuitOpenError`
      - finetune poll,   1 x 404   -> `assert 3 == 1` (burns all three attempts)
      - embeddings,      1 x 401   -> `assert 2 == 1` (spends the idempotent retry)
      - bedrock embeddings, 10 x 400 -> `CircuitOpenError`
The 2 PASSING are the protection-not-weakened arms (503 still opens; transport still
counts) — correctly green before AND after, which is what stops "stop counting 4xx" being
implemented as "stop counting anything".
The bedrock red also CONFIRMS the finding that todo #60 does not carry: that site's
breaker is a per-instance `self._breaker`, not per-tenant, so a caller sending bad
requests takes the provider down for every other caller on the instance.
Command: `uv run pytest tests/breaker_4xx_classification -p no:cacheprovider --no-cov -q`.
One correction during authoring: the bedrock test first failed on MY bad import
(`proxy.infrastructure.credential_context` vs the real `proxy.domain.credential_context`) —
a false red that proved nothing. Fixed before this record was written.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

`make ci` GREEN on the full tree — 2026-08-07, exit 0:
```
4559 passed, 7 skipped, 28 deselected, 1 xfailed, 0 failed   37:00
coverage 91.11% (main was 90.94%)   infra-guard trips: 0
```
main carried 4531; +28 = the three suites landed in this run (10 + 12 + 6).

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: self · adversarially checked:
- **Could "stop counting 4xx" have been implemented as "stop counting anything"?** That is
  the cheap fake, and every per-site test would still pass. Two arms exist precisely to
  refuse it: 503 must still open the breaker (10 consecutive 503s -> CircuitOpenError), and
  a transport error must still count. Both pass.
- **Is the predicate overfit to the statuses the tests name?** It is asserted over the WHOLE
  domain, 100..599, against `RetryPolicy.classify_status`. A sampled test would let the two
  definitions drift in the gap between samples, and a silently-diverged breaker policy is
  the bug being fixed.
- **Do the per-site tests exercise the REAL method bodies?** Yes — the defect lives in those
  bodies' except-clauses, so `httpx.AsyncClient` is patched to inject a MockTransport rather
  than stubbing the methods. A double that overrode `submit`/`embed` would prove nothing.
- **Was any response changed?** No. Only `on_upstream_error()` vs `record_success()` moved;
  every raise/return is byte-identical. The retry loops additionally BREAK on a terminal
  4xx, which changes request COUNT (asserted: 404 costs 1 request, not 3) but not the
  response the caller sees.
- **Residual, stated rather than hidden:** `record_success()` on a terminal 4xx also RESETS
  the consecutive-failure counter, so 4xx interleaved with 5xx delays an open. That is the
  existing `execute_with_retry` semantics, chosen deliberately at freeze over inventing a
  third breaker verb; consistency with the seam beat a marginally sharper counter.
- **Not a security change.** Availability only — no authz, no secret, no data path.

### GATE RECORD
Reported: yes
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-08-07

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
