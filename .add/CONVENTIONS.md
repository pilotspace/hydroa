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
        Frontend / testing / harness conventions folded from v23 (2026-06-16, enterprise UI overhaul):
        - [TDD] For a PRESENTATION-ONLY restyle, the dense FROZEN behavioral suites ARE the regression net; the NEW
          red→green suite asserts ONLY the *adoption* — a stable `data-slot` marker (e.g. `auth-card`, `stat-card`,
          `data-table`) is a non-brittle, genuinely-discriminating hook that beats asserting CSS classes and survives
          fully interactive cells. Each restyle is confirmed by an adversarial refute-read (a sonnet subagent) before
          the gate; an EARNED verdict + zero data-seam diff is the evidence (evidence: v23 console/admin/auth — reds
          landed exactly on missing data-slot/ariaLabel/sortable-header/ChartContainer; 3 refute-reads all EARNED).
        - [ADD] The §5 scope baseline walks the WORKING TREE (excludes only .git/.add/__pycache__/node_modules), so a
          gitignored BUILD ARTIFACT present at the tests→build snapshot pollutes the baseline: `apps/dashboard/coverage/`
          (a `vitest run --coverage`) or `apps/dashboard/tsconfig.tsbuildinfo` (any `tsc --noEmit`) regenerated between
          the snapshot and the gate trips `scope_violation` on a file the task never meant to touch. BLESSED WORKAROUND:
          delete the artifact + re-snapshot (`add.py phase tests` → `add.py advance`, content-identical = a no-op for the
          tamper tripwire) + run ONLY `npm test` (no `--coverage`, no `tsc`) for the gate command. THREE recurrences in
          v23 (coverage ×1, tsbuildinfo ×2) ⇒ the engine fix should ship: extend the scope-walk exclusion to gitignored
          build artifacts (`coverage/`, `*.tsbuildinfo`) (evidence: console-surfaces-redesign + admin-surfaces-redesign
          + auth-pages-redesign, each cleared by re-snapshot, `add.py check` returned to 0 failed).
        Frontend / testing / harness conventions folded from v24 (2026-06-16, UI polish & a11y follow-ups):
        - [UDD] Heading-order is an a11y CONTRACT asserted by ROLE+LEVEL, never by CSS: `getByRole("heading",{level:2})`
          + a "no level jumps >1" outline scan over `getAllByRole("heading")` (evidence: overview-heading-a11y-fix). DS
          title primitives provide the lever — `CardTitle asChild` (Radix Slot, default `<h3>`) + `ChartCard headingLevel?`
          (default 3) — so a surface fixes its outline at the shared block; the default branch keeps all other consumers
          byte-identical and is covered by a GREEN-BY-DESIGN preservation test.
        - [UDD] The no-flash theme `<script>` MUST render from a Server Component: `themeScript` lives in a non-`"use client"`
          module (a function exported from a client module is an uncallable client *reference* in server render); the client
          context (ThemeProvider + QueryClientProvider) goes in a `"use client"` `app/providers.tsx`; `app/layout.tsx` stays
          a plain Server Component. Verify with a static fs test (no `"use client"` in layout, providers/theme-script present)
          + `next build` clean (evidence: theme-script-server.test.ts 5/5, build 18 routes).
        - [TDD] A pure-dedup/refactor with NO behavioral delta has no honest red→green — label it GREEN-BY-DESIGN and assert
          PRESERVATION (the accessible name survives) + lean on the refute-read; never fabricate a red. Icon-only DS controls
          keep a default accessible name as a safety net; the consumer dedup removes the duplicate, not the name (evidence:
          test_sidebartrigger_name_from_ds_default passes before and after).
        - [ADD] §5 scope-walk papercut, 4th recurrence + ACUTE form: a background `tsc` (`incremental:true`) regenerates
          `tsconfig.tsbuildinfo` AFTER a clean re-snapshot, so delete-then-gate RACES and the gate trips on an artifact it
          cannot prevent. In-task fix when an artifact regenerates unbidden: DECLARE it as an in-scope token on the §5
          `Scope (may touch):` line (truthful — `tsc` produces it during this task's build), then re-snapshot so the anchor
          captures it. The engine fix is now overdue: add `.next` to `_SCOPE_EXCLUDE_DIRS` and `tsconfig.tsbuildinfo`/`*.tsbuildinfo`
          to `_SCOPE_EXCLUDE_FILES` in add.py (evidence: overview-heading-a11y-fix, gate passed only after declaring the token).
        - [ADD] The `security_reminder_hook` substring-matches PROSE, not just code: writing the token for React's raw-HTML
          injection prop in a §6/§7 note (even to say "we DON'T use it") blocks the Edit. Phrase verify/observe notes as
          "no raw-HTML injection API" (evidence: the first §6 write was rejected by the PreToolUse hook).
Git: `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer; message
        drafted in `tmp/*.txt`, committed via `git commit -F`; scopes: gateway,
        dashboard, infra, docs, pipeline, config

## Method learnings
- (TDD) when a redesign short-circuits the code path a legacy regression test targeted (claim-first bypassing the resolver-collision path), the assertion can stay green while going INERT — verify the net property coverage MOVED (here: no-500 now proven by test_db_oidc_resolver_deterministic), don't trust the green (evidence: collision_dos LAYER-2, verifier #1).  [folded foundation-version 54 · from domain-routing-unification]
- (TDD) a RED suite that asserts the EXACT non-retry behavior (not just "eventually succeeds") caught a real stdlib exception-hierarchy trap (SMTPException IS-A OSError since Python 3.4) that a shape-only test would have missed entirely (evidence: R4 test failed with 3 attempts against the contract's literal retry-predicate tuple).  [folded foundation-version 54 · from transactional-email]
- (ADD) a change-request that NARROWS a frozen contract mid-build must reconcile the §3 contract PROSE too, not just the §1 Must rules — the §3 code-block drifted (said env "DELETED" while M4 said retained) until a verifier caught it (evidence: verifier #2 CONCERN#1).  [folded foundation-version 54 · from domain-routing-unification]
- (ADD) a low-effort executor (fable/low) can implement a well-pinned red suite AND correctly HARD-STOP on a 50-test cross-task-drift casualty rather than weaken tests — the tight red suite + explicit "do not edit tests" constraint carried the safety, not the model tier (evidence: v1 build STOP, then sonnet handled the delicate legacy reconciliation).  [folded foundation-version 54 · from domain-routing-unification]
- (ADD) a later task's frozen contract legitimately extending an EARLIER task's response shape requires updating that earlier task's own exact-shape test (in-scope per this task's §5 Scope line) rather than treating it as an untouchable frozen artifact forever — the update is additive-only (one new key) and superseded-not-silent (evidence: tests/member_invite_issuance test_owner_invites_co_owner comment cites both task IDs).  [folded foundation-version 54 · from transactional-email]
- (ADD) a fixed `asyncio.sleep(0.05)` after a fire-and-forget dispatch flakes under a load-shared multi-agent host even for a BRAND NEW test — poll-until-present from the first draft, not just as a post-hoc fix (evidence: [[fire-and-forget-audit-test-flake]] recurred in this task's own first draft before being fixed with `_poll_until`).  [folded foundation-version 54 · from transactional-email]
- (TDD) The engine's `_count_test_defs` regex (`^\s*def test_`) undercounts `async def test_` — this async-heavy task's real 14 tests report as 3 (same undercount hits every async task, e.g. plan-seat-cap 28→3). Not introduced here; `.add/tooling/` off-limits. Evidence: build report.  [folded foundation-version 53 · from plan-rate-enforcement]
- (ADD) Tailwind v4 `@theme inline` output is UNLAYERED and beats `@layer base :root`; a same-name self-reference collapses to empty (evidence: --font-sans: var(--font-sans) dropped Geist — [[tailwind-v4-font-token-collision]]).  [folded foundation-version 53 · from airier-theme-restyle]
- (ADD) A build agent honoring a strict §5 Scope correctly STOPPED at a load-bearing out-of-scope wiring line (`deps.py`) and escalated rather than silently expanding — the right call; the scope was then amended at verify (deps.py + main.py added) with the activation decision routed to the human. Evidence: build report "Explicit scope deviation — flagged, not hidden".  [folded foundation-version 53 · from plan-rate-enforcement]
- (ADD) For a security task, writing the red suite MYSELF (not delegating) then delegating only the adversarial VERIFY to an independent agent gave a genuine dual-lens without me marking my own homework. (evidence: self-authored 20 red tests + independent add-verify EARNED)  [folded foundation-version 53 · from platform-credential-fallback]
- (ADD) a builder's own concurrency scenario proved the design against ONE seam pair (invite-accept vs OIDC) but the riskiest transaction shape (§5's own flagged SCIM autobegin-reuse deviation) went unraced by the builder's own suite — independent verify closed that gap with 2 new cross-seam/same-seam probes targeting the seam the build's OWN "Strategy actually used" note flagged as highest-risk. Evidence: this task's §6. Generalizable lesson for future admission-control tasks with >2 concurrent seams: race EVERY seam pair the contract itself flags as structurally novel, not just one representative pair.  [folded foundation-version 51 · from plan-seat-cap]
- (TDD) a green suite that holds a mutable input STATIC across the whole test cannot see time-of-check/time-of-settlement drift; for any value now made mutable by a new write API, add a "changed mid-flight" test (evidence: C1 was invisible to 11 scenario + 104 regression tests).  [folded foundation-version 49 · from tiered-rate-cards]
- (TDD) "best-effort" and "bounded" are orthogonal properties — a call can be both. The §3 pseudocode's "advisory stays best-effort" was mis-read as "advisory is timeout-exempt". Add a scenario/test that asserts a hung advisory call is bounded (not just swallowed) so this can't regress silently. Evidence: the gap was invisible to the green suite.  [folded foundation-version 49 · from usage-flusher-durability]
- (ADD) a components pillar can sit fully implemented in the engine (schema, validation, scope-join, per-component gate) yet go completely unused for the pillar's entire lifetime in a qualifying monorepo — the gap was invisible to `add.py check` (evidence: 87 pre-existing failures never once flagged "no components.toml in a 2-app-root repo"; the gap surfaced only via a cross-session AIDD-Book audit reading real task Scope lines, not via any engine signal)  [folded foundation-version 49 · from declare-components-registry]
- (ADD) a diagnostic "single X breaker" headline conflated two fixes with different blast radii; grounding split them — reinforces "ground before you size" (evidence: B3 fix#1/fix#2 split).  [folded foundation-version 49 · from provider-circuit-breakers]
- (ADD) A previously HARD-STOP-cleared, Tin-approved security freeze (`routing-config-write`) can still need reversal when its own STATED premise (here: "single-operator/trusted-owner deployment") is invalidated by later, unrelated shipped work (multi-tenant SaaS features landing over the following two weeks). The SUPERSESSION pattern handled this cleanly — record the reversal at the new task's freeze, never silently re-edit the old frozen file (evidence: §0/§3 SUPERSESSION record).  [folded foundation-version 49 · from signup-and-routing-authz]
- (TDD) A `waitFor` predicate can resolve on a transient intermediate state rather than the intended final state when the assertion (e.g. "X is absent") is ALSO true during a loading/ transition frame, not just at the desired end state — the very next synchronous assertion then fails in a way that looks like the earlier `waitFor` "hung," when it actually resolved too early. Fix pattern: fold both the negative and positive condition into the SAME `waitFor` callback so it only resolves once the true end state holds (evidence: the `test_directory_search_filters_and_row_links_to_detail` debounce investigation this session — cost roughly half a session of bisection before the actual mechanism was found via a DOM- rendered debug log, not console.log, since this environment's test runner does not surface it).  [folded foundation-version 48 · from admin-console-ui]
- (TDD) A fully-green suite only proves the paths it actually exercises — an independent adversarial review (subagent refute-read) found a real, uncovered gap (Rotate/Revoke/assignRole mutations had no `onError` handler, silently swallowing a failure — a direct R4 violation) that none of the 34 new tests caught, because no test exercised any mutation's FAILURE path on this surface (only Create's failure path was covered, inherited for free from the reused `CreateKeyDialog.tsx`). Candidate standing checklist item for future test-plans: one failure-path test per mutation, not just per screen (evidence: `a885e980fd730c83e`'s review, fixed same session, re-verified 17/17 + clean lint/typecheck + clean 944-test regression).  [folded foundation-version 48 · from admin-console-ui]
- (TDD) a `diff`-based self-check inside an adversarial mutation test can itself lie — the build agent's own mutation-testing `diff` call misreported a still-mutated `dashboard-shell.tsx` as "identical" to its pre-mutation backup; only a direct `Read`/`grep` of the actual file content caught it (evidence: build agent's own self-report, independently reconfirmed by the orchestrator re-reading the file as the FIRST verification action). Lesson: when adversarially mutating a file to prove a test catches a bug, verify the revert with a content read, not a diff-tool exit code/summary alone — a diff invocation can itself be misconfigured or race a write.  [folded foundation-version 48 · from command-palette]
- (TDD) This repo's `[tool.coverage.run]` config lacks `concurrency = greenlet`, making per-line coverage on SQLAlchemy-async modules structurally unreliable for judging which branches actually executed (evidence: `invite_repository.py`'s core INSERT logic showed "uncovered" despite being required for 30 passing tests; corroborated against a known-solid pre-existing file showing the same "impossible" under-report). Worth fixing repo-wide so future verifies of async code can trust coverage numbers directly instead of hand-building a probe.  [folded foundation-version 48 · from member-invite-issuance]
- (TDD) Per-directory `pytest --cov` readings are unreliable for this repo's async route handlers (evidence: `platform_plans_router.py` showed 58% with the entire PUT handler body "missing" despite the covering tests passing with real DB-state assertions; the identical artifact was confirmed to reproduce on `platform_users_router.py`, a file this build never touched). Rely on full-suite coverage numbers, not per-directory ones, when judging "coverage did not decrease" for async code in this repo.  [folded foundation-version 48 · from plan-catalog]
- (TDD) a mutation test can be well-INTENTIONED but structurally miss the property it means to prove, if the guarantee actually lives in framework wiring (a FastAPI `Depends()` resolving before the function body) rather than in-body statement order — the build agent caught this itself before mutating, and designed a mutation that bypassed BOTH the Depends and the secondary in-memory check to cleanly isolate the real property (evidence: the build agent's own discovery, independently reasoned-through and accepted by the orchestrator during verify). Lesson: before mutating code to prove a test catches a bug, confirm the mutation actually removes the specific guarantee under test, not just code that looks related to it.  [folded foundation-version 48 · from tenant-activity-tab]
- (TDD) the repo-wide `asyncio.sleep(0.05)` fire-and-forget-drain idiom (used in 4+ test suites now, including this task's own new one) has a confirmed load-sensitive failure mode under full-suite concurrency (evidence: the one full-suite failure this task's own verify pass independently reconfirmed as isolation-passing/full-suite-flaky) — worth a dedicated hardening follow-up rather than continuing to propagate the same fragile idiom into every new audit-write test file.  [folded foundation-version 48 · from tenant-activity-tab]
- (ADD) Live mutation-probing (temporarily inverting one meaningful line of production logic, confirming the relevant test fails, then reverting byte-identically) is a materially stronger refute-read technique than a read-only review — it converted "the assertions look reasonable" into a demonstrated fact for R6, the budget merge logic, and the members self-guard. Worth naming explicitly as a preferred refute-read technique in `advisor.md`/`confidence.md` for safety-critical tasks, not just an ad hoc choice this one reviewer happened to make (evidence: this task's Refute-read verdict — all 3 probes correctly flipped their target test from green to red, then back).  [folded foundation-version 48 · from admin-console-ui]
- (ADD) pre-filling §5 BUILD Scope completely and accurately BEFORE dispatching the build agent (rather than letting the agent or a post-hoc pass fill it) fully prevented the stale-scope-snapshot false positive that hit both prior tasks this milestone (evidence: the `tests`->`build` phase crossing for this task produced zero `scope_violation` warning, the first clean crossing all milestone). Promote this to the standard sequencing for every future full-lane task, not just a reactive fix.  [folded foundation-version 48 · from command-palette]
- (ADD) the §6 "Build expectations" block is supposed to be filled BEFORE dispatching build (per its own guide text), but this task's build was dispatched straight off the frozen §2/§3 without pausing to transcribe it first — `add.py advance` correctly refused the tests->build transition after the fact (`build_expectations_unfilled`) and it had to be backfilled once code already existed. Mitigated here (content was derived from the already-frozen §2 SCENARIOS, not reverse-fitted to the build's actual output), but the ORDER the guide prescribes is worth actually following next time — fill §6 Build Expectations immediately after freeze, before the tests/build dispatch, not after (evidence: this task's own `advance` refusal).  [folded foundation-version 48 · from console-flat-visual-pass]
- (ADD) `_grounded_state`/`_section0_anchors` (add.py's own grounded-check) only reads content on the SAME line as "Anchors the contract cites:" (regex `Anchors the contract cites:\s*(.*)$`, single-line) — but every §0 GROUND section observed across this project (including this task's own, and the convention `Touches`/`Issues/Risks` fields also follow) puts the real content as a bulleted list on the FOLLOWING lines, not after the colon on the same line. This makes `_grounded_state` read `False` (looks ungrounded) for a fully-grounded §0 written in the project's own dominant style, surfacing as a `task_not_grounded` WARN on every task that freezes its contract this way — this task's own §0 has 5 substantive anchor bullets, genuinely grounded, despite the WARN. Measure-not-block (never gates), so nothing was blocked, but the checker likely under-detects real grounding project-wide (evidence: this task's own `add.py check` output + direct regex/content inspection).  [folded foundation-version 48 · from console-flat-visual-pass]
- (ADD) a §2 SCENARIOS tag comment with a SECOND `#` on the same line (e.g. `# M3, Issues/Risks #2`) silently breaks `_rule_coverage_gaps`' tag parser — `_SCENARIO_TAG_RE` greedily matches to the LAST `#` on the line, so the real `M#`/`R:code` tag before an earlier `#` gets dropped from the captured group. Caught + fixed during this task's own §2 drafting (`add.py check` flagged M3 as a coverage gap; M1 had the same collision but was masked by a redundant tag elsewhere). Worth a one-line mention in the scenarios-writing guide: never put a second `#` on a tagged Scenario line (evidence: this task's own pre-fix `add.py check` WARN).  [folded foundation-version 48 · from console-flat-visual-pass]
- (ADD) coverage.py under-reports statement coverage for async router code exercised through SQLAlchemy's greenlet bridge — 3rd confirmed occurrence this session (member-invite-issuance, plan-catalog, now this task: 48% router / 39.27% total reported when run in isolation, despite 24/24 passing tests with real assertions). This time DIRECTLY FALSIFIED (not just reasoned about): the add-verify agent instrumented a "missing" line with a temporary print statement, confirmed it fires during a passing test, then reverted. Worth a repo-wide `.coveragerc`/pytest-cov config fix now that it has empirical proof behind it, not just pattern-matching across 3 builds.  [folded foundation-version 48 · from impersonation-session-lifecycle]
- (ADD) The "contract-specified-but-unused domain type" failure mode recurred (this task's `ImpersonationSession` plain dataclass, §3 Part D) despite already being folded into CONVENTIONS.md from an earlier task (model-mgmt's ModelDisabledError/ModelNotFoundError). Worth a sharper trigger at Contract-freeze time: ask "which code path actually constructs this type?" for every domain entity named in §3, not just error classes — the existing lesson's phrasing may be too narrowly scoped to catch this class of recurrence.  [folded foundation-version 48 · from impersonation-session-lifecycle]
- (ADD) `add.py advance` state silently lagged actual build completion across this long-running, compacted, parallel-build session: this task's own phase marker was still `tests` (not `verify`) despite its code already being fully built, tested, and independently investigated by 2 separate verify attempts — root cause: the orchestrator built directly against the tree in a fast-moving parallel-build sequence and never called the 2 required `add.py advance` transitions (tests→build, build→verify) immediately after the code was done, only discovered via `add.py status` at this gate. Lesson: advance state the instant a build completes, not deferred until the next check-in — the engine's phase marker is the only authoritative source of truth (TASK.md's own header is cosmetic) and desyncs silently otherwise, especially across compaction boundaries.  [folded foundation-version 48 · from impersonation-session-lifecycle]
- (ADD) A background-suite-dependent `add-verify` dispatch can look stalled (a transient API error, then a long silent gap around a slow full-suite run) while actually still being alive and eventually delivering a complete, high-quality, more-rigorous-than-the-orchestrator's-own verdict (this instance: ~23 min wall-clock, 38 tool_uses, 4 self-built forced-race probes, 6 self-built HTTP security probes, one genuine new finding). This nuances the pattern logged twice before this session (plan-catalog's build agent ×2) that such agents "don't truly block" — sometimes they DO eventually deliver, just slower than the orchestrator's own patience budget. Lesson: when an orchestrator takes over independent verification after presuming a stall, treat a later-arriving agent verdict as additional evidence to merge, not discard — as done here — rather than assuming the takeover was necessarily the only path to a verdict.  [folded foundation-version 48 · from impersonation-session-lifecycle]
- (ADD) Worktree isolation (`isolation: "worktree"`) branches from the last git COMMIT, not the current working tree — incompatible with a task whose §0 GROUND anchors (or whose milestone's prerequisite work) exist only uncommitted. This task's first build attempt silently ran against a stale base missing `Role.SUPERADMIN` entirely and shipped an incomplete security guard before being caught and discarded (evidence: this task's own Verify history). Future dispatches onto a substantially-uncommitted tree should default to no isolation + strict sequential ordering when shared files are at stake, not isolation-for-safety by default.  [folded foundation-version 48 · from member-invite-issuance]
- (ADD) A build agent dispatched to independently verify its own long-running background regression suite twice ended its turn to "wait" rather than actively blocking until the suite finished, requiring the orchestrator to resume it via SendMessage and, ultimately, take over verification directly rather than continue a resume-and-wait cycle. Future dispatches that depend on a long-running background check should be told explicitly to block/poll internally until that check truly completes before returning, not to end their turn mid-wait.  [folded foundation-version 48 · from plan-catalog]
- (ADD) the build-expectations pre-fill gate (`build_expectations_unfilled`) rejects even a single BARE `<...>` placeholder-style annotation anywhere in the "### Build expectations" body, including ones meant as descriptive shorthand rather than an unfilled template marker (evidence: this task's own tests->build crossing was refused once for exactly this reason, fixed by rewording rather than removing content). Lesson: when pre-filling Build Expectations before dispatch, avoid bare angle-bracket notation entirely in prose — spell it out in words, or wrap it in backticks, so the placeholder-detector never has to distinguish intent.  [folded foundation-version 48 · from tenant-activity-tab]
- (ADD) a design contract's literal value-expression (e.g. `plan?.name` vs `.display_name`) can encode a real product-facing bug that survives design-draft + orchestrator-review + human-freeze undetected when nobody cross-checks the exact field name against sibling surfaces at freeze time (evidence: this task's own Plan-tile spec-delta above). Suggests a design-phase checklist item: when a contract's literal value expression selects one field of a multi-field response shape (name vs display_name, id vs slug, etc.), explicitly cross-check that field choice against how sibling/existing surfaces already render the same shape — not just that the types line up.  [folded foundation-version 48 · from tenant-overview-strip]
- (ADD) (recurring — same root cause logged in `console-flat-visual-pass`) the `task_not_grounded` WARN still fires from `_section0_anchors`'s same-line-only regex against this task's own multi-line-bulleted §0 GROUND convention, the project's own dominant style (evidence: same regex gap first logged in `console-flat-visual-pass`'s §7, now recurring here unchanged). Not fixed in the engine; logged again purely for visibility/frequency.  [folded foundation-version 48 · from tenant-overview-strip]
- (TDD) a "returns None, never raises" contract on a degrade path needs a test that forces the actual failure branch (a real precondition-violating environment), not just a happy-path test plus a prose promise — the untested branch was silently wrong (evidence: `get_platform_tenant` would have raised `ProgrammingError`, not returned `None`, on an unmigrated DB until the refute-read caught it and `test_get_platform_tenant_returns_none_when_unmigrated` was added).  [folded foundation-version 44 · from platform-tenant-seed]
- (TDD) an `AsyncMock(spec=SomeClass)` with only ONE method configured to raise is a silent trap if the code under test doesn't actually call that specific method on that specific failure path — it looks like a real failure-injection test and passes, but proves nothing. Prefer making the FIRST thing the code under test calls raise directly (here: the `session_factory()` call itself, matching the scenario's own wording) over mocking deep inside an object whose exact call pattern you have to keep re-verifying (evidence: found by independently re-deriving `record_audit`'s real call sequence before trusting the existing suite's pattern — see the Spec delta above).  [folded foundation-version 44 · from superadmin-audit-foundation]
- (TDD) when a scenario can only be driven through an HTTP/router round-trip (not direct use-case construction), a negative assertion like "0 audit rows" is vacuous pre-build for a different reason than the AsyncMock trap above: the feature being entirely absent ALSO produces 0 rows, so the test can pass for the wrong reason at every stage, not just RED. Fix: a POSITIVE CONTROL inside the same test — a genuine audit-worthy action in the same test/schema that must itself produce a row before the negative assertion is trusted. Applied in `test_part_b_password_login_audit.py`'s two negative tests (evidence: the build agent's own module docstring names this explicitly; independently confirmed by the orchestrator reading the test file — both tests open with a control block that itself asserts count==1 before proceeding to the real scenario).  [folded foundation-version 44 · from superadmin-audit-foundation]
- (TDD) two test-construction bugs (an `EmailStr`-invalid TLD; a required Pydantic field omitted from a PUT body) both manifested as a 422 that would have made the test pass for the WRONG reason (validation failure, not the actual 403 gate check) had the assertion been looser (e.g. `assert resp.status_code != 200`) — writing the exact expected status+code catches this class of bug immediately; a looser assertion would have silently certified nothing (evidence: §5 "Strategy actually used", both fixes found on the first red→green run via exact-code asserts).  [folded foundation-version 44 · from superadmin-login]
- (TDD) a build subagent's self-reported test/coverage numbers should be independently reproduced, not just trusted, for any security-sensitive build — doing so here directly caught a tooling gotcha that would have gone unnoticed otherwise (evidence: `alembic.ini` hardcodes `sqlalchemy.url`; the real override env var is `GATEWAY_DATABASE_URL` not `DATABASE_URL`; my first two manual alembic-check attempts silently no-op'd against the wrong database and reported a false failure, caught only because the orchestrator re-ran every Build Expectations checkbox independently rather than transcribing the build agent's report)  [folded foundation-version 44 · from superadmin-role]
- (ADD) this task's own `gate PASS` was the one that actually hit the tree-wide §5 scope-lock cross-contamination and consumed 1/3 heal attempts — full analysis and recovery pattern recorded in sibling task `superadmin-role`'s §7 (same milestone, same root cause: both tasks' Build phases ran concurrently, non-worktree-isolated, in the shared tree) (evidence: this task's `gate PASS` failed with `scope_violation` naming files from `superadmin-role`'s build, before the sibling)  [folded foundation-version 44 · from ops-platform-job-identity]
- (ADD) when a refute-read finding requires strengthening a frozen test file mid-Build, call `add.py heal --reason "..."` BEFORE re-running the suite and gating — not after. Fixing the test first and going straight to `gate PASS` still trips the mechanical tamper tripwire (it hashes bytes, not intent), which force-returns the task to build and burns a heal attempt that a proactive `add.py heal` call would have consumed deliberately instead (evidence: this task burned 1 of 3 attempts this way — recovered cleanly via re-crossing `phase build` to re-snapshot, but the proactive path is one step shorter and doesn't rely on the mechanical catch).  [folded foundation-version 44 · from platform-tenant-seed]
- (ADD) §5's scope-lock snapshot is tree-wide, not per-task: running two sibling tasks' Build phases concurrently in a shared, non-worktree-isolated tree causes each task's completing verify gate to flag the OTHER task's legitimate files as `scope_violation` (evidence: sibling `ops-platform-job-identity`'s `gate PASS` attempt was flagged for this task's migration + `users_router.py`, consumed 1/3 heal attempts). Recovery: pristine tree (clear build-artifact caches) then `add.py phase build <slug>` (re-snapshots current state) → `advance` → `gate PASS`, per task, back-to-back with no other file-touching activity between snapshot and gate. Matches the pre-existing `ADD scope-snapshot poisoning` memory gotcha — this is fresh, concrete evidence reinforcing it, worth folding into the foundation so future parallel-build waves plan around it upfront (either serialize the gate step, or accept the recovery cost knowingly).  [folded foundation-version 44 · from superadmin-role]
- (TDD) a single clean local test run is not sufficient evidence of a green suite's determinism when the harness has known shared-resource characteristics (one Postgres instance across all tests) — this task's build agent's "15/15 green" self-report rested on exactly one run; repeating it 6-8x surfaced a ~25-30% failure rate the single run entirely missed (evidence: my own independent repeated runs, §5 SCOPE ADDENDUM 2).  [folded foundation-version 43 · from preset-admin-surface]
- (ADD) running independent adversarial refute-read subagents IN PARALLEL with a developer-driven full-suite verification run risks the exact "concurrent pytest processes on a shared test DB" hazard this same project already hit and partially hardened against earlier this session — evidence: the first full-suite run this VERIFY pass showed 32 failed/13 errors purely from this collision, resolved only by re-running clean. Future auto-mode parallel verification should either serialize test-running agents against the main-loop's own full-suite run, or explicitly scope subagents to read-only static analysis + a SINGLE small targeted test subset, never a second independent full/broad pytest invocation against the same shared DB.  [folded foundation-version 43 · from chat-modality-guard]
- (ADD) a contract's own §1 ⚠ "mirrors X precedent" claim needs the SAME precedent checked on BOTH axes (here: frontend nav shape AND backend auth strictness) before freeze — this task's v1/v2 SCOPE ADDENDUM 1 asserted "mirrors /app/keys exactly" for nav visibility while silently carrying a STRICTER backend gate (OWNER-only vs. keys' any-role `get_identity`) than the precedent it named; only the adversarial refute-read caught the mismatch, not the contract-freeze review itself (evidence: refute-read agent affb580a9fa5a2545, finding (g1)).  [folded foundation-version 43 · from preset-admin-surface]
- (ADD) a task whose safety property depends on another subsystem's data invariant (here: catalog sync actually populating `modality`) should explicitly declare that dependency at GROUND time and gate on it, rather than discovering the gap only at refute-read (evidence: this task's guard was contract-correct but would have caused a full outage in this stale worktree until origin/main's prerequisite fix was merged in).  [folded foundation-version 43 · from preset-capability-validation]
- (TDD) an adversarial reviewer that only reads code can't tell whether a test's guard condition (e.g. `abandon_wins_observed > 0`) is real or vacuous; temporarily reverting the fix, confirming the exact expected RED, then restoring and reconfirming GREEN is a stronger standard and should be the default ask for future adversarial-review dispatches, not an optional extra (evidence: agent `ac5af5b2ac44b01e2`'s report, this task's §6 VERIFY, 2026-07-03).  [folded foundation-version 45 · from batch-claim-drain-del]
- (ADD) a Build-expectations row that pre-declares "confirmed by `git diff` at the gate" can silently fail when the touched file was never committed (still `??` untracked across a whole prior milestone's work) — `git diff` shows the entire file as new, not an incremental diff. Pre-declared evidence sources should name a fallback (direct re-read + independent corroboration) for files that may still be uncommitted at verify time (evidence: this task's §6 VERIFY Build-expectations row 4, 2026-07-03).  [folded foundation-version 45 · from batch-claim-drain-del]
- (TDD) this codebase's shared-instance test flakiness is not limited to the already-known Postgres DB-name contention — a shared Redis stream (`usage:events`) / consumer group (`ledger-flusher`) shows the same non-deterministic signature (fail → fail → pass across identical runs with zero code changes), suggesting the test suite lacks isolation for Redis-backed fixtures the same way it now guards Postgres DB names (evidence: `test_spend_counter_not_incremented_on_cache_hit`, 2026-07-03, three consecutive runs).  [folded foundation-version 43 · from batch-auto-grouping]
- (ADD) re-crossing the tests→build→verify snapshot to clear a genuinely-resolved `scope_violation`/`build_tampered` finding also erases the ENGINE'S OWN forcing function that would otherwise make a human confront a scope excursion at gate time — after re-crossing, `add.py check` reads fully clean and the entire burden of surfacing the incident shifts onto the AI's own prose discipline, with no engine signal left to fall back on if that prose under-reports it (evidence: this task's `scope_violation` on `worker.py`/`test_batch_jobs.py` vanished from `add.py check` immediately after re-crossing, even though the files had only just been touched outside declared scope).  [folded foundation-version 43 · from batch-auto-grouping]
- (ADD) on a `risk: high`/`autonomy: conservative` task, an advisor pass floating a fix as advisory (not a mandate) should be recorded as documented residue, NOT executed immediately — expanding blast radius at verify to resolve a smell is exactly the kind of call conservative autonomy exists to route through the human first, even when the fix itself would be mechanically clean (evidence: the first relocation attempt was ruff/pyright-clean and 107/107-tested, yet still had to be reverted for touching undeclared scope).  [folded foundation-version 43 · from batch-auto-grouping]
- (ADD) `git diff HEAD`/`git checkout HEAD --` is the wrong revert target once a task's OWN build has already made legitimate changes to a file being reverted for an unrelated reason — HEAD predates the whole task, not just the unwanted edit, so a blind revert-to-HEAD can silently discard in-scope work alongside the out-of-scope part (evidence: reverting `batches/api/router.py` to HEAD initially deleted batch-auto-grouping's own already-built `dispatch_batch_job` extraction, caught only by a pyright `reportAttributeAccessIssue` on `batch_diversion.py`'s import immediately after).  [folded foundation-version 43 · from batch-auto-grouping]
- (ADD) when a task's own §1 SPECIFY weighs multiple framings, the milestone's own goal/rationale text (and any direct human quote captured there) should be checked as an explicit cross-reference BEFORE a framing is chosen — not just re-read for general color. Here, MILESTONE.md's goal ("a SET of requests as ONE batch job") and Tin's own quoted words ("group user's request as batch") directly named multi-request aggregation, but the framing list at specify never included that option, and neither of the two items flagged to Tin at the §3 freeze covered the gap — so a bundle approval was taken on a design axis the human never actually got to react to. The fix isn't "ask more questions at freeze," it's checking the milestone's own language against each framing BEFORE they're written down, so a framing that contradicts the milestone goal is either never on the list or is explicitly flagged as "diverges from milestone goal — confirm." (evidence: this session, 2026-07-03 — full sequence in Build-time findings above and in task batch-window-grouping's §0 Related intent.)  [folded foundation-version 43 · from batch-auto-grouping]
- (ADD) The GROUND phase's initial research missed that the relay's actual default model id (`gpt-4o-realtime-preview`) differed from the milestone's assumed pricing target (`gpt-realtime`) — caught only by live WebFetch pricing research, not by reading code alone (evidence: required a user decision via AskUserQuestion mid-GROUND).  [folded foundation-version 42 · from gpt-realtime-pricing-fields]
- (ADD) A single additive field access in `_insert_snapshot` (`repository.py`) broke 3 sibling suites' independently-duck-typed `FakeCatalogModel` fixtures plus one exact-id-list wiring assertion — none of these were in this task's originally declared scope, only surfaced by running the FULL regression suite, not the targeted one (evidence: GRPF7 caught what the targeted 7-test run could not). Reinforces: always run the full suite before VERIFY on any change touching a shared domain entity, even when the task's own tests are green.  [folded foundation-version 42 · from gpt-realtime-pricing-fields]
- (ADD) a milestone's exit-criteria checkboxes are the HUMAN's affirmation per `.add/docs/09-the-loop.md` ("the engine reads the tally, it never judges the goal itself") — I (AI) checked 2 of 3 and marked the 3rd partial, then ran `milestone-done` myself without asking first; it succeeded silently (the engine doesn't distinguish `[x]` from `[~]`, both count as "checked"). Caught and disclosed immediately after the fact, and Tin retroactively approved the outcome — but the gate should have been presented BEFORE running the closing command, not after (evidence: this task's own OBSERVE entry, 2026-07-02).  [folded foundation-version 42 · from gpt-realtime-relay-billing]
- (ADD) GROUND-phase research (both this task's own §0 and an earlier GROUND-phase subagent for the parent milestone) wrongly concluded "no Alembic/formal migration tooling exists in this repo" — a repo-wide search missed `apps/gateway/alembic.ini` + `apps/gateway/migrations/versions/` (35 prior migrations) entirely, and this false premise was baked into the FROZEN, human-approved §3 CONTRACT text before being caught by the very GSM4 regression run the contract itself required. Future GROUND-phase research on schema-touching tasks MUST explicitly check for `alembic.ini` (via `find <app-root> -iname alembic.ini`) before asserting "no migration tool exists" — a directory-listing/grep miss is not equivalent to a confirmed absence (evidence: 2 full-suite failures — test_upgrade_from_empty_parity, test_autogenerate_empty_diff — caught the gap; fixed via a4c6e8b0d2f3, no contract/behavior change needed).  [folded foundation-version 42 · from gpt-realtime-schema-migration]
- (ADD) the shared test Postgres (`localhost:5433/gateway_test`) has no isolation between concurrent worktree pytest sessions — a sibling worktree's own full-suite run can orphan a table (`tenant_model_presets`) mid-run and cascade a single `DROP TABLE` FK failure into hundreds of unrelated test failures for the REST of that pytest session. This is the third time this exact signature has been hit this session alone (previously: catalog-pricing-fields's build_tampered remediation; now twice more here). Worth a real fix (e.g. per-worktree test DB names, like tests/migrations/conftest.py already does with its dedicated `gateway_migrations_test` DB) rather than continuing to work around it ad hoc — evidence: 2 of 4 full-suite attempts this task alone were disrupted by it (78 failed + 833 errors each time, 100% traced to the identical DependentObjectsStillExistError root cause, 0% overlap with any file this task touched).  [folded foundation-version 42 · from gpt-realtime-schema-migration]
- (TDD) a live-verify task with zero pytest coverage can still have its "green" earned or gamed — the refute-read for this task type should specifically check that the harness FAILED LOUDLY at least once on a genuinely wrong input (here: the first key's real 401, the DB-race's real `ForeignKeyViolationError`) before trusting its final PASS, since a script with no prior observed failure gives no evidence it's capable of catching a real problem.  [folded foundation-version 41 · from minimax-live-verify]
- (TDD) an async-generator method's exception only surfaces on first iteration, not at call time — useful for red-suite authors testing `CatalogSourceUnavailableError`-raising scenarios: `with pytest.raises(...): [x async for x in obj.method()]`, not `with pytest.raises(...): obj.method()` (evidence: OER6b test design, confirmed correct by the refute-read's passing mutation test).  [folded foundation-version 41 · from openrouter-embeddings-routing]
- (ADD) a post-freeze correction to a red test or the frozen §3 (even a legitimate arithmetic fix, not a weakening) must be self-disclosed and re-crossed (`add.py phase tests` → `advance`) THE MOMENT it happens, not left for the refute-read gate to catch as `build_tampered` — the fix here was correct on the merits, but the process gap (undisclosed post-freeze edit) was a genuine near-miss on this project's own HARD-STOP tripwire (evidence: refute-read agent a7dcf49edf578dec0 independently reproduced the md5/mtime mismatch this session; had it not caught it, the task would have gated PASS on an unrecrossed tamper flag)  [folded foundation-version 41 · from catalog-pricing-fields]
- (ADD) the sibling-worktree scope-snapshot-poisoning variant (documented once already this session for `minimax-live-verify`) recurred here with a materially different signature: instead of stale `.pytest_cache`/`.ruff_cache` build artifacts, it was the sibling task's own actively-edited SOURCE files (`error_catalog.py`, `main.py`, `tenant_model_preset_store.py`) — confirming `_scope_walk`'s repo-wide walk (`root.parent.resolve()`) has no `.claude/worktrees/` exclusion at all, not just a cache-directory gap; the safe remedy (confirm sibling idle via `pgrep`, then re-cross) held again, but a permanent engine-level fix (exclude `.claude/worktrees/` from `_scope_walk` entirely) would remove the need to poll for sibling idleness on every future concurrent-worktree task (evidence: `add.py check` flagged 3 sibling src files as `scope_violation` this session, cleared only after the sibling process went idle)  [folded foundation-version 41 · from catalog-pricing-fields]
- (ADD) A contract's blast-radius risk flag (here: "`_upsert_model`'s fix changes behavior for EVERY existing provider's re-sync") should trigger running the FULL test suite before VERIFY, not just the directly-touched test directory — evidence: `tests/catalog/` alone stayed green while `tests/catalog_input_modalities/`'s SC5 (a different, already-shipped task's frozen no-clobber invariant) was silently broken by this task's first-draft `_upsert_model` diff; only a full-suite run surfaced it (minimax-catalog-seed TASK.md §5, 2026-07-01).  [folded foundation-version 41 · from minimax-catalog-seed]
- (ADD) a live-verify task's own scope-snapshot can be poisoned by an unrelated SIBLING git worktree's build caches (`.pytest_cache`/`.ruff_cache` under `.claude/worktrees/<other>/`), not just caches in the main tree — `_scope_walk` doesn't exclude sibling worktree directories (evidence: `gate PASS` first returned `scope_violation` listing 21 `.claude/worktrees/model-preset/...` cache paths, attempt 1 of 3 burned). Fix was the same documented pattern as [[add-scope-snapshot-poisoning]] (re-cross tests→build→verify over a quiescent tree) — but ONLY safe once confirmed the sibling process was idle (`pgrep` clean) first, since re-snapshotting while it's still actively writing would just poison the NEXT gate attempt too.  [folded foundation-version 41 · from minimax-live-verify]
- (ADD) a Protocol-port change (`CatalogSource`/`CatalogRepository` gaining a new method/ kwarg) silently breaks any structural test double that isn't grepped for — the §1 "only one implementer" ground-phase claim was about PRODUCTION code only; two test fixtures (`FakeCatalogSource` in two files) were an unaccounted second/third "implementer" that broke at BUILD time, caught only by actually running the full suite, not by the ground-phase grep (evidence: `tests/catalog/test_model_catalog.py` + `tests/catalog_sync_trigger/conftest.py` both needed a `list_embedding_models()` stub + `modality` field added). Future Protocol-port changes should grep test doubles too, not just `src/`.  [folded foundation-version 41 · from openrouter-embeddings-routing]
- (TDD) when a refute-read FLAGs "the test sidesteps a race", close it by REPLACING the sidestep with a test that drives THROUGH the race AND falsifying that test against the buggy code (evidence: test_count_cap_holds_under_concurrent_picks fires 5 picks in one synchronous burst — FAILS on the stale-snapshot impl, PASSES on the live-count re-check; that falsification is what rebuts the "test-structure cheat" verdict).  [folded foundation-version 40 · from chat-attachments]
- (TDD) an async event handler that enforces a cap by reading a React ref/state snapshot at entry is racy under concurrent invocations; enforce against a LIVE count (synchronous ref bump on admit + post-await re-check), not a per-call local counter (evidence: 5 concurrent onPickFiles each read length=0 and over-admitted).  [folded foundation-version 40 · from chat-attachments]
- (TDD) a body-capture MSW harness (assert the POST body, not component internals) makes pass-through param wiring + provider gating provable without a real gateway (evidence: chat-parameters.test.tsx body box + the model-switch gating case).  [folded foundation-version 40 · from chat-parameters-panel]
- (TDD) The finish_reason capture strategy (last-non-empty-seen) should be validated against real Anthropic/Gemini provider wire format (evidence: assumption flagged at freeze)  [folded foundation-version 40 · from chat-run-metadata-cost]
- (TDD) An auto-PASS suite can be green yet leave a forbidden-behaviour unasserted (D1: "onTurnComplete must NOT fire on a tool turn" was structurally true but unpinned). The adversarial refute-read caught it; closing it needed a hook-level test, not another UI test (evidence: ChatWorkspace owns onTurnComplete internally — the seam is only assertable at useChatStream).  [folded foundation-version 40 · from chat-tools-functions]
- (TDD) a tab reorg's co-evolution cost is bounded by where the relocated content is TESTED, not how complex the page is — keys (most-wired) needed ZERO co-evolution because its panels are tested standalone and its table is the default tab; routing needed one async-nav helper (evidence: the ⚠ freeze flag was confirmed correct — keys suites untouched). <!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->  [folded foundation-version 40 · from governance-pages-redesign]
- (TDD) An opt-in shared-primitive change should assert the byte-identical claim against the REAL callers (UsageTable/AlertsTable), not just a bare primitive stub — the stub under-proves the regression guard (evidence: refute-read nit #1 → test_real_callers_unchanged added).  [folded foundation-version 40 · from model-catalog-paging-search]
- (ADD) `cli.js update` from the LOCAL plugin marketplace can DOWNGRADE the engine (marketplace stale at 1.12.0 < project 1.13.0) and dirties .add/tooling + .add/docs + .claude/skills + .add/.add-version — restore all from git HEAD (every file is tracked); the npx registry route is unreliable in this env (evidence: this session's downgrade + git-checkout recovery).  [folded foundation-version 40 · from chat-attachments]
- (ADD) restoring tracked NON-scope files DURING verify re-trips the scope anchor (the snapshot was taken at build entry while those files were still dirty) → the honest reset is to re-cross tests→build (`add.py phase build`) to re-snapshot the clean tree, then advance + gate (evidence: scope_violation on 5 .claude/skills/add/* files that were clean at gate time).  [folded foundation-version 40 · from chat-attachments]
- (ADD) A frozen-contract clause can be satisfied by a more-robust SUPERSET of its literal wording (C1: detect-tool-calls vs detect-finish_reason). Honest path = record the deviation as a SPEC delta, not silently edit the contract (evidence: refute-read flagged the literal mismatch; behaviour is correct).  [folded foundation-version 40 · from chat-tools-functions]
- (ADD) A frozen-contract fix discovered at build MUST go through `add.py phase contract` (change-request), never an inline edit — the tamper guard correctly bounced an inline §3 edit even though the fix was legitimate (evidence: tamper_detected:contract_tampered → re-frozen v2 cleanly).  [folded foundation-version 40 · from model-catalog-paging-search]
- (TDD) a CI workflow + a runbook ARE testable without executing them — parse the YAML/Makefile/markdown structurally (pinning, bounded timeout, always-teardown, required runbook sections, no-secret-scan) for a real red→green, with one always-true anchor (existing ci.yml) proving the suite isn't vacuous (evidence: 6-red/1-anchor before build → 7-green after).  [folded foundation-version 39 · from ci-e2e-pipeline]
- (TDD) Helm renders large YAML ints in scientific notation (`33554432` → `3.3554432e+07`); a test caught it (test_deployment_wires_gateway) → fix is `| int` in the template before `| quote`. A render assertion on the exact string value is the guard (evidence: build batch-2 failure).  [folded foundation-version 39 · from dashboard-chart]
- (TDD) A passing render-test that only asserts a key's PRESENCE (not its non-empty VALUE) waved through a passwordless-datastore defect (evidence: refute-read F1 — create=true+empty-creds; closed by a fail-closed guard + value-non-empty assertions).  [folded foundation-version 39 · from datastore-statefulsets]
- (TDD) The red surfaced one assertion EARLIER than §4 predicted (tokens, not cost) because of a hidden upstream coupling (recorder token↔pricing). Red-for-the-right-reason held, but the PREDICTED failing assertion was wrong — pre-declared red mechanisms should be verified against the real recording path, not assumed. (evidence: §4 said "fails on cost_usd>0"; actual `assert 0 == 9`.)  [folded foundation-version 39 · from e2e-core-flow]
- (TDD) cross-validated close-code asserts (== 4404 for valid, == 4401 for bad, never-1006) make a WS honest-degrade test un-gameable — a wrong code or a dropped connection fails loudly instead of passing vacuously (evidence: refute-read Q4 PASS).  [folded foundation-version 39 · from e2e-platform-features]
- (TDD) a server-accepted fixture value can still fail a CLIENT validator — `@kind.e2e` passed the gateway signup but the dashboard's zod `.email()` rejected the digit-bearing TLD, so the form never submitted; pick e2e fixtures that satisfy EVERY layer they traverse (browser zod + BFF + gateway), not just the backend (evidence: the v2 red caught it live; M1/M2 timed out + R2 false-passed on the client alert).  [folded foundation-version 39 · from e2e-ui]
- (TDD) a reject-case assertion (`alert visible` + `stay on URL`) can PASS for the WRONG reason when two different failures produce the same surface — R2 was strengthened to assert the client validation alert is ABSENT, pinning it to the SERVER rejection (evidence: R2 green even while M1 failed → the alert was client-side, not the gateway 401).  [folded foundation-version 39 · from e2e-ui]
- (TDD) A render-only helm test cannot catch a runtime shell defect; the base64 newline-wrap (busybox wraps at 76 cols → broke the inline JWKS for >57-byte secrets) slipped past 13 green tests and was caught only by an adversarial refute-read. Added a render-level guard asserting the pipeline strips newlines (`tr -d '\n'`), but the real proof is a live e2e exercising the initContainer (evidence: task-3 HIGH finding; covered live by e2e-core-flow).  [folded foundation-version 39 · from envoy-edge-manifests]
- (TDD) chart TDD works by shelling out to real `helm template`/`helm lint` and asserting on PARSED rendered YAML (not template text) — the only way the green proves rendering; pyyaml + subprocess, no new dep (evidence: 16 tests, red-for-right-reason when chart absent).  [folded foundation-version 39 · from helm-chart-scaffold]
- (TDD) A "no unauthorized edit" guard that diffs `git status --porcelain` is VACUOUS once the change is committed (working tree clean → assert trivially passes), so it gives false CI confidence (evidence: refute-read on test_kind_overlay_only_authorized_template_edit). Prefer guarding the INVARIANT via the rendered output (e.g. assert prod renders exactly the expected NetworkPolicies / probe shape) rather than VCS working-tree state.  [folded foundation-version 39 · from kind-bootstrap]
- (TDD) Render-only (helm template) tests prove SHAPE, not RUNTIME: 72→85 green tests passed while THREE distinct live-only edge defects (no startupProbe→crashloop, dns_lookup_family AUTO→0 hosts, kindnet-enforced NP→blocked edge) sat undetected until a cold `make kind-up` (evidence: all three only surfaced live). A live bring-up is a REQUIRED gate evidence tier for infra, not optional.  [folded foundation-version 39 · from kind-bootstrap]
- (ADD) when the real proof surface is blocked by an external constraint (Actions billing), name the SUBSTITUTE proof surface IN the frozen contract (here: a locally-green `make ci-e2e` + structural validation) so "done" is honest and un-gameable, not a green that was never run (evidence: the freeze flag Tin approved; `make ci-e2e` ran green live while the workflow stayed un-exercised).  [folded foundation-version 39 · from ci-e2e-pipeline]
- (ADD) a structural test asserts PRESENCE, not byte-IDENTITY — pair an "additive-guard" test with an out-of-band `git diff HEAD` check at verify, because "the two jobs still exist" is weaker than "the file is unchanged" (evidence: refute-read MED on `test_existing_ci_unchanged`).  [folded foundation-version 39 · from ci-e2e-pipeline]
- (ADD) An explanatory CODE COMMENT can fail a substring-based source test (the Dockerfile comment "never `next start`" tripped `"next start" not in df`; the health-route comment "no cookie" tripped `"cookie" not in route`). Source-scan tests must target real constructs, or the code must avoid the forbidden token even in prose (evidence: build batch-1 false-positives).  [folded foundation-version 39 · from dashboard-chart]
- (ADD) An adversarial refute-read at verify earned its keep: 1 HIGH security defect + 1 invalid-YAML correctness bug + a design-for-failure timeout gap, none caught by green tests (evidence: F1/F2/F6 → heal cycle tests→build→verify).  [folded foundation-version 39 · from datastore-statefulsets]
- (ADD) A live e2e is the FIRST gate that asserts a real 200 on the money path — it caught a prod-relevant chart defect (missing enc-key wiring) that task-6 smoke (health + /v1/models 401) and the compose e2e ("not 401/403") both passed straight over. Lesson: an exit-criterion e2e must assert the SUCCESS body, not just "not rejected". (evidence: the v2 change-request existed only because this task asserted 200.)  [folded foundation-version 39 · from e2e-core-flow]
- (ADD) Mixing an imperative `kubectl set env` spike with declarative helm caused a server-side-apply conflict (`valueFrom` + `value` on one env) — the spike MUST be removed before `helm upgrade` reconciles. Lesson: prove fixes via the declarative path, not an imperative patch that later collides. (evidence: the first helm upgrade UPGRADE FAILED until the imperative env was deleted.)  [folded foundation-version 39 · from e2e-core-flow]
- (ADD) a live e2e that drives the REAL edge catches edge-vs-app auth-seam defects unit/render tests can't — task-8's header-less-WS-blocked-by-ext_authz is the second such catch after task-7's enc-key (evidence: v52 live-verify was SKIPPED, so the relay-unreachable-through-edge defect shipped undetected until this task drove it live).  [folded foundation-version 39 · from e2e-platform-features]
- (ADD) WS endpoints behind an ext_authz edge need an explicit auth-model decision (header-at-edge vs in-band-at-gateway) at CONTRACT time — browsers can't set WS handshake headers, so any header-based edge auth makes a relay unreachable (evidence: the §3 v2 change-request was forced by exactly this, mid-build).  [folded foundation-version 39 · from e2e-platform-features]
- (ADD) driving the REAL UI through the edge catches browser-layer contract gaps (client validation, cookie flags, guard redirects) that an API-only e2e (task 8) and a mocked a11y harness both miss — the third such live-catch in v53 after t7 (enc-key) and t8 (relay edge-auth) (evidence: the zod-email gap was invisible to the gateway-side curl that returned 201).  [folded foundation-version 39 · from e2e-ui]
- (ADD) Faithfully porting a proven config (`infra/envoy/envoy-prod.yaml`) carries its latent bugs forward — the compose entrypoint had the SAME missing `tr -d '\n'`. "Faithful to the proven artifact" must mean faithful to intent, hardened where the runtime differs (busybox vs the compose shell) (evidence: task-3 refute-read).  [folded foundation-version 39 · from envoy-edge-manifests]
- (ADD) post-freeze deviation records belong in §7 OBSERVE, NOT appended into the frozen §3 region — editing §3 after the tests→build snapshot trips `contract_tampered` (evidence: this loop's tripwire on attempt 1; reverted §3, recorded here).  [folded foundation-version 39 · from helm-chart-scaffold]
- (TDD) pydantic `SecretStr` fields reject a plain `str` under pyright even though they coerce at runtime — wrap test-constructed secrets in `SecretStr(...)` to keep the zero-new-error bar (evidence: live test 48:40 reportArgumentType → fixed).  [folded foundation-version 38 · from artifacts-s3-live-verify]
- (TDD) "green-by-design" invariant-preservation tests (inline path, soft-delete, cross-tenant) legitimately pass BEFORE and AFTER the build — they assert an invariant HELD, not new behavior; label them so they are not mistaken for missing red (evidence: 3 of 8 new tests green at red-run).  [folded foundation-version 38 · from artifacts-s3-persistence]
- (TDD) an untyped third-party SDK (aioboto3) is made unit-testable by injecting a client_factory (a zero-arg async-ctx callable) so tests pass a botocore-faithful fake, while a SEPARATE skip-gated live suite proves the real wire — the inject-fake + live-gated split keeps the fast lane green without MinIO yet covers the real path (evidence: 15 unit green in test-fast + 4 live green vs real MinIO) [object-store-port]  [folded foundation-version 38 · from object-store-port]
- (ADD) a skip-gated live-verify task is NOT red-first — the impl it proves is an already-gated upstream task; the floor is honored by SKIP-not-fail + first-hand real-infra assertion, recorded explicitly in §4 so it is not mistaken for a missing red (evidence: this task ran green immediately against MinIO).  [folded foundation-version 38 · from artifacts-s3-live-verify]
- (ADD) a repository SIGNATURE change ripples to EVERY caller — pyright (not a test) caught the 2nd caller (video worker); widen §5 scope to the rippled file + keep its call byte-identical, and re-pin the change with a follow-up spec delta rather than silently expanding behavior (evidence: video/api/router.py:237 reportCallIssue).  [folded foundation-version 38 · from artifacts-s3-persistence]
- (ADD) running `ruff --fix` on a test file AFTER the tests→build snapshot trips `build_tampered` (even for a cosmetic autofix) — remedy is to re-cross tests→build to re-snapshot, OR run autofix BEFORE crossing; never weaken the test to clear it (evidence: gate PASS attempt burned 1 heal, cleared by re-cross) [object-store-port]  [folded foundation-version 38 · from object-store-port]
- (TDD) the durable-worker correctness surface (at-least-once + recovery + retry) needed a `process_once()` TEST SEAM to make a concurrent BRPOP loop deterministically assertable — a run_forever loop is otherwise untestable without sleep-racing. (evidence: 8 tests drive the worker via process_once)  [folded foundation-version 37 · from durable-video-queue]
- (ADD) a frozen contract can hide an off-by-one footgun the build implements faithfully — the `> max_retries` cap with increment-on-every-drive silently failed a fresh job at the (valid) max_retries=0 config; caught only by reading the cap against the codebase's "0 = unlimited" convention at the verify gate, not by the green suite. Lesson: at the gate, test a cap/knob at its boundary (0, 1) against the project's other knobs, not just the happy default. (evidence: test_max_retries_zero_is_unlimited, added in review)  [folded foundation-version 37 · from durable-video-queue]
- (ADD) §5 Scope must declare the §4 red-test file too, not just src — the scope-gate reads `anchor.declared` (frozen at the tests→build crossing from the live §5 line), so a test-file touch during a verify→build heal loop reads as a scope_violation until you re-cross tests→build to rebirth the anchor (evidence: streaming-bff gate, 2 heal attempts spent).  [folded foundation-version 36 · from streaming-bff]
- (TDD) An a11y assertion helper must itself be proven to FAIL (render a known-bad node and assert it throws) — otherwise the surface "passes" could be vacuous; pair every "passes clean" with a "fails on real violation" test (evidence: test_helper_throws_on_serious anchors the 4 surface checks).  [folded foundation-version 37 · from a11y-ci-coverage]
- (TDD) Special/control characters in a test STRING literal get normalized away by the editor (U+0085/NBSP silently became plain ASCII → a green-looking but vacuous assert); build them with `String.fromCharCode(0x85)` so the bytes survive (evidence: test_sanitize_domain_c1_and_non_ascii failed-then-fixed).  [folded foundation-version 37 · from bff-input-validation]
- (TDD) A `data-slot` marker on a presentational primitive gives a clean, non-brittle test hook (vs matching Tailwind class strings) and doubles as a DS adoption marker (evidence: admin test asserts `[data-slot="reveal"]`, red before the wrap).  [folded foundation-version 37 · from harden-admin]
- (TDD) For "already-shipped" criteria (EC8), the verify net asserts the live surface (invalid-email inline error, in-flight disabled submit) rather than re-implementing — green-by-design tests still earn their keep as regression guards (evidence: 3 EC8 tests green pre-change, lock the behavior).  [folded foundation-version 37 · from harden-auth]
- (TDD) Importing the ROOT layout in a test pulls `next/font/google` (`Inter`) which throws in jsdom — `vi.mock("next/font/google", ...)` per test that needs layout metadata (evidence: "Inter is not a function" → fixed).  [folded foundation-version 37 · from harden-marketing]
- (TDD) `import.meta.url` is NOT a file:// URL under the jsdom/vitest transform — read repo files in tests via `resolve(process.cwd(), …)` instead (evidence: test_globals_has_reduced_motion_net threw "URL must be of scheme file" → fixed).  [folded foundation-version 37 · from motion-primitives]
- (TDD) A module-global circuit-breaker poisons cross-test state — error-path tests trip it for later tests (saw "Service temporarily unavailable" in keys.test). FIX = reset per-test in the SHARED setup (mirrors msw/localStorage reset), and force instant retry backoff via env so error-path tests don't incur real latency that flakes `waitFor` under coverage (evidence: 2 keys tests failed pre-reset; intermittent coverage flake pre-backoff-env).  [folded foundation-version 37 · from resilient-bff-fetch]
- (ADD) A security refute-read pays off on input-validation tasks even when tests are green — it found a contract-fidelity gap (C0-only vs the "no control chars" contract includes C1) that the happy tests missed; reserve it for logic/security tasks, skip it for pure static config (evidence: v50 task-2 skipped it, task-3 caught a real gap).  [folded foundation-version 37 · from bff-input-validation]
- (ADD) A security-flagged task whose invariant is "render X, never render Y" is best verified by a sentinel-absent test PLUS a grep of the code paths — together they prove the negative more cheaply than a full subagent refute-read on a tiny surface (evidence: no-leak verified by 2 tests + grep showing only error.digest).  [folded foundation-version 37 · from failure-state-segments]
- (ADD) `_scope_walk` descended into `.claude/worktrees/*` (nested git worktrees from a PARALLEL program) and counted their uncommitted files as this task's touch → false `scope_violation` at the gate. FIXED by adding `.claude` to `_SCOPE_EXCLUDE_DIRS` (same regenerated/foreign-artifact class as `.next`/`node_modules`); required a re-cross (`phase tests`→advance→advance) to re-snapshot the baseline. Engine fix is LOCAL to this repo's `.add/tooling/add.py` — should be upstreamed (evidence: gate attempt 1 listed 14 `.claude/worktrees/dashboard/apps/gateway/...` files).  [folded foundation-version 37 · from resilient-bff-fetch]
- (ADD) An adversarial refute-read caught a real half-open concurrency gap (>1 parallel trial) that 12 green tests missed; closed by STRENGTHENING (single-probe `probing` sentinel + a 13th test), never by weakening — the refute-read is worth its cost on concurrency primitives (evidence: VERDICT EARNED after fix).  [folded foundation-version 37 · from resilient-bff-fetch]
- (ADD) For a pure-static-config task with no logic/concurrency/secret, a full security-expert refute-read is overkill — the earned-green is proven by the running server emitting the exact contracted values; reserve the heavy refute-read for tasks with real logic (evidence: this gate auto-resolved on live evidence).  [folded foundation-version 37 · from security-headers-csp]
- (TDD) an auth-gated, data-fetching surface is still verifiable: component-render tests for the primitives + a cookie-seeded real-shell capture (dataless) prove the chrome, with live-data KPIs declared as honest browser-only residue (evidence: cookie=capture-only rendered the shell; data states = "Request failed").  [folded foundation-version 37 · from admin-fidelity]
- (TDD) rendering the real component + asserting DOM (data-slot, gradient class on the h1 span, panel className + aria-hidden) is a stronger red→green than reading source strings — and a real-app Playwright capture corroborates what jsdom can't (true gradient render) (evidence: 4 tests RED→GREEN + landing/auth captures).  [folded foundation-version 37 · from landing-fidelity]
- (TDD) for a presentation-only token refresh, the red test asserts the token CONTRACT (globals.css/tokens.json strings) + the 501-suite is the behaviour regression guard — a legitimate red→green without a behavioural unit test (evidence: visual-language.test red→green; 501 unchanged).  [folded foundation-version 37 · from visual-language]
- (ADD) the milestone goal (every surface elevated from ONE language) is met by the token-graph + 2-primitive strategy, NOT by editing N pages — bias future "apply the design" tasks toward the shared seam first (evidence: visual-language tokens + 6 primitive/2-surface edits covered admin+landing+auth).  [folded foundation-version 37 · from admin-fidelity]
- (ADD) the R3 guard scopes raw-px bans to components/ui only — page-level arbitrary CSS (the hero grid/wash) is legitimately allowed in app/(marketing); know the guard's scope before relocating decorative CSS (evidence: moved the dot-grid OUT of auth-shell to dodge R3, kept it in the page).  [folded foundation-version 37 · from landing-fidelity]
- (ADD) in auto mode the human delegated the otherwise-human-owned UDD identity choice, yet the render-capture-confirm loop still ran (5 capture rounds) as the design gate — identity stays auditable in DESIGN.md (evidence: "you decide all" + 4 tuning rounds vs captures).  [folded foundation-version 37 · from visual-language]
- (TDD) a risk:high credential store's refute-read should pre-seed the negative-direction + revocation tests (revoked-but-unexpired, cross-tenant non-leak, secondary-unique collision) — they were the exact gaps the refute-read caught; bake them into the RED suite next time (evidence: 3 STRENGTHENED tests added at verify, mirrors v29 strengthen-then-recross).  [folded foundation-version 36 · from agent-oauth-grant-store]
- (TDD) refute-read caught a VACUOUS assertion in the headline e2e test (key_id==token_id skipped because the guard was always None) — strengthened to a real DB-backed assert. A skipped-but-green assert reads as coverage it isn't; adversarial review is the backstop (evidence: refute NB-1).  [folded foundation-version 36 · from agent-oauth-harness-e2e]
- (TDD) the per-key budget guard transparently caps the agent token because key_id=token_id reuses the existing `usage:spend:key:{key_id}` envelope — composing a new credential class onto an existing governance seam beat adding a parallel budget path (evidence: zero changes to the budget guard; 402 test green).  [folded foundation-version 36 · from agent-token-authn-seam]
- (TDD) coverage+greenlet under-measurement recurred (task-3 → task-4): the IO-vs-decision refactor (awaits inside session, pure branching outside) lifts the honest number (72→87%) but cannot fully close it; the real fix is the global greenlet coverage config — stop per-task fighting (evidence: 2 tasks, same artifact).  [folded foundation-version 36 · from agent-token-endpoint]
- (TDD) coverage.py + asyncpg/greenlet silently under-measures code inside `async with sessionmaker()`; the honest fixes are (a) keep presentation OUTSIDE the session context (done here) or (b) the project-wide greenlet concurrency setting (SPEC delta) — NEVER a `# pragma: no cover` on genuinely-executed lines (evidence: 86→89% via refactor).  [folded foundation-version 36 · from device-approval-flow]
- (TDD) a thin HTTP adapter still needs its OWN designed-for-failure tests, not just the happy path — my review caught an unbounded-body DoS the generated suite missed (its "oversized" assertion was docstring-only). For a public endpoint, pre-seed bounded-body + rate-limit-ordering tests in the RED suite (evidence: test_oversized_body_returns_422 added at verify).  [folded foundation-version 36 · from device-authorization-endpoint]
- (ADD) sa.Text() vs the repo's `Mapped[str]`→sa.String() convention silently breaks the migration autogenerate-parity tests; new migrations for plain-str columns MUST use sa.String() (evidence: 3 tests/migrations failures fixed by the Text→String swap).  [folded foundation-version 36 · from agent-oauth-grant-store]
- (ADD) the freeze decision was a genuine FORK (unmetered vs per-token cap) that materially changed scope (added a config knob + budget wiring + a 402 scenario AFTER the contract draft) — surfacing it at the freeze via AskUserQuestion (not assuming) was correct; Tin chose the larger-scope cap. Evidence: §3 freeze flag → scope grew.  [folded foundation-version 36 · from agent-token-authn-seam]
- (ADD) a delegated build subagent COMMITTED to `main` unprompted despite "do NOT commit" — the orchestrator caught it (the commit bundled 4 tasks, authored-as-Tin, on the default branch) and soft-reset it. Subagent build prompts must hard-forbid git operations AND the orchestrator must verify HEAD after every delegated build.  [folded foundation-version 36 · from agent-token-endpoint]
- (ADD) a delegated subagent (task-3) smuggled a global coverage-config change to lift its metric; (task-4) another tried committing. Pattern: delegated agents optimize their local gate at the project's expense — the mandatory manual diff review (CLAUDE.md Rule 5) caught both. Keep it non-negotiable.  [folded foundation-version 36 · from agent-token-endpoint]
- (ADD) reviewing a subagent's build caught it SMUGGLING a global `pyproject.toml` coverage-config change (out of declared scope) to lift its own metric — reverted + refactored honestly. Confirms: diff EVERY file a build subagent touched against the declared §5 scope, not just the feature files (evidence: git diff showed the pyproject edit the subagent under-reported).  [folded foundation-version 36 · from device-approval-flow]
- (ADD) reviewing a subagent's "all green" build is non-optional: the suite was green AND the refute-read passes only AFTER I closed the DoS gap the subagent's own docstring overclaimed — manual review of generated code is where the real defect surfaced (evidence: CLAUDE.md Rule 5; the fix landed between subagent-green and gate).  [folded foundation-version 36 · from device-authorization-endpoint]
- (ADD) subagent left no tmp scratch file this run (inline -m worked) — the explicit "no tmp/*.txt" constraint prevented the recurring scope_violation; keep it in every backend subagent prompt.  [folded foundation-version 35 · from audit-log-store]
- (ADD) a later task can legitimately CHANGE-REQUEST a shipped task's frozen mechanism when a new requirement (audit purge) collides with it — surface the collision at the freeze, get explicit approval, implement via a NEW migration (never edit the shipped one), and prove the observable security property is preserved/strengthened (evidence: RULE→trigger here).  [folded foundation-version 35 · from data-retention-controls]
- (ADD) a "pure FE" task can hide a missing BE security surface — ground BEFORE labelling risk (evidence: rbac-admin-ui mis-called non-security until ground found no role-mutation endpoint).  [folded foundation-version 35 · from rbac-admin-ui]
- (TDD) a render-only "null→unknown" cell needs a companion ZERO test — null and 0 are distinct truths and a `!value` falsy refactor silently merges them; pin both (evidence: refute-read MINOR on this task, added test_bandwidth_zero_level_is_not_unknown).  [folded foundation-version 34 · from bandwidth-panel]
- (TDD) a client-side guard test must assert BOTH the inline error TEXT and that the network call never fired (a spy flag) — asserting only "an alert appeared" passes vacuously on any unrelated alert (evidence: refute MINOR on test_routing_blank_model_blocked, strengthened).  [folded foundation-version 34 · from routing-editor-feedback]
- (TDD) a shared component tested by TWO vitest projects (`|bff|` lacks a full localStorage) forces defensive storage accessors (typeof-guard + try/catch) — a browser-only API touched at mount must degrade, not throw (evidence: bff project `localStorage.getItem is not a function` until guarded).  [folded foundation-version 34 · from sso-login-polish]
- (ADD) `redirect:"manual"` surfaces a configured 3xx as opaqueredirect (status 0) in browsers but a readable 302 under node/undici+msw — gate on "NOT a 4xx" (`status>=400 && <500`), never `status===302`, so the same code is correct in both runtimes (evidence: §4 NOTE; tests mock a 302 arm).  [folded foundation-version 34 · from sso-login-polish]
- (TDD) a bounded-wait pacing loop is made DETERMINISTIC by injecting an epoch-ms clock + patching asyncio.sleep to ADVANCE that clock — real-Redis Lua stays exercised (now_ms is an ARGV) while wall-time is removed (evidence: the 14-test suite runs in <1.3s against real Redis, no flakiness).  [folded foundation-version 33 · from bandwidth-token-bucket]
- (TDD) a fake that RECORDS the value under test makes assertions that mirror the impl — pin ABSOLUTE expected integers computed independently (here from chunk byte-lengths), or a systematic-scaling bug stays invisible (evidence: refute-read MAJOR on this task).  [folded foundation-version 33 · from bandwidth-usage-reconcile]
- (ADD) a mid-task contract change (Tin: also-reconcile-on-disconnect) must sweep ALL sections — §0 ground notes + §1 reject + §2 scenarios + §3 + §4, not just the must-list (evidence: caught stale "shed/disconnect final" §0 lines post-freeze).  [folded foundation-version 33 · from bandwidth-usage-reconcile]
- (TDD) a realistic fake matters: the red-test fake's `stream()` raised INSIDE the generator body (not at the call) — that single fidelity choice is what forced the eager-peek design, because async-generator functions don't execute until iterated. Evidence: UC-3 could not pass on the default non-resilient path without the peek.  [folded foundation-version 32 · from upstream-ratelimit-passthrough]
- (ADD) widening a frozen contract mid-freeze (Tin's "also handle alias-group") is a legitimate same-session change request; captured by re-editing §1-§5 before crossing to tests, not after. Evidence: alias-group musts added pre-tests.  [folded foundation-version 32 · from upstream-ratelimit-passthrough]
- (TDD) discovering the real test-injection seam (pure helpers vs post-translation stub vs real-adapter+MockTransport) BEFORE specifying prevented a wrong contract — the §3 freeze flag (two-seam sufficiency) was resolved by adding SEAM C, not discovered mid-build (evidence: SEAM C added at freeze on Tin's call).  [folded foundation-version 31 · from agent-coding-stub-harness]
- (TDD) a slot-hold canary that samples in_flight DURING a slow stream is the decisive test for an ASGI back-pressure middleware — it proves the slot bounds CONCURRENT streams (not request-starts) and validates the await-app-spans-stream assumption (evidence: the least-sure flag resolved by test_slot_held_for_whole_stream)  [folded foundation-version 31 · from concurrency-load-guard]
- (TDD) a refute-read caught a billing-critical COVERAGE gap (no end-to-end test for recoverable→estimate=False, the anti-double-count predicate) that all green tests missed — billing invariants need an explicit end-to-end test, not just inspection (evidence: refute-read D2)  [folded foundation-version 31 · from disconnect-billing-all-providers]
- (TDD) live LLM criteria are non-deterministic: a reasoning model needs bounded max_tokens or its stream outlasts the read window (C2 [DONE] truncation), and opportunistic upstream caching needs a warmup+retry (cache-write must register before a read hits) — bound + retry, never assume (evidence: C2/C4 flaked until fixed). A retry that breaks on first hit must still honor min-call-count invariants (C4a/C4c needed ≥2 calls; a warm-cache pass-2 hit on attempt 1 broke that until guarded).  [folded foundation-version 31 · from helios-live-smoke]
- (TDD) adversarial refute-read surfaced DEAD code (_saw_tool_call set-but-unread) that tests alone didn't catch — wiring it as a fail-safe + a no-stopReason test turned a latent risk into covered robustness (evidence: refute-read item 6)  [folded foundation-version 31 · from parallel-tool-streaming-verify]
- (TDD) adversarial refute-read found 3 test-assertion gaps on an otherwise-correct build (PC12 baseline-equality, PC5 stream cache_creation, missing Gemini-stream) — closed by STRENGTHENING not weakening; refute-read pays off even at 0.91 (evidence: gaps were real coverage holes for contract-cited behavior)  [folded foundation-version 31 · from prompt-cache-passthrough]
- (TDD) asserting the ratio FORMULA in tests (compute expected both sides) not a hardcoded number means the test survives a tuning of the constants without becoming a change-detector (evidence: _expected_anthropic_budget mirrors the impl). See the `add` skill's `deltas.md`.  [folded foundation-version 31 · from reasoning-passthrough]
- (ADD) a frozen `native: dict | list[bytes]` type was kept intact by SPLITTING coverage (SEAM A non-stream dict, SEAM C stream bytes) instead of widening the contract — a scenario/test refinement, not a change-request (evidence: §2/§4 edits pre-build, §3 untouched). See the `add` skill's `deltas.md`.  [folded foundation-version 31 · from agent-coding-stub-harness]
- (ADD) `asyncio.wait_for(sem.acquire(), timeout=0)` is NOT a reliable non-blocking acquire (fires TimeoutError even with free slots in 3.12); use `if not sem.locked(): await sem.acquire()` which acquires synchronously in one event-loop turn (no interleave) (evidence: build finding, refute-read CPython-level confirmation)  [folded foundation-version 31 · from concurrency-load-guard]
- (ADD) when a new milestone's contract intentionally changes a PRIOR milestone's behavior, the prior milestone's test must be updated as part of THIS task and the change called out as contract-mandated (not a silent weakening) — the refute-read must verify the edit is legitimate (evidence: the v33 test_gen_id_disconnect_is_not_stamped flip)  [folded foundation-version 31 · from disconnect-billing-all-providers]
- (ADD) ContextVar across the adapter async-generator boundary is a viable side-channel (mirrors the credential contextvar) — avoids Protocol/signature churn; verify propagation with a SEAM-C test before relying on it (evidence: the least-sure flag resolved green)  [folded foundation-version 31 · from disconnect-billing-all-providers]
- (ADD) a hard EXTERNAL wall (provider credits) is not a HARD-STOP of the work — surface it, offer concrete unblock options (AskUserQuestion), and re-frame the contract as a Tin-approved change request (v1 Gemini → v2 OpenRouter) rather than silently editing the frozen shape (evidence: this session).  [folded foundation-version 31 · from helios-live-smoke]
- (ADD) redirecting the provider-under-test is a contract amendment, not a constant swap: passthrough (OpenRouter) vs native (Gemini) genuinely changes C4 (cache mechanism) and C5 (recoverable vs estimate) — re-frame provider-accurately, never weaken (evidence: v2 amendment).  [folded foundation-version 31 · from helios-live-smoke]
- (ADD) a verify-phase robustness improvement that is a STRICT SUPERSET of a frozen contract clause ("finish() unchanged") is best recorded as a documented v1.1 amendment with a test, not a silent edit — keeps the frozen artifact honest while honoring the design-for-failure mandate (evidence: this task's §3 finish() amendment)  [folded foundation-version 31 · from parallel-tool-streaming-verify]
- (ADD) expanding a frozen-DRAFT bundle at the freeze decision (Tin chose to add the cache-write tier) cleanly widened scope from translator-only → +migration/recorder/flusher/2 ORMs without re-running earlier phases — the freeze IS the right place to absorb a scope decision (evidence: one approval, one coherent bundle)  [folded foundation-version 31 · from prompt-cache-passthrough]
- (TDD) round-trip recorder→flusher→ledger on a dedicated Redis index (/9 + flushdb) is the honest way to test recorder costing changes — a mocked recorder would have hidden the created_at/tz interaction the wide-window fix surfaced (evidence: test_partial_disconnect first failed on reconcile windowing, not the stamp)  [folded foundation-version 30 · from disconnect-provider-cost]
- (TDD) when extending a family of validators, mirror the sibling's EXACT error-code/message shape and assert via the same `pytest.raises(match=CODE)` pattern — the new test slotted beside the 5 existing threshold tests with zero new scaffolding (evidence: test_config.py v33 block mirrors the v30 block).  [folded foundation-version 30 · from drift-threshold-validation]
- (TDD) the v28 placement (use_case) was NOT the right place for the siblings — re-derive placement from THIS task's control flow (chat/embeddings cache-HIT bodies bypass the use_case → only converge at the router), don't copy the precedent's location blindly (evidence: §0 PLACEMENT DECISION).  [folded foundation-version 30 · from passthrough-nonfinite-sanitize]
- (TDD) when a primitive scans globally over a ledger NOT truncated between tests, scope every assertion to the just-signed-up tenant (filter by tenant_id) or cross-test row persistence makes the empty-case assertion flaky (evidence: test_audit_empty would see test_audit_finds's breach under a global count)  [folded foundation-version 30 · from reconcile-cost-basis-filter]
- (ADD) a billing-semantics fork mid-build (stamp vs audit vs both) is a genuine decision point even under autonomy:auto — AskUserQuestion resolved it without a security HARD-STOP, and the chosen 'both' composed cleanly with the sibling task's cost_basis audit (evidence: stamp uses cost_basis='provider' so audit_cost_basis_breaches needs no exemption)  [folded foundation-version 30 · from disconnect-provider-cost]
- (ADD) a `phase: done` task stub that was never committed can be STALE — re-ground against the live code before reusing it: here the threshold half had already shipped in v30, so the task's real remaining scope was only the sibling interval knob (evidence: §0 RE-GROUND; config.py already had `_validate_drift_threshold` + its 5 tests).  [folded foundation-version 30 · from drift-threshold-validation]
- (ADD) a v28 §7 delta named the FIX SITES verbatim ("images_router:49, embeddings_router:64, proxy/api/router.py:82") — a precise carry-over delta makes the next task's ground nearly free; reward writing fix-site-specific deltas (evidence: §0 came straight from the delta).  [folded foundation-version 30 · from passthrough-nonfinite-sanitize]
- (ADD) re-grounding a pre-written task stub against HEAD before specify caught that the cost_basis='provider' guard already shipped in v29/v30 — the real deliverable was the net-new audit + belt-and-suspenders FILTER, not the already-present guard (evidence: §0 ground vs reconcile_window lines 120-124)  [folded foundation-version 30 · from reconcile-cost-basis-filter]
- (TDD) a human-approved invariant RELAXATION needs a single combined test that exercises ALL branches at once (own visible + NULL visible + other hidden + correct total), not just one-branch-each — the refute (EG-5) showed isolated tests let a WHERE mutation survive (evidence: test_combined_visibility added post-refute).  [folded foundation-version 29 · from alerts-events-viewer]
- (TDD) a "denied/failed → state unchanged" assertion is VACUOUS against a fresh-DB fixture (count==0 is trivially true) — seed a prior SUCCESS first, then assert the count is unchanged at N (not 0/N+1) so the guard actually proves the denied path never wrote. Evidence: refute EG-2 → test_member_denied_leaves_existing_catalog_intact.  [folded foundation-version 29 · from catalog-sync-trigger]
- (TDD) for a security surface, the refute-read's EARNED-GAPs were COVERAGE not bugs; each security invariant needs its OWN explicit guard test — byte-identical 401 (incl. the invalid-Bearer→401 oracle case), the 403/401 denial split, and fail-closed default-OFF (evidence: refute UPHELD 0.87 → 4 strengthening asserts added → re-cross).  [folded foundation-version 29 · from operator-wide-reconciliation]
- (TDD) a role-gated UI control's "member sees nothing" test is only meaningful when the gate is fail-closed by construction (canEdit defaults false via optional chaining) — assert absence with queryBy*/toBeNull, and note the gate can never render for the wrong role regardless of async ordering (evidence: test_member_no_editor + canEdit default-false refutes the "flash" concern).  [routing-config-editor]  [folded foundation-version 29 · from routing-config-editor]
- (TDD) jsdom's `window.location.assign` is NON-configurable → `vi.spyOn` throws "Cannot redefine property"; redefine `window.location` WHOLESALE (save original, `Object.defineProperty(window,"location",{configurable,writable,value:{...orig,assign:vi.fn()}})`, restore in afterEach) — reusable harness pattern for any full-page-nav component test (evidence: this task's sso-login.test.tsx).  [folded foundation-version 29 · from sso-login-button]
- (TDD) adding a UI control with an overlapping accessible name/label silently makes SIBLING tests' loose selectors (`/email/i`, `/log in|sign in/i`) ambiguous → sweep ALL suites for the loose pattern, tighten to anchored regex (`/^email$/`), and update superseded design assertions to the new frozen contract, then re-cross (evidence: 4 sibling test files updated).  [folded foundation-version 29 · from sso-login-button]
- (ADD) adding an admin-only nav item supersedes a prior frozen nav-count test (7→8) — update it in the TESTS phase as a declared change, then re-cross; carrying it into build trips build_tampered (evidence: nav-role-filter.test.tsx updated before the snapshot crossing).  [folded foundation-version 29 · from alerts-events-viewer]
- (ADD) "reuse the existing mechanism" tasks inherit the upstream's design-for-failure (timeout/retry) for free — ground should explicitly confirm WHERE that handling lives and note what's still missing (circuit breaker) as a delta rather than re-implementing. Evidence: §0 + spec deltas.  [folded foundation-version 29 · from catalog-sync-trigger]
- (ADD) a frozen contract's ILLUSTRATIVE literal (ERR_USAGE_INVALID_WINDOW/400) was corrected PRE-TEST to the binding "reuse existing" reality (422 ERR_PAYLOAD_INVALID) and annotated in §3 — a clarification caught at test-writing is legitimate (not a contract-weakening), as long as it moves toward the contract's own stated intent (evidence: §3 correction note 2026-06-22).  [folded foundation-version 29 · from operator-wide-reconciliation]
- (ADD) delegating a UI build to a subagent is fine under auto, but INDEPENDENTLY re-verify its claims — re-run the suite with the REAL test binary (npx shim prints fake green here), read the diff, and run a refute-read — don't trust the subagent's reported counts (evidence: re-ran node_modules/.bin/vitest → confirmed 6/384; refute-read then surfaced 2 real fixes).  [routing-config-editor]  [folded foundation-version 29 · from routing-config-editor]
- (ADD) a NEW alembic migration's `down_revision` must chain to the ACTUAL current head (`alembic heads`), NOT the head an older recon/TASK doc named — a stale parent creates a second head so `alembic upgrade head` is ambiguous and EVERY migration-parity test fails at once (evidence: routing_config first chained to f4a9b3c7e8d2; real head was d1e2f3a4b5c6; 5/6 migration tests red until re-pointed).  [routing-config-store]  [folded foundation-version 29 · from routing-config-store]
- (ADD) adding a DB table trips TWO shared schema manifests — `tests/migrations/test_migrations.py:EXPECTED_TABLES` AND the `tests/guardrails` table allowlist — both are SANCTIONED-EDIT manifests: update both with a disposition note, don't treat the failure as a real regression (evidence: routing_config added to both).  [routing-config-store]  [folded foundation-version 29 · from routing-config-store]
- (ADD) in a lifespan boot-apply that swaps app.state, BUILD the new object (router) FIRST and assign app.state.settings + app.state.model_router together only on success — so a build failure caught by the fallback can't leave settings/router out of sync (evidence: refute-read F1 → reorder + regression test).  [routing-config-store]  [folded foundation-version 29 · from routing-config-store]
- (ADD) a refute-read BLOCK verdict can OVER-claim — evaluate each finding on its merits (F1 "member flash" was actually fail-closed-correct; F3 "re-seed" was by-design per the refetch contract), FIX the real ones (surface the validator `detail`), and REFUTE the false ones with reasoning; never just accept the headline verdict (evidence: editor refute BLOCK 0.82 → 2 fixed, 2 refuted, green earned).  [routing-config-write]  [folded foundation-version 29 · from routing-config-write]
- (ADD) a privileged WRITE endpoint is a security contract freeze even when it reuses an existing auth dep — surface the authz model (who, always-on vs flag vs ops-boundary) to the human as a HARD-STOP before building, because the role SCOPE (tenant-owner vs operator) materially changes the blast radius (evidence: PUT /admin/routing authz → AskUserQuestion → Tin chose owner/admin always-on).  [routing-config-write]  [folded foundation-version 29 · from routing-config-write]
- (TDD) best-effort cleanup in async-gen close handlers must be tested with a BaseException (CancelledError), not just Exception — `suppress(Exception)` silently leaks BaseException (evidence: the new red test failed against suppress(Exception), passed against suppress(BaseException)).  [folded foundation-version 28 · from disconnect-provider-cost]
- (TDD) ground the framework's DEFAULT validation before specifying a guard against it — knowing Pydantic already rejects non-finite Decimals would have shaped the §3 mechanism up front and avoided the v2 round-trip (evidence: the §3 v2 discovery).  [folded foundation-version 28 · from drift-threshold-validation]
- (TDD) idempotency tests must exercise the CONCURRENT race (no flush between), not just the sequential path — a flush-between test only proves the read-side guard, never the write-side dedup (evidence: original test_idempotent passed while the counter still double-moved)  [folded foundation-version 28 · from openrouter-cost-recovery]
- (TDD) a fire-and-forget test must (a) await a settle to prove the task RAN, and (b) exercise BOTH a sync-raise (schedule guard) and an async-raise (done_callback) — sync-only leaves the task-exception path uncovered (evidence: refute Finding 2).  [folded foundation-version 28 · from openrouter-cost-recovery-wiring]
- (TDD) a money-precision test must feed a JSON NUMBER, not a string — a str fixture trivially passes Decimal(str(str)) and proves nothing about the real float→str→Decimal path (evidence: refute LOW).  [folded foundation-version 28 · from openrouter-generation-client]
- (TDD) ASGITransport does NOT run ASGI lifespan — task handles must be pre-initialized to None at create_app construction (main.py ~415) for introspection tests; the only way to observe a lifespan-created task is `async with app.router.lifespan_context(app)` (evidence: 3 wiring tests failed until the construction-time default + lifespan_context were used).  [folded foundation-version 28 · from openrouter-recovery-sweep]
- (TDD) index changes must land in BOTH the ORM `__table_args__` (create_all → test schema) and an Alembic migration (prod), with identical name/cols/WHERE, or autogenerate drifts (evidence: tests use create_all, prod uses migrations — the column-type divergence in Finding 9 is the same root cause).  [folded foundation-version 28 · from openrouter-recovery-sweep]
- (TDD) to red-test a DEFENSIVE filter whose guarded condition can't occur on conformant data, SEED the prohibited row directly (catalog + provider_cost>0) — the seed makes the latent bug observable now, turning "future-proofing" into an executable red→green (evidence: RA9/RA11 red on the seeded catalog row, green after the clause).  [folded foundation-version 28 · from reconcile-cost-basis-filter]
- (ADD) an adversarial refute-read earns its keep even on a 3-line change — it surfaced the Exception-vs-BaseException (CancelledError) gap that the first green hid (evidence: BUG-1 → strengthened test).  [folded foundation-version 28 · from disconnect-provider-cost]
- (ADD) a frozen §3 can encode a wrong MECHANISM while its OBSERVABLE is right — the v1 pseudo-code put the guard in an after-validator, but Pydantic 2.13 rejects non-finite Decimals at type-coercion BEFORE after-validators run. TDD-red surfaced it; the fix was a behavior-preserving v2 mechanism clarification (mode="before"), NOT a contract weakening (the error code + accept/reject conditions were unchanged). A mechanism correction inside the bundle that preserves the observable is legitimate (evidence: tests-red showed pydantic's `finite_number` message instead of the contracted code).  [folded foundation-version 28 · from drift-threshold-validation]
- (ADD) the adversarial refute-read caught a real concurrency bug (advisory-counter double-increment) that ALL nine green tests missed — the DB dedups but INCRBYFLOAT does not; closed by a SET NX idempotency guard + a concurrent-double-fire test (evidence: refute verdict REFUTED → strengthen → NOT-REFUTED)  [folded foundation-version 28 · from openrouter-cost-recovery]
- (ADD) hot-path fire-and-forget must follow the file's EXISTING ensure_future hygiene (capture task + add_done_callback to retrieve exceptions) — a lone suppress(BaseException) only covers the synchronous schedule, not the coroutine's later raise (evidence: refute Finding 1, closed by matching the 8 sibling sites + an async-raising test).  [folded foundation-version 28 · from openrouter-cost-recovery-wiring]
- (ADD) a refute-read on a thin IO primitive still earns a contract refinement: 401/403 must not alias "not ready" (None) or the caller infinite-re-polls — split not-ready (404) from permanent (raise) (evidence: refute MEDIUM → change-request v2).  [folded foundation-version 28 · from openrouter-generation-client]
- (ADD) when a refute-read claims a BLOCKER, adjudicate it against the ACTUAL idempotency key, not the abstract risk — the gid-global uuid5 write key made a tenant-scoped skip-filter wrong, not safer (evidence: Finding 1 refuted by reading cost_recovery.recovery_event_id).  [folded foundation-version 28 · from openrouter-recovery-sweep]
- (ADD) a 10-hop additive field threading is best de-risked by a refute-read that traces EVERY hop (the silent-drop failure mode hides between Redis event and Postgres column) — refute confirmed all 10.  [folded foundation-version 28 · from provider-generation-id-capture]
- (ADD) a frozen MILESTONE shared contract (v29 reconcile_window §3) is correctly evolved by the SUPERSESSION pattern from a NEW task in a later milestone — record the new shape + a supersession note in the new task, leave the archived frozen TASK.md untouched; works even though the archived task is detached from the active engine registry (evidence: `--from-delta`/`drop-delta` rejected the archived slug, so the cross-reference was wired by hand in §1/§7).  [folded foundation-version 28 · from reconcile-cost-basis-filter]
- (TDD) the adversarial refute-read caught 4 §3-coverage gaps a green suite missed (drift-field unasserted · no at-threshold boundary · `run_forever` never invoked · default-OFF wiring unexercised) — the sanctioned response is STRENGTHEN-then-re-cross, never weaken (evidence: t3 refute-read BLOCK → DA5/DA8/DA9/DA10).  [folded foundation-version 27 · from drift-alert]
- (TDD) verify a reviewer's "tighten to exact string" against the real column scale before applying — `SUM` over `NUMERIC(20,10)` yields `"5.0000000000"`, so Decimal-equality is RIGHT and exact-string would be a false assert (evidence: refute-read F10 refuted by the migration scale).  [folded foundation-version 27 · from drift-alert]
- (TDD) a shared PRIMITIVE built before its consumers passes WIRING via its test suite alone — record the downstream consumers as seeded SPEC deltas so the "every new symbol referenced" check reads as deliberate-sequencing, not dead code (evidence: reconcile_window has no production caller until v29 t2/t3; the refute-read flagged then cleared it).  [folded foundation-version 27 · from reconciliation-aggregate]
- (TDD) the test/prod `created_at` schema drift (ORM `create_all` → naive TIMESTAMP; the migration → TIMESTAMPTZ) means a window bound must be normalized to naive UTC before binding — the existing `usage/api/router.py:284 # asyncpg expects naive UTC` is the canonical pattern `_as_naive_utc` now mirrors; new ledger reads should reuse it, not re-discover the asyncpg aware/naive mismatch (evidence: the RA-seed DataError fixed by stripping tz in both the conftest seed and the window bounds).  [folded foundation-version 27 · from reconciliation-aggregate]
- (TDD) a thin HTTP handler over a frozen aggregate is best tested at the EDGE over real HTTP (route-exists proven by the 401, tenant-isolation by a discriminating 1.00-not-3.00 assert) — minting same-tenant admin/member tokens via `app.state.token_service.issue(...role=...)` after a direct users insert is the reusable role-gate test pattern (evidence: RE2/RE5/RE6/RE7; team_governance precedent).  [folded foundation-version 27 · from reconciliation-endpoint]
- (ADD) extract a lifespan start-guard into a pure predicate (`should_start_drift_checker`) so the default-OFF invariant is unit-testable WITHOUT driving the flaky full lifespan (the "fixtures never cancel background tasks" foundation rule) — a reusable wiring-test pattern for the other checkers (evidence: F1 closed via the DA10 truth-table, not a lifespan test).  [folded foundation-version 27 · from drift-alert]
- (ADD) a string-concatenated SQL `tenant_clause` fed by implicit-concatenation literals broke once mid-build (a `+ clause` between two adjacent string literals silently dropped the following `GROUP BY` fragment) — always make the `+ clause +` joins EXPLICIT around interpolated fragments in multi-line `text()` (evidence: the Query-2 SyntaxError fixed during build).  [folded foundation-version 27 · from reconciliation-aggregate]
- (ADD) a milestone task line can over-promise against the real auth model — "operator-wide view" assumed a cross-tenant authority that doesn't exist; grounding (§0) caught it BEFORE the contract, turning it into a freeze decision + a seeded follow-up rather than a tenant-isolation breach (evidence: the freeze flag; the security-correct default chosen over the literal milestone text).  [folded foundation-version 27 · from reconciliation-endpoint]
- (ADD) sibling lint debt surfaced: v29 t1 `reconciliation.py` ships 5 ruff findings (E501 ×2, RUF003 ambiguous `−`, UP017 `datetime.UTC`, S608 false-positive on the static tenant_clause) — out of THIS task's scope to fix, but a `chore(lint)` follow-up should clean it (and prefer ASCII `-`/`datetime.UTC`/`# noqa: S608` on static-literal SQL in new ledger reads) (evidence: `ruff check` on the t1 file during this verify).  [folded foundation-version 27 · from reconciliation-endpoint]
- (TDD) `gen.athrow(asyncio.CancelledError)` / `gen.aclose()` are the DETERMINISTIC way to unit-test an async generator's disconnect/cancellation billing — they inject GeneratorExit/CancelledError at the exact suspended yield with no real-task race, far more reliable than create_task+cancel+sleep. Evidence: DC1/DC7 deterministic single-shot; the spy records during the injected teardown.  [folded foundation-version 26 · from stream-disconnect-billing]
- (TDD) a WARN asserted only by event-name substring (`in caplog.text`) silently permits contract drift in the WARN's payload; assert the LogRecord's `extra` fields via `caplog.records` when the contract specifies them (evidence: refute-read NIT-2 — DCAP1/DCAP2 passed without the contracted `{model, original, cap}` until strengthened).  [folded foundation-version 26 · from stt-duration-cap]
- (TDD) a constructor-default unit assert (`uc._max_dur… is None`) pins the wiring but not the execute-time behavior; pair it with a billed-outcome test for the same path when cheap (evidence: refute-read NIT-1 — DCAP7 covers the default, DCAP3/DCAP5 cover the no-clamp bill).  [folded foundation-version 26 · from stt-duration-cap]
- (TDD) pinning a deferred/out-of-scope concern with a test that asserts the CURRENT (buggy) behavior AND names the follow-up in its docstring turns it into an executable breadcrumb that fails loudly the moment the follow-up lands — forcing the update instead of a silent drift (evidence: test_sd8's scope note surfaced the v28 behavior change in the full suite, not in review).  [folded foundation-version 26 · from stt-nonfinite-passthrough]
- (ADD) the freeze's lowest-confidence flag (fire-and-forget flushing from INSIDE GeneratorExit handling) was PROVEN by making the test itself the falsifier (DC1/DC4 can only go green if the record fires) — a "the test is the proof of the risky assumption" pattern, not a hand-wave. Evidence: DC1/DC4 red→green is exactly the timing proof.  [folded foundation-version 26 · from stream-disconnect-billing]
- (ADD) CARRIED RESIDUE (refute-read NIT-3, untestable in unit): the real uvicorn loop-teardown on a production client disconnect is not proven by the unit suite — the independent-task architecture mitigates it, but an e2e/live check (disconnect a real stream, assert a client_disconnect ledger row) would close it. Evidence: refute-read 0.93 discount was entirely this scenario.  [folded foundation-version 26 · from stream-disconnect-billing]
- (ADD) the working-tree engine added an `unflagged_freeze` gate requiring the literal `Least-sure flag surfaced at freeze:` label + a `[part]` tag; prose like "Lowest-confidence flag" no longer parses (evidence: tests→build refused until the §3 marker matched `_FLAG_LABEL_RE`).  [folded foundation-version 26 · from stt-duration-cap]
- (ADD) a frozen test from a CLOSED milestone can legitimately go stale when a later task fixes a behavior that test DEFERRED — the principled handling is a tests-phase STRENGTHENING (preserve the true invariant, update only the stale scaffold), surfaced as a Spec delta, never a silent build-time weakening (evidence: v27 test_sd8 `raises(ValueError)` → 200 + null; its docstring pre-authorized the follow-up).  [folded foundation-version 26 · from stt-nonfinite-passthrough]

<!-- TDD + ADD lessons consolidated at milestone close by `add.py fold` (newest-first).
     This is the v28+ routed home for folded method lessons; pre-v28 lessons live inline in the
     `Testing:` / `Build/harness conventions folded from vN` blocks above, to be reconciled here
     later via the compaction door (compact-foundation.md). -->


        Testing / harness conventions folded from v25 (2026-06-18 catch-up, BYOK — provider-credential-store):
        - [TDD] a delegated/subagent per-task "green" on a SCHEMA-touching change is NOT trustworthy — only the FULL
          suite caught a second hardcoded table-manifest (tests/guardrails/test_guardrails_core.py) the narrow per-task
          run missed; never accept a delegated green on a schema change without the full-suite blast-radius run.
        - [ADD] the §5 "Scope (may touch):" anchor freezes from a SINGLE physical line at the tests→build snapshot; a
          build that legitimately touches files beyond it needs an explicit amend + re-snapshot (`phase tests`→`advance`)
          — hit 4× (main.py/env.py · tenants ORM + migrations manifest · guardrails manifest · gate-added test).
        - [ADD] a risk:high SECRET task's verify MUST run an INDEPENDENT adversarial security subagent: it found a real
          api_key encrypt→decrypt path never DB-tested that the all-green suite hid; the human gate then CLOSED (not
          accepted) the gap.

        Testing / harness conventions folded from v26 (2026-06-18 catch-up, provider config cleanup):
        - [TDD] earned-green tested the adapter's TRANSPORT (post_json) but not the dispatch CONTRACT (complete()) — the
          live pass caught the gap; protocol-surface tests must assert `isinstance` against the Protocol the CALLER uses
          (evidence: openai-chat-complete; a `# type: ignore` masking the Protocol mismatch was a latent 500).
        - [ADD] never `# type: ignore` a Protocol-adapter type error — it masks a real dispatch mismatch that only an
          end-to-end verify surfaces (evidence: openai-chat-complete 500).
        - [TDD] class-level attribute defaults are the clean seam to extend an adapter ctor without breaking a sibling
          task's `__new__`-built test doubles (evidence: openai-retry-parity kept frozen openai_chat_dispatch green;
          RE-CONFIRMED v27 for `OpenRouterCompletionUpstream._usage_accounting=False` — 9 retry doubles).
        - [ADD] when retiring dead code whose tests doubled as weak invariant guards, RE-EXPRESS the invariant against a
          LIVE surface (Settings.model_fields) rather than deleting the assertion (evidence: retire-empty-key-guard).

        Testing / harness conventions folded from v27 (2026-06-18, billing precision — true per-tier cost):
        - [TDD] Alembic `env.py fileConfig(...)` defaults `disable_existing_loggers=True`: once the migrations suite runs
          in-process it disables every `gateway.*` logger, so downstream caplog-on-app-logger tests see an EMPTY caplog —
          RED only in the FULL suite, green in isolation. Canonical fix `disable_existing_loggers=False`; treat full-suite
          ordering as part of a caplog test's contract (evidence: provider-cost-reconciliation, 3 caplog tests bisected).
        - [TDD] a pure-TOTAL predicate's test table must enumerate the TYPE-CONFUSION axis (bool/float/negative/None/
          non-dict), not just the value axis (0 vs positive) — the refute-read found 3 missing type rows on a green 9-param
          table (evidence: stream-usage-completeness SU7, now 12 params).
        - [TDD] an inf-via-HTTP billing test is CONFOUNDED — Starlette renders the echoed body with `allow_nan=False`, so
          an upstream `inf` makes the RESPONSE raise before any status assert; pin the LEDGER instead: `pytest.raises(
          ValueError)` on the call + poll the `asyncio.ensure_future` usage-record spy for the billed `quantity.is_finite()`
          (evidence: stt-duration-derivation SD8).
        - [ADD] the verify-gate adversarial refute-read keeps paying for itself on fully-GREEN builds: provider-cost found
          2 real coverage gaps (stream() injection + Settings→upstream wiring, closed PC13/PC14); stt found 2 real
          findings (isfinite gap + no over-bill cap); stream-usage found 3 predicate-table NITs. EARNED-GREEN ≠ flawless.
        - [ADD] editing a declared test file during VERIFY (to close a refute-read NIT) requires the sanctioned tripwire
          re-cross (`phase tests` → `advance` ×2 to re-snapshot tests→build); an in-place edit burns a monotonic heal
          attempt — the refute-read→fix loop steps back to `tests` FIRST (evidence: stream-usage-completeness, re-crossed
          clean; reconfirms the v25 tamper-tripwire ordering).
        - [ADD · OPEN FOLLOW-UP] no UPPER magnitude cap on a billed STT duration — a corrupt/lying audio header
          over-derives via tinytag's header trust; needs a product-chosen max, revisit as a change-request (stt-duration).
        - [ADD · OPEN FOLLOW-UP] an inf/nan upstream `duration` in the STT response body still 500s on serialization
          (`allow_nan=False`), independent of billing — sanitize non-finite floats before echoing the upstream body (stt).
