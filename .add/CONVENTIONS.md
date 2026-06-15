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
        Folded from v13 (2026-06-14, frontend a11y/verify):
        - axe-core in jsdom gates on `results.violations` FILTERED to impact ∈ {serious,
          critical}, NOT `toHaveNoViolations()` — the latter fails on MODERATE best-practice
          rules (region/landmark) that fire when a component is scanned in ISOLATION, masking
          the real gate; the `color-contrast` rule MUST be disabled (`axe(c,{rules:{"color-
          contrast":{enabled:false}}})` — jsdom has no canvas), and always scan a POPULATED
          container (gate on findBy/waitFor first), never a skeleton (evidence: v13 12/12
          earned green across 7 surfaces)
        - run the COVERAGE gate (`vitest run --coverage`) before claiming "coverage held" —
          `--no-coverage` HID a real 78.14%<80% regression that only the coverage run + the
          adversarial earned-green subagent surfaced (evidence: v13 task1 components/ui untested)
        - a VERIFY-ONLY task can be legitimately GREEN-on-first-run (no product code to write);
          the honest red-first is FILE-ABSENCE, and integrity comes from a DISCRIMINATING
          MUTATION check — inject a known-critical violation (e.g. `<img>`-no-alt) through the
          SAME helper and confirm it is CAUGHT, then delete the scratch — never manufacture a
          fake red (evidence: img-no-alt→`image-alt` caught; the verify suite's zero-violation
          result is real, not a vacuous/crashed scan)
        - two vitest projects (legacy `tests/` + bff `tests-bff/`) both resolve client fetches
          to the same `http://localhost:3000` base (the `appBase()` Node fallback); the split
          is only WHICH mocks/server has default handlers. Pure-props components are
          deterministically STATE-testable without msw (render with isLoading/isError/data
          props); fetching surfaces need the project whose server seeds their endpoint
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
          [RESOLVED in v12 — see below: the FK flake is now killed at its source]
        Build/harness conventions folded from v12 (2026-06-13):
        - the recurring FK-violation flake (v8–v11) is killed by a SURGICAL, group-preserving
          per-test Redis clear (global autouse fixture in tests/conftest.py): the contaminator
          is leaked UNDELIVERED `usage:events` stream entries (record() pushes fire-and-forget;
          a later flusher-driving suite's XREADGROUP `>` consumes them and INSERTs usage_records
          against its freshly-recreated schema → FK). Clear with `XTRIM usage:events MAXLEN 0`
          (clears backlog, PRESERVES the `ledger-flusher` consumer group) + `DEL usage:spend:*`.
          NEVER FLUSHDB — it deletes the stream WITH its consumer group → every flusher-driving
          suite fails NOGROUP on XREADGROUP and the suite runs ~3x slower (tests retry on broken
          state). Evidence: 2 consecutive clean full runs (730 passed ×2, 595s/643s ≈ the 585s
          neutralized baseline) vs FLUSHDB's 5 failures + 1222s.
        - a test-isolation fixture must NEVER cancel pending asyncio tasks: cancelling all
          non-current tasks kills the pytest-asyncio/anyio runner task (CancelledError at
          teardown). Function-scoped event loops already reap a test's leaked fire-and-forget
          tasks at loop close, so a SETUP-ONLY pre-test clear is the sufficient guarantee
          (no teardown drain needed). redis.exceptions.RedisError does NOT subclass builtin
          ConnectionError — catch RedisError for graceful Redis-down no-op.
        - `make test-fast` (root Makefile) is the documented fast per-change gate: the no-DB
          MockTransport/pure-unit suites (translation + dispatch + provider), --no-cov, infra-
          free (the autouse clear degrades to a no-op when Redis is absent). `make test` (full
          suite) stays the thorough gate, now deterministic.
        - a billing-behavior CHANGE SUPERSEDES prior estimate tests rather than weakening them:
          when Gemini embeddings graduated estimate→exact (:countTokens), the v9 estimate tests
          were updated to the exact-count contract and documented as supersession at the freeze
          (NOT a test weakened to pass) — the distinction is the green-by-design fallback tests
          that prove the estimate path still works as a documented last resort.
        - extend a concrete class you OWN by EXPLICIT constructor DI, never runtime reflection:
          NonChatGovernance gained an optional `session_factory` param (default None) to fire the
          shared soft-budget alert seam — reflection-free, honoring the no-hasattr/inspect rule;
          the chat path's legacy `getattr(guard, "_session_factory")` is the pattern NOT to copy.
        - LIVE-VERIFY against the shared e2e DB: count-based assertions must use a before/after
          DELTA, never an absolute or "recent-window" count — consecutive double-pass runs share
          the e2e Postgres, so a prior pass's legitimate rows false-positive a "no spurious rows"
          check (v12 C4b: switched "unknown key_ids in last 60s" → usage_records delta == 0).
        Build/harness conventions folded from v13 (2026-06-14, frontend scope-lock + verify):
        - the §5 scope-lock flags GITIGNORED build artifacts (`.next/`, `coverage/`,
          `tsconfig.tsbuildinfo`) as scope violations — the engine `_SCOPE_EXCLUDE_DIRS` is only
          .git/.add/__pycache__/node_modules — so a frontend task must DECLARE them in §5 Scope
          (or clean them before the gate). The anchor's `declared` list is FROZEN at the tests→
          build snapshot, so editing §5 alone does NOT clear a violation: RE-CROSS tests→build
          (`add.py phase tests` then `advance`) to re-snapshot (evidence: v13 task2 tsbuildinfo)
        - STRENGTHENING tests mid-build (e.g. after an adversarial review finds coverage gaps)
          requires going BACK to the tests phase and RE-CROSSING tests→build to re-snapshot the
          tripwire — editing tests while IN build trips `build_tampered`; adding assertions is
          legitimate (strengthening, not weakening) but must follow the re-cross ritual
          (evidence: v13 task4 added Shift+Tab + isolated axe scans, re-crossed clean)
        - the adversarial earned-green refute-read (subagent, model sonnet) earns its keep on
          VERIFY tasks too: it returned EARNED-WITH-GAPS and surfaced 3 real coverage gaps the
          first green hid (Shift+Tab wrap untested on both dialogs; isolated state renders
          un-scanned) — all closed, focus-trap branch coverage rose 73.91%→75% (evidence: v13 task4)
        Test conventions folded from v15 (2026-06-14, dashboard feature-coverage):
        - every `useMutation` carries an `onError` that surfaces the BffError title, AND every
          contracted error branch (404/409/422/403) gets its OWN red→green test — never just the
          happy path + the read-side rejection. A passing suite "looks complete" when a missing
          path has no red anchor (silent-mutation-failure DEFECT recurred across model-mgmt then
          teams budget PATCH → now a STANDING rule, evidence: test_budget_patch_server_error).
        - a "loading shows role=status" assertion is VACUOUS unless it also proves the spinner
          RESOLVES (assert the loading→data transition: `findByText` + `queryByRole("status")
          .not...` after the T=0 assert) — a permanent role=status node would otherwise pass it.
        - a "skip-link is first focusable" assertion must query ALL focusable types
          (`a[href],button,input,select,textarea,[tabindex]:not([tabindex='-1'])`), not just the
          first `<a>` — a preceding focusable button/input would slip through anchor-only checks.
        - a fidelity test pins the PER-ENTITY value (pair A=true WITH B=false), never asserts a
          single truthy case (an always-true cheat passes A=true alone); enumerate EVERY block on
          a "no leak on 403" assertion (one-heading-absent passes empty shell rendering); cover a
          fixture per ENUM VALUE not per error-class (half_open was silently skipped while cov
          stayed high); a write-only-secret needs an EXPLICIT negative DOM assert (input=="" + the
          "<stored>" sentinel absent everywhere + no secret field on the role-denied path).
        - scope shared msw fallbacks to the paths that truly need them — a permissive
          `/api/gw/:path*` wildcard silently defeats `onUnhandledRequest:"error"` (a forgotten
          per-test handler returns wrong data, not a loud failure) (deferred: bff-test-harness-
          strict-handlers); role-scoped queries (`getByRole("textbox",{name})`) disambiguate when
          one control's accessible name is a superstring of another's (budget input vs "Save
          budget for X" button collided under getByLabelText substring-match).
        Build/harness conventions folded from v15 (2026-06-14, dashboard feature-coverage):
        - a milestone-EXIT verification suite legitimately lands GREEN on first run (the behavior
          already shipped + gate-PASSed per-surface); "RED for the right reason" maps to "the
          consolidated bar is newly codified and provably held", with earned-green proven by an
          adversarial refute-read rather than a first-run failure (evidence: feature-coverage-
          verify 8/8 green then hardened D1/D2/G1/G4 after the audit, via the re-cross ritual).
        - a build-time port (Protocol) signature change is a legitimate SCOPE CORRECTION, not a
          contract change, when pyright forces it to reflect a new capability (TeamRepository
          .add_member gained `email` to clear reportCallIssue; §3 untouched).
        - GROUND records the response ENVELOPE per-endpoint, never assumes uniformity — `/admin/
          teams` returns a BARE array while `/admin/models` returns `{object,data}`; a wrong
          unwrap is a silent footgun (§0 must flag "BARE array, no {data} unwrap").
        Test conventions folded from v14 (2026-06-14, Next.js 16 upgrade):
        - adopting a framework's NEW lint rules that flag PRE-EXISTING code: downgrade error→warn
          (VISIBLE in `eslint .` output — never `// eslint-disable`, which hides it) to hold the
          0-error baseline, and TICKET the real fix as a follow-up; never break the baseline nor
          silently suppress (evidence: eslint-config-next 16's react-hooks/refs + set-state-in-effect
          flagged 60 pre-existing v13/v15 patterns → error→warn + `react-hooks-strict-lint`).
        - the PRODUCTION type-gate for the dashboard is `next build` (it type-checks the app graph),
          NOT a bare `tsc --noEmit`: `tests-bff/` is excluded from BOTH lint (eslint globalIgnores)
          and the type-gate, so test-harness type drift (e.g. Next 16's async-params Promise<{path}>
          typing) is tracked separately (`bff-test-harness-strict-handlers`), never blocking nor
          conflated with a clean production surface (evidence: v14 prod build TS-clean while tests-bff
          showed Promise<{path}> + msw-cast errors; the 236 tests pass at runtime via esbuild).
        Build/harness conventions folded from v14 (2026-06-14, Next.js 16 upgrade):
        - a risk:high major-dependency bump landing WITHOUT CI (Actions billing-blocked) must capture
          PROD-SERVER smoke curl output VERBATIM as gate evidence — a green jsdom suite cannot prove
          Turbopack-bundle / Edge→Node-runtime / prefetch-cache parity; `next build` + `next start` on
          127.0.0.1 + curl of an authed + an unauthed route through the guard is the in-scope runtime-
          parity proof, recorded in §6 (evidence: v14 5-curl proxy smoke byte-identical to the v13 guard).
        - an npm-advisory security gate is scoped to the SHIPPED surface (`npm audit --omit=dev`),
          NOT the full dev+prod audit: dev-toolchain advisories (vitest/vite/esbuild) are pre-existing,
          never shipped, and triaged + ticketed separately (`devtool-vitest4-upgrade`) — conflating them
          either blocks a clean production upgrade on dev debt or hides real shipped risk (evidence: v14
          prod audit 0/0/0 vs full audit 7; the SDD §Spec principle codifies the why).
        Test conventions folded from v17 (2026-06-15, hardening / clear carried debt):
        - msw `onUnhandledRequest:"error"` in Node/jsdom does NOT reject the fetch — the interceptor
          RESOLVES a 500 Response — so a forgotten handler LOGS loudly but a test that doesn't assert
          on that request still PASSES; "0 test failures" is NOT "0 leaks". The real monitor is the
          stderr unhandled-request COUNT (evidence: the 13→2→0 reduction at constant green; bff-test-
          harness-strict-handlers). Scope shared fallbacks to needed paths — never a `:path*` wildcard.
        - reach a TRUE 0-leak by stubbing EVERY render of `useCurrentUser` (GET /api/auth/me): a shared
          AppShell test-setup stub + per-test stubs in the usage suites — an unhandled auth/me render
          leaks up to 7 logs under load. This couples with the `auth-me-session-verify` security task
          (once /api/auth/me verifies signatures, tests must supply a properly-signed token stub anyway).
        - tests-bff is now tsc-clean (18→0) → a standing test-tree `typecheck` gate is newly possible;
          the v16 "tests-bff excluded from the type-gate" convention can TIGHTEN to include the harness
          rather than tracking its drift separately (evidence: bff-test-harness-strict-handlers).
        - run the vitest floor with a generous `--testTimeout` (20s) so a CPU-starved load flake never
          reads as a regression — timing-sensitive tests (axe ≥5s, in-flight `toBeDisabled` windows)
          throw false failures under load (3 fail loaded → 240/240 green isolated → green with the
          timeout floor); pair with the existing `make test-fast` no-DB gate (react-hooks-strict-lint).
        Build/harness conventions folded from v17 (2026-06-15, hardening / clear carried debt):
        - an adversarial refute-read catches MIS-DIAGNOSIS, not just cheating: trace EVERY residual
          leak to its SOURCE file — never hand-wave a "benign cross-file late-resolve". 2 leaks labeled
          benign were in-file forgotten handlers in ui-ux-verify.test.tsx; the reviewer traced them →
          fixed to 0 (evidence: the EARNED-WITH-GAPS review + the beforeEach fix; bff-test-harness).
        - the v16 error→warn convention now has a worked DISCHARGE template: fix behavior-preservingly
          (the green floor is the proof) → flip the lint rule back to error → PIN it with a config-text
          ratchet-guard test (mirrors v17 strict-harness.test.ts). The ratchet test is config-text-only
          by design; `eslint .` 0/0 stays the real gate (evidence: react-hooks-strict-lint).
        Test conventions folded from v18 (2026-06-15, auth session hardening):
        - an msw default handler must be an INITIAL handler passed to `setupServer(...)`, NEVER a runtime
          `server.use()` registered from a setupFile — `afterEach(server.resetHandlers())` wipes runtime
          handlers after test #1, so the "default" silently vanishes and later renders leak (load-dependent
          "0 unloaded / N loaded"). Per-test overrides via server.use STILL win (LIFO) and are restored on
          reset. This was the ROOT CAUSE of the carried v17 /api/auth/me 0-leak (evidence: moving the legacy
          default into tests/mocks/handlers.ts initial handlers → 0 unhandled across the full suite ×2).
        Build/harness conventions folded from v18 (2026-06-15, auth session hardening):
        - a server-side fetch RELAY must set `redirect: "manual"` AND treat every non-200 as fail-closed:
          a followed 3xx can chain to a trusted 200 from an unexpected origin (a fail-OPEN identity bypass).
          Pair it with an `AbortSignal.timeout` bound + fail-fast no-retry on an auth hot path (evidence:
          the adversarial refute-read on auth-me-session-verify; redirect→503 test).
        - a STRUCTURAL source-grep guard must be PRECISE, not a bare keyword: `/SECRET/i` false-positived
          on a comment that EXPLAINED the absence of a secret. The precise form matches
          `process.env.*(secret|key|hmac|password|token)` + jwt-lib imports + verify-call names — it still
          catches a real secret read without tripping on prose (evidence: the test-precision fix during
          the auth-me-session-verify build; recurring "over-broad assert" smell from the v15/v17 folds).
        Test conventions folded from v19 (2026-06-15, reliability):
        - a pure classifier's pattern list must be tested in BOTH directions — true-positives (provider-real
          messages) AND generic false-positives ("field too long", "blocked by firewall") — a too-broad
          pattern fails DANGEROUS (spurious fallover), not safe (evidence: 5 guard tests added after the
          refute-read on error-aware-fallback flagged bare "too long"/"safety"/"blocked by").
        - for cumulative-deadline / retry-exhaustion logic, test the is_last × active-deadline CROSS-state
          explicitly — the green suite missed an is_last/deadline mislabel the verify-gate refute-read
          caught; boundary states on retry/timeout code earn the adversarial pass (evidence: retry-seam-unify
          REAL-BUG finding → fixed).
        Build/harness conventions folded from v19 (2026-06-15, reliability):
        - at freeze, cross-check a broad §3 RANGE against §1's explicit REJECT enumeration — the freeze gate
          did NOT catch §3 "status 400-499" silently contradicting §1 "429 already retry-handled"; the
          refute-read did (evidence: error-aware-fallback classifier now excludes 408/429).
        - a verify-time refinement that STRENGTHENS assertions and leaves §3 byte-identical is legitimate:
          act on the refute-read in-loop, then re-cross tests→build to re-snapshot — no test weakened
          (evidence: error-aware-fallback PASS after refine-and-re-cross).
        - declare the TEST SURFACE in §5 when the build will lint/format newly-authored tests (ruff/eslint
          on new test files diverges them from the tests→build snapshot → scope-gate trip), OR run the
          formatter inside the tests phase BEFORE the snapshot (evidence: retry-seam-unify ruff-format on
          3 new tests).
        - post-freeze refinements go in §6/§7, NEVER §3 — the tamper tripwire md5s the WHOLE §3 body
          (comments included), so even editing a §3 pseudocode COMMENT after the snapshot trips it
          (evidence: retry-seam-unify reverted the comment edit to keep the tripwire green).
        - the §5 "Scope (may touch):" declaration is parsed from a SINGLE physical line and FROZEN into the
          state.json scope anchor at the tests→build snapshot — a wrapped continuation path is silently
          dropped (scripts/* on line 1 recognized, infra/* on line 2 missed → scope_violation). Keep all
          scope tokens on ONE line; if you correct §5 after the snapshot, re-snapshot (phase tests →
          advance → advance) so the anchor re-resolves `declared` — editing §5 alone does nothing (the gate
          reads anchor.declared, not the live file) (evidence: reliability-verify, this milestone's close).
        Build/harness + testing conventions folded from v20 (2026-06-15, AWS Bedrock provider):
        - [TDD] an external-protocol signer/encoder must be tested against the ACTUAL target service's
          path/identifier shape, not just the canonical "happy" vector — all SigV4 fixtures used path "/",
          which hid that the path is signed RAW; real Bedrock model IDs carry a ':' version suffix that AWS
          canonicalizes to %3A, so raw-':' signing 403s every versioned-model call (evidence: SV8, a ':'-path
          test the refute-read added, was RED against the green-but-incomplete impl).
        - [TDD] for a vendor-protocol live verification, build an INDEPENDENT-ORACLE stub: re-implement the
          vendor's auth from spec (NOT importing our own signer) and PIN it to the vendor's published
          known-answer vector, so the stub ACCEPTS our real signed request only if it is genuinely correct
          AND rejects a tampered one — a CI-able cryptographic cross-check stronger than MockTransport and not
          gated on docker (evidence: bedrock_verify BV1 pins to AWS get-vanilla 5fa00fa3…, BV3 proves 403 on
          tamper, BV2 proves the real %3A-path signature passes; all in the no-DB floor).
        - [ADD] pin a security primitive's core math to an AUTHORITATIVE published vector via a small exposed
          seam (e.g. _signature() pinned to AWS get-vanilla), so higher-level self-computed expectations ride
          on a non-self-referential anchor — the green stays trustworthy when the public API shape has no
          published known-answer (evidence: SV0 anchors SV1/SV2/SV8).
        - [ADD] the §5 scope-token grammar CANNOT express a project-root-level file (a bare token = sibling
          of the previous token's dir; only '/'-containing tokens resolve to the project root) — a Makefile /
          top-level-file edit needs its own handling or an unconventional '../' token; prefer scoping the
          change into a subdir-resident file, or land the root-file edit as a separate standalone change
          (evidence: bedrock-verify's gate tripped scope_violation on a bare `Makefile` token that resolved to
          `infra/Makefile`; the bedrock-suites-in-test-fast floor edit was deferred to a follow-up).
        - [ADD] split a live-infra verify task into (a) a docker-free EARNED-GREEN core that fully proves the
          logic (real adapters → real socket → independent oracle) and (b) operator scripts for the
          edge/cache/billing pass — the gate never blocks on bringing up a heavy stack, the residue stays
          honest, and the live ×2 still runs when the stack is available (evidence: bedrock-verify gated on the
          pytest core, then the TLS-edge double-pass ×2 ran 10/10 once the e2e stack was up).
        Provider + security + testing conventions folded from v21 (2026-06-15, Azure OpenAI provider):
        - [SECURITY] when wrapping a transport exception whose request/response could carry a secret (api-key
          header, client_secret body, bearer token), use `raise ... from None` — the chained httpx error exposes
          `__cause__.request.headers/content` to any crash-reporter walking the chain; `str(exc)` (the clean
          transport message) is what surfaces. This is a TESTABLE property: assert `exc.__cause__ is None`
          (evidence: azure_ad + azure_embeddings regression tests). KNOWN GAP: the shared execute_with_retry seam
          + openai/bedrock/gemini/anthropic adapters still use `from exc` — a `provider-secret-chain-hardening`
          sweep is a carried follow-up (spans frozen contracts).
        - [ADD] any auth/secret-handling task's verify gate MUST run an INDEPENDENT adversarial security subagent
          (sonnet), not only the author's self refute-read — it caught a real api-key/client_secret chain-leak +
          WEAK-test gaps on tasks that looked like thin passthrough (evidence: azure-aad-auth + azure-embeddings,
          findings remediated via the change-request loop BEFORE the gate).
        - [TDD] a resilience-seam test injects a CircuitBreaker SPY subclass that counts on_upstream_error /
          record_success, so breaker transitions are ASSERTED not assumed — a 5xx test that only checks
          `pytest.raises` would pass an impl that never trips the breaker (evidence: _SpyBreaker, azure_embeddings).
        - [TDD] for a token-exchange provider (AAD client-credentials, and later managed-identity / GCP SA /
          AWS STS), the live oracle MINTS the credential at its own token endpoint and accepts the model request
          ONLY if the presented Bearer equals that minted token — an end-to-end auth proof (the token analogue of
          v20's independent SigV4 re-impl), not a header-presence check (evidence: azure_verify AV3 + live C1).
        - [SDD] an OpenAI-compatible provider is a THIN passthrough: chat/stream/tools/response_format/embeddings
          need ZERO body/response translation; only deployment/URL routing + the auth seam are new, and
          content-filter "mapping" is a no-op because the FROZEN classify_fallback_trigger already matches
          "content_filter"/"content management" (evidence: azure-chat, zero new classifier code).
        - [SDD] a token-exchange auth provider is instantiated ONCE and shared across every modality adapter
          (chat + embeddings) so there is a single token cache — assert object identity (`is`) in a wiring test,
          not just presence (evidence: azure-embeddings test_wiring_aad_only_shares_token_provider_instance).
        - [ADD] scope-snapshot cache prophylaxis: run build-phase ruff with `RUFF_CACHE_DIR=/tmp/...` and pytest
          with `-p no:cacheprovider --no-cov` so no `.ruff_cache`/`.pytest_cache` enters the tests→build
          snapshot; a SUBAGENT's ruff can still pollute repo-root, so clean transient caches then re-snapshot
          (phase tests → advance → advance) before gating — a root-level Makefile floor edit LANDED this way
          (vs v20's defer) (evidence: azure-auth-routing + azure-verify gate bounces resolved by clean re-snapshot).
        Security + testing + harness conventions folded from v22 (2026-06-15, provider security & config hardening):
        - [SECURITY] the `from None` rule is now the PROJECT-WIDE floor, not just the Azure bar: EVERY provider
          adapter's transport-error wrap (the shared execute_with_retry seam + openrouter/openai/anthropic/gemini/
          bedrock/azure stream/post_json paths) uses `raise ... from None`. The v21 KNOWN GAP is CLOSED. A future
          adapter MUST ship a `__cause__ is None` regression test in its own suite; the invariant is greppable —
          `rg "from exc|from terminal_exc" infrastructure/` must return zero secret-bearing transport-error wraps
          (a CI lint could enforce it) (evidence: provider-secret-chain-hardening, 13 sites, 13/13 earned-green +
          477-test regression).
        - [TDD] when generalizing a behavior-preserving fix across many call sites, write ONE covering test per
          site (driving the REAL adapter / shared seam over a MockTransport raising the transport error), and run
          ALL of them RED first — RED-for-the-right-reason here means `__cause__` is the live transport error, which
          proves the test exercises the exact leak vector, not a stand-in (evidence: secret_chain_hardening).
        - [TDD] in a single suite, keep the must-not-regress INVARIANT guards (default-fallback, gating-unchanged)
          GREEN from the first run while only the NEW-behavior tests go RED — this cleanly separates "new capability"
          from "behavior-preserving" without a confusing all-red start (evidence: azure-ad-authority-config 2-red/3-green).
        - [DDD] a partially-wired seam can hide for a whole milestone: a config field consumed at one end
          (AzureADConfig.authority → _token_url) but never SOURCED at the other (resolve ignored settings) looks
          configurable but isn't. An END-TO-END test (settings → resolved config → minted URL) catches "looks wired,
          isn't" that a unit test on either end alone misses (evidence: azure-ad-authority-config).
        - [ADD] RE-CONFIRMED (v19 line + v22): ruff-format newly-authored test files BEFORE the tests→build snapshot
          (or declare the test surface in §5); a cosmetic format DURING build diverges the file from the tripwire
          md5 → `build_tampered`, whose blessed remediation is a clean tests→build re-cross, NOT editing the baseline
          (evidence: provider-secret-chain-hardening hit it once via post-snapshot ruff format, cleared by re-cross;
          azure-ad-authority-config pre-formatted and gated clean first try).
        - [ADD] calibrate the §5 `risk:` level to ACTUAL reversibility/blast, not the topic: a behavior-preserving
          SECURITY REMEDIATION (no new finding — verify CONFIRMS a fix, not discovers a problem) with full regression
          is auto-gateable at `risk: medium`; `risk: high` + `autonomy: auto` trips the engine `unguarded_high_risk_auto`
          guard (a high-risk gate must be human-owned). Over-flagging blocks the auto loop on a change that the
          project bar (v21 azure-aad-auth/azure-embeddings, unlabelled) already auto-gated after remediation. Record
          the calibration transparently in the header comment; the human may still override to conservative/manual
          (evidence: provider-secret-chain-hardening — high+auto refused, re-calibrated to medium with a transparent note).
        - [ADD] a systemic finding surfaced inside one task's verify that spans MULTIPLE frozen contracts becomes its
          OWN milestone (a cross-cutting sweep), never a retro-edit of the originating task's frozen contract
          (evidence: v21 azure-embeddings finding → v22 provider-secret-chain-hardening, 8 files).
Git: `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer; message
        drafted in `tmp/*.txt`, committed via `git commit -F`; scopes: gateway,
        dashboard, infra, docs, pipeline, config
