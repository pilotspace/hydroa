# CONVENTIONS  (living documentation — set once, kept for the whole project)

Language/framework: Python 3.12 / FastAPI (gateway) · TypeScript / Next.js 15 (dashboard)
Folders: monorepo — `apps/gateway` (src layout: `src/gateway/`, `tests/`),
         `apps/dashboard`, `infra/envoy`, `scripts/`; ADD task state in
         `.add/tasks/<slug>/TASK.md` (production code lives in `apps/`, not under `.add/`)
Naming: Python `snake_case` files/functions, `PascalCase` classes; TS components
        `PascalCase`, hooks `useCamelCase`, files `kebab-case.tsx`; modules by
        domain (`proxy/`, `tenants/`, `auth/`, `usage/`, `core/`), not by layer
Lint/format: `ruff check` + `ruff format` (line 100) + `mypy --strict`, enforced in CI (`make ci`)
Errors: machine-readable codes `ERR_<DOMAIN>_<REASON>` (string enums), returned as
        RFC 9457 problem+json — never free text
Architecture: CLEAN ARCHITECTURE per domain module — `domain/` (entities, ports
        as Protocols, domain errors; zero framework imports) ← `application/`
        (use cases orchestrating ports) ← `infrastructure/` (SQLAlchemy/argon2/
        jwt/httpx adapters implementing ports) ← `api/` (FastAPI routers, DTOs,
        dependency wiring). Dependencies point INWARD only; `core/` is the shared
        kernel (config, problem+json errors, ids, db base). Composition root =
        `main.create_app`. Stateless gateway behind Envoy; async-only request path; every
        outbound IO has timeout + bounded jittered retry (idempotent ops only —
        never retry a non-idempotent completion) + circuit breaker on OpenRouter;
        Postgres via SQLAlchemy 2 async + Alembic (additive migrations, rollback
        documented); usage ledger append-only; all tenant data `tenant_id`-scoped
Testing: red/green TDD mandatory — tests red before build; pytest + pytest-asyncio +
        httpx.ASGITransport; assert observable behavior, never internals; coverage floor 80%
        Folded from v1 (2026-06-10):
        - red must be red for the RIGHT reason — verify the failure mode (missing
          implementation, not a test bug) before freezing; a wrong-reason red invalidates
          the gate (evidence: budgets suite called a non-existent token method)
        - security-sensitive failure paths get byte-identical responses across ALL failure
          modes, enforced by dedicated tests (anti-enumeration/oracle; evidence: api-keys
          authz suite drove always-run hash comparison even for unknown rows)
        - UI red suites (vitest/RTL) scope every text/role assertion with
          `within(<section>)` and name the owning component — bare `getByText` string or
          regex matchers over a whole page over-constrain the build when data repeats
          across sections (evidence: dashboard-usage duplicate-match collisions)
        Folded from v6 (2026-06-12):
        - full-jitter backoff timing is asserted by monkeypatching BOTH `random.uniform`
          AND `asyncio.sleep` — capture the computed delay, never sleep wall-clock
          (evidence: retry_policy R4)
        - GREEN-BY-DESIGN tests (asserting the ABSENCE of behavior, e.g. no stream
          fallback) are labeled as such in the §4 plan so a pre-build green is not
          mistaken for a wrong-reason red (evidence: model_fallbacks F11)
        - a fake async Redis for concurrent SET-NX tests processes commands atomically and
          orders task yields explicitly — asyncio.gather gives no interleaving guarantee, so
          the single-probe NX assertion needs deterministic ordering (evidence: cooldown_circuit C4)
        Folded from v2 (2026-06-11):
        - stream/wire parsers test fragmentation as part of the input domain by default —
          split-at-midpoint AND byte-by-byte chunk cases (evidence: live-upstream-smoke;
          per-chunk parser lost the split usage frame)
        - test arranges call CANONICAL routes only — an arrange that invents an endpoint
          pushes builders into expanding product surface; builders treat the contract's
          "Modules touched" list as a hard boundary (evidence: observability /tenants
          compat router, rejected at review)
        - drain/consume loops bounded by a batch size are tested against backlogs LARGER
          than the batch, and emptiness checks must cover undelivered + pending sets
          (evidence: ops-hardening flush_once count=100 early-exit, caught at review)
        Folded from v3 (2026-06-11):
        - contract prose specifies the OBSERVABLE surface (status + code + shape); it names
          internal types only at a layer boundary that needs them — listing domain-error
          class names in §3 invites dead code (evidence: model-mgmt ModelDisabledError /
          ModelNotFoundError both born dead at build, removed at review)
        - route params that may contain "/" (catalog model ids) use the :path converter and
          the §3 contract says so — ASGI servers deliver the DECODED path (evidence:
          model-mgmt PUT /admin/models/{model_id:path})
        - jsdom tolerates invalid table DOM that real browsers restructure — dashboard
          reviews include a markup-validity lens; component-test green does not prove valid
          HTML (evidence: dashboard-govern nested-<tr> shipped green through 77 tests,
          caught only at manual diff review)
        - port extensions that frozen fakes cannot implement use the hasattr capability
          seam: the use case detects the new method and falls back to the frozen one —
          frozen tests never edited (evidence: soft-budget seam, model-mgmt
          check_for_tenant; two milestones, zero frozen-test edits)
        Folded from v5 (2026-06-12):
        - every app.state test-injection seam is PAIRED with a production-wiring
          regression test that asserts the default (no-seam) construction returns the
          real adapter — seam-presence tests alone prove the fake, never the wiring
          (evidence: tests/oidc_exchanger_binding; two production-dead OIDC paths found
          live behind passing frozen suites)
        - milestone close REQUIRES the live edge verification pass — two consecutive
          clean runs against a long-lived stack (re-runnability doubles as the identity-
          isolation proof); never waived because gates are green
        - rename/branding tasks freeze a file-by-file rename table + a wire compat-pin
          list as their §3 shape; pure-file/grep suites pin the result; wire pins get a
          green-by-design guard test that must stay green forever
        - Next.js "use client" root layouts cannot export metadata — pick the server-
          component placement at §1 time
        Folded from v4 (2026-06-11):
        - the capability seam is now TYPED (supersedes the v3 hasattr rule for kwargs):
          additive kwargs on a frozen port are declared as a `TypedDict(total=False)` in
          the port module and the implementation declares `supported_extras: frozenset` —
          callers filter against the declaration; never inspect.signature/hasattr probing
          (evidence: UsageRecordExtras in proxy/domain/ports.py; user-mandated rework of
          the inspect.signature dispatch mid-v4)
        - a security control skipped by design needs a PRIMARY-SPEC citation plus pinned
          preconditions written into §3 — preconditions are HARD-STOP tripwires, not prose
          (evidence: sso-oidc TLS-channel ID-token validation per OIDC Core 1.0
          §3.1.3.7(6); verify=False anywhere voids the sanction)
        - milestone exit criteria are verified LIVE through the real edge before close;
          frozen suites can miss raw-marker/side-channel assertions they were not written
          to see (evidence: scripts/live_v4_verify.py found the unrecorded pii_masked
          marker after 326 tests passed green)
        - autouse fixtures that flip feature flags belong in the owning suite's conftest,
          never the repo root — a root bridge silently enables the feature for every
          future suite (evidence: obs-callbacks builder's root conftest, relocated at
          review into tests/obs_callbacks/conftest.py with a disposition comment)
        Folded from v8 (2026-06-12):
        - `make ci` runs `ruff check .` over the WHOLE tree incl. tests/ — brief build/test
          subagents to run the SAME lint scope as the gate, or the orchestrator re-lints
          tests/ before the authoritative run (evidence: a src-only subagent left a RUF001
          ambiguous Greek `α` in a balance-strategies assert message; use ASCII in messages)
        - a front test that asserts on EXISTING code must match that type's real surface at
          AUTHORING time, not at build — read the source (evidence: DL2b first asserted
          `ProblemError.status_code`, but the field is `.status`; caught at test-review by
          reading core/errors.py before the front froze, not as a red-for-wrong-reason)
        - weighted-random behavior is assertable DETERMINISTICALLY via an injected
          `random.Random(seed)` + a distribution band over many draws (e.g. 1000 draws,
          0.80<b_share<0.98), never by mocking — keeps the test honest about the real algo
        - the gateway suite has a ROTATING set of timing/env flakes (health_alerting s07–s11
          fixed-50ms async-write race, semantic_cache, response_caching, a guardrails case)
          independent of the change under test; a green gate needs a flaky-isolation pass
          (full-suite-minus-flaky deterministic green) + a stash-repro to attribute reds.
          Candidate fix: poll-until-row instead of a fixed sleep
        - a live harness firing bursts must PACE under the edge rate limit (Envoy
          local_ratelimit = 50 req/s global): a statistical check (weighted distribution)
          needs volume, so it needs pacing — the two are coupled (evidence: C1's 40-request
          sample + C5's trip loop drained the bucket → 429 "local_rate_limited" on a
          following /admin/keys; 50 ms/req + a settle fixed it)
Dependencies: every package in `.add/dependencies.allowlist`; CI gate
        (`scripts/check_allowlist.py`) rejects unknown packages
        Folded from v1 (2026-06-10): the allowlist governs PYTHON packages only — node
        dependencies are governed by the committed lockfile + orchestrator review at the
        freeze/gate seams until the allowlist format is extended (open follow-up for v2)
Build/harness conventions folded from v1 (2026-06-10):
        - the contract-freeze flag ritual includes a cross-artifact consistency pass
          (spec vs GLOSSARY vs prior frozen contracts) — it caught the argon2-vs-SHA-256
          conflict before any code existed
        - when frozen test files conflict with lint/format rules, suppress at config level
          (pyproject per-file-ignores or format excludes) — frozen tests are never edited
          to satisfy tooling
        Build/harness conventions folded from v6 (2026-06-12):
        - risk=high tasks carry an explicit retryable/classification TABLE in §1 — the
          table format is load-bearing, fixing which failures retry/fall-through before
          the build can interpret it ambiguously (evidence: retry_policy retryable set,
          model_fallbacks fall-through table)
        - a [contract]-level flag that spec alone cannot resolve becomes a BUILD constraint
          with an acceptance criterion, not merely a §3 note — e.g. the concurrent-probe
          race requires the TTL relationship (probe duration < probe TTL) enforced
          (evidence: cooldown_circuit half-open)
        - when parallel tasks share a protocol, the OWNING task defines and freezes the
          interface before the consuming task builds against it — never two divergent
          duck-typed copies (evidence: ModelHealthGate owned by model-fallbacks, the
          Redis gate in cooldown-circuit built to the frozen shape)
        Build/harness conventions folded from v7 (2026-06-12):
        - a "module X stays byte-identical" invariant has no compile-time enforcement —
          it rests on a behavioral test (EM11) plus a manual `git diff --stat` of the
          named INVIOLABLE files at the verify WIRING check; downstream task contracts
          must spell out the boundary as an explicit "do NOT import/use <private symbol>"
          constraint. Future improvement: an ArchUnit-style test asserting the forbidden
          import (evidence: chat-untouched invariant across provider-seam + the 3 v7
          non-chat endpoints, enforced only by EM11 + git diff this milestone)
        - a billed-quantity / fallback policy (e.g. bill actual-returned vs requested-n)
          is a BUSINESS decision, not a technical default — it must surface as a
          [contract] flag at §3 top and be resolved at freeze, never silently coded
          (evidence: images-endpoint dropped the `or requested-n` fallback at freeze to
          avoid over-billing failed/empty responses; billed exactly len(data))
        - live-verify e2e closes must SELF-CONTAIN their upstream credentials in the
          compose overlay (a non-secret placeholder), never source them from operator
          shell env — an empty-but-interpolated key (`${VAR:-}`) yields a malformed
          `Authorization: Bearer ` header that httpx/h11 reject client-side
          (LocalProtocolError) before egress, surfacing as an opaque upstream 500
          (evidence: v7 C5 came up with an empty GATEWAY_OPENROUTER_API_KEY; fixed by
          baking stub-openrouter-key into docker-compose.e2e.v7.yml; audit v4–v6 overlays
          for the same shell-env dependency)
        Build/harness conventions folded from v8 (2026-06-12):
        - the v7 self-contained-creds lesson RECURRED because a new overlay composes only
          a SUBSET of siblings: docker-compose.e2e.v8.yml stacks base+v4+v5+v6 (NOT v7),
          so it did not inherit v7's placeholder and the base `${VAR:-}` empty default won
          → the identical "Illegal header value b'Bearer '" 500. STRENGTHENED rule: every
          overlay that drives an upstream SETS its own non-secret placeholder, and the
          builder briefing/CONTEXT.md must state which sibling overlays are and are NOT
          composed (a "kept from v6" claim is wrong if v6 never set the key)
        - "frozen behavioral pin → supersession" works ADDITIVELY: supersede a frozen SYNC
          seam by adding an OPTIONAL async capability (aorder) selected via `isinstance` at
          the call site — frozen tests keep calling the sync seam (order()) and stay green,
          zero re-freeze (evidence: routing-strategy rs1..rs8 + model_fallbacks green under
          the async-aware router). The reusable recipe for evolving any frozen Protocol
        - a pure-sync seam is the cleanest concurrency story under an asyncio loop (atomic
          within one step) but it PINS the seam sync; when a known async successor exists,
          freeze the SUPERSESSION NOTE in the §3 contract up front so the re-pin is a
          planned follow-up, not a surprise re-freeze (evidence: routing-strategy order())
        - a cross-cutting candidate constraint (saturation skip) filters UPSTREAM of the
          routing strategy — it composes with EVERY strategy (ordered/shuffle/least-busy/
          latency) and the v6 loop without touching any of them, because the strategy only
          ever sees survivors (evidence: deployment-limits filter above _strategy_order_async)
        - a cooldown/health-gate LIVE check asserts the AUTHORITATIVE gate state (GET
          /admin/routing snapshot_state), never infers it from upstream-stub call counters —
          under a non-deterministic strategy (simple-shuffle) + upstream retries the counter
          is muddied and the inference flakes (evidence: C5 stub-counter version failed,
          primary counter 3→6 under retries; the /admin/routing-poll version passed 29/29 ×2)
        - capture the authoritative pytest+coverage to an orchestrator-owned path
          (`> /tmp/...log 2>&1`); the `rtk` tee log filename/rotation is unreliable for a
          re-run and `ls -t` can return a stale cached name
        Build/harness conventions folded from v9 (2026-06-13):
        - a multi-provider chat path dispatches by the SERVED model's catalog provider
          through a wrapper (`ProviderAwareCompletionUpstream`) over a
          `dict[provider→adapter]` map; an unknown/unset provider FAIL-SAFES to the
          "openrouter" default adapter — the v8 router/billing path stays untouched
          BEHIND the wrapper, so the default chat path is byte-identical (evidence: C7
          5/3/8 + the 628-unit suite green with the OpenRouter path unmodified)
        - a relocated composition seam needs a PUBLIC alias for its wiring tests: v9 moved
          `app.state.completion_upstream` to the dispatch wrapper, so the OpenRouter
          wiring/retry/base-url suites were redirected to a new
          `app.state.openrouter_completion_upstream` seam (behavior preserved, the
          dispatch-type assertion strengthened) — legitimate regression-maintenance, NOT
          test-weakening (evidence: 7 wiring assertions redirected, ps10 strengthened)
        - every non-OpenAI provider stream() MUST emit a TERMINAL OpenAI chunk carrying
          `usage:{prompt_tokens,completion_tokens,total_tokens}` before `data: [DONE]` —
          the frozen `extract_usage_from_sse` scans joined frames in REVERSE for the LAST
          usage frame, so a provider's SSE translation IS its stream-path billing
          correctness (evidence: Anthropic 7/4 + Gemini 9/6 terminal usage, live C2/C4)
        - ground each provider's wire translation in a VERBATIM SSE fixture shared by the
          adapter unit suite AND the live stub — the stub bytes match the unit fixtures,
          so a green unit suite PREDICTS a green live pass (evidence: v9_provider_stub.py
          SSE == the _ANTHROPIC_SSE / _GEMINI_SSE unit fixtures; double-pass first-try)
        - a new in-memory resolver map (model_id→provider) refreshes at lifespan startup +
          on /internal/catalog/sync; the live harness SEEDS provider-tagged rows via
          psql docker-exec then RESTARTS the gateway so the lifespan refresh() reads them
          (no source-sync = no deactivation) — confirmed the freeze's least-sure flag
          first-try (evidence: seed-then-restart, 35/35 ×2, no fallback, no iteration)
        - raw httpx per provider over vendor SDKs keeps ONE resilience contract
          (CircuitBreaker/timeout/UpstreamUnavailableError/v8-fallback) across every
          provider — matches LiteLLM's own hand-rolled llms/anthropic httpx; avoids
          per-provider SDK dependency sprawl + divergent resilience seams (Tin-confirmed)
        Build/harness conventions folded from v10 (2026-06-13):
        - extend a frozen seam for a richer SHAPE by adding ADDITIVE branches to the SAME
          pure helpers, never a new adapter class: v10 tools landed in the v9 per-provider
          request/response/SSE helper triad with the adapter class untouched and NO
          re-freeze — provider tool-translation is a repeatable 4-step template (request
          tools/tool_choice + message restructure · response native-call→tool_calls ·
          streaming native-event→delta fragment · no-tools byte-identical pin)
        - a streaming fragment helper (`build_tool_call_delta`) absorbs ASYMMETRIC provider
          granularity at one UNIFORM seam: Gemini emits one combined id+name+args fragment,
          Anthropic streams id+name then incremental input_json_delta needing a content-
          block→tool_calls index REMAP (a `block_to_tc` dict) — the remap recurs for any
          provider interleaving text+tool events (evidence: both stream suites green)
        - a freeze-first contract task's red suite MIXES unit tests (new helpers) with
          CHARACTERIZATION pins (tools flow unstripped through the v9 dispatch seam; no-
          tools byte-identical) — the pins guard a behavior that already works so the
          provider tasks cannot silently break it; some pins are GREEN-BY-DESIGN from the
          start and MUST stay green through the build (evidence: 2 of 10 anthropic red
          tests green-by-design; test_request_passthrough_tools_unstripped green pre-build)
        - VERIFY the request-side assumption IN CODE before freezing (router.py:42 forwards
          a raw dict, so tools/tool_choice flow unstripped) so the contract pins a real
          invariant — a Pydantic request model would strip tools and break passthrough
        - a multi-turn protocol is proven LIVE by ONE STATELESS request-inspection stub:
          the turn is discriminated by the presence of a TRANSLATED tool result (Anthropic
          tool_result block / Gemini functionResponse part), no server-side turn state —
          operator-run live checks double as the red→green suite for cross-provider
          translation (red against a v9-only gateway, green after the provider tasks);
          a name-correlation test needs a TWO-MESSAGE fixture (assistant tool_calls turn +
          the tool message) to exercise id→name resolution honestly (evidence: 18/18 ×2)
        Build/harness conventions folded from v11 (2026-06-13):
        - a NEW directive seam can COMPOSE with a prior one rather than duplicate it: v11
          response_format reused v10's tool seam wholesale for Anthropic — a synthetic
          forced `json_output` tool (build_json_coercion_tool returns the canonical v10
          Tool/ToolChoiceNamed types) whose tool_use is UNWRAPPED back into message.content
          (tool_use→content inversion), APPENDED alongside caller tools so both coexist
          (only json_output is unwrapped; caller tools still surface as tool_calls)
        - prefer a SHARED frozen extractor (extract_response_format) as the single no-op +
          validation gate every provider calls — a provider gets the byte-identical
          guarantee + the rejections for free (Gemini: 1 import + 1 call delivered the whole
          request branch); response_format on a native-field provider is REQUEST-SIDE ONLY
          (responseMimeType/responseSchema on the existing generationConfig; the unchanged
          v9 response path already maps output to message.content — no response/SSE code)
        - a streamed coerced block needs THREE coordinated SSE touchpoints bridged by per-
          call state (coercion_block_index/saw_coercion): content_block_start MARKS the
          block, input_json_delta ROUTES by that index to delta.content (not delta.tool_calls),
          message_delta OVERRIDES finish to "stop" — the shape recurs for any provider that
          streams a coerced block; a live `_sse_has_tool_calls` guard makes the no-leak
          invariant OBSERVABLE, not just asserted-absent (evidence: 13/13 ×2, both exit 0)
        - GATE INTEGRITY under a flaky shared DB: the full `-m 'not e2e'` suite is NON-
          DETERMINISTIC against the shared dev Postgres (FK-violation flake, 16/34/44
          varying; each suite passes IN ISOLATION) — DO NOT auto-pass on it. The trustworthy
          per-change gate is the no-DB blast-radius run (translation+dispatch suites,
          deterministic); record the flake honestly, never a false PASS (recurring v8 lesson)
Git: `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer; message
        drafted in `tmp/*.txt`, committed via `git commit -F`; scopes: gateway,
        dashboard, infra, docs, pipeline, config
