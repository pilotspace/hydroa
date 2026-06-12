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
Git: `<type>(<scope>): <summary>` + body + `author: Tin Dang` footer; message
        drafted in `tmp/*.txt`, committed via `git commit -F`; scopes: gateway,
        dashboard, infra, docs, pipeline, config
