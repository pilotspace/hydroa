# TASK: Impersonation live session guard

slug: impersonation-live-session-guard · created: 2026-07-05 · stage: production
milestone: tenant-impersonation
sensitivity: security
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `JwtTokenService.issue()`/`.decode()` (`tenants/infrastructure/jwt_service.py:20-108`) — READ
    ONLY, not modified. `.decode()` already fully implements M2/M3 (dual-identity claims,
    `Identity.impersonation` reconstruction, the SUPERADMIN+impersonation defense-in-depth
    reject). Confirmed by direct read: there is NO revocation check anywhere in `.decode()` —
    it validates signature/`exp`/`iss`/required-claims only. Natural TTL expiry (JWT `exp`
    elapsed) is therefore ALREADY fully rejected today via `jwt.InvalidTokenError` ->
    domain `InvalidTokenError` (line 101, `except (jwt.InvalidTokenError, ValueError, KeyError,
    TypeError)`) — this task's own gap is narrower than "TTL enforcement": it is PURELY
    "session explicitly Ended, but the JWT's own `exp` has not yet elapsed."
  - **THE actual per-request verification path is NOT one function — it is FIVE independent
    ones**, each decoding the same Bearer JWT via `tokens.decode(token)`/`token_service.decode
    (token)` and each reachable by an impersonation identity on the self-service `/admin/*`
    surface (confirmed by direct read of every one, not assumed from the sibling task's prose,
    which named only one):
    1. `_resolve_identity` (`tenants/domain/authz.py:164-177`) — backs `require_superadmin`
       (authz.py:210-233) and `require_permission(perm)` (authz.py:183-206). Reachable example:
       `GET /admin/audit` (`usage/api/router.py:705-711`, gated
       `require_permission(Permission.AUDIT_READ)` — confirmed ADMIN holds `AUDIT_READ`,
       `authz.py:74-75`).
    2. `get_identity` (`keys/api/deps.py:45-53`) — backs `require_owner_or_admin` (keys' own
       copy) AND is used bare (no permission layer at all) by `GET /admin/cache`
       (`tenants/api/cache_router.py:1-30`, docstring: "any authenticated role").
    3. `get_current_identity` (`catalog/api/deps.py:46-62`) — backs catalog's OWN
       `require_owner_or_admin` copy and is used bare by `GET /admin/catalog/models`
       (`catalog/api/router.py:143-149`).
    4. `_extract_identity` (`usage/api/router.py:69-79`) — called directly (no permission
       wrapper) by `GET /admin/usage` (`usage/api/router.py:102-108`, "for the authenticated
       tenant" — any role) and wrapped by `_require_usage_read`/`_require_ops_read` for other
       usage-surface routes.
    5. `GetIdentityUseCase.execute()` (`tenants/application/use_cases.py:97-98`, a plain
       `def execute(self, token: str) -> Identity: return self._tokens.decode(token)`) — wired
       via `get_identity_use_case` (`tenants/api/deps.py:50-53`) and used by FOUR different
       routers, THREE of which construct it directly (bypassing the shared DI wiring):
       `GET /admin/auth/me` (`tenants/api/router.py:67-81`, mounted under that router's own
       `"/admin/auth"` prefix, line 36 — verified by direct HTTP test run, not assumed; via the
       shared `Depends(get_identity_use_case)`), `agent_oauth/api/device_approval_router.py:117`
       (`GetIdentityUseCase(request.app.state.token_service)`, direct), `auth/api/
       oidc_admin_router.py:134` (direct), `proxy/api/provider_keys_admin_router.py:105`
       (direct).
    - Investigated and explicitly RULED OUT: `catalog/api/deps.py`'s own docstring says it
      "mirrors gateway/tenants/api/deps.py pattern" (not a 6th independent copy — that file only
      wires `GetIdentityUseCase`, already counted as #5). `ops/api/deps.py:require_ops` (line 61)
      also calls `tokens.decode(token)`, but the DECODED RESULT is discarded — used only to pick
      403 `ERR_OPS_FORBIDDEN` ("valid tenant JWT, wrong surface") vs 401 `ERR_OPS_UNAUTHORIZED"
      ("everything else"); no capability is granted by that branch regardless of the token's
      revocation state, and `/ops/*` is mTLS/XFCC-gated (`OpsCertVerifier`), not JWT-gated —
      confirmed by direct read, not assumed. /v1/* (the data-plane proxy) authenticates via
      `CompositeKeyAuthenticator`/sk- API keys (`keys/api/deps.py:111-135`), a completely separate
      credential path that never calls `TokenService.decode()` — an impersonation JWT cannot
      reach /v1/* at all, matching the milestone's own scope ("the ENTIRE existing self-service
      `/admin/*` surface").
  - `ImpersonationSessionRow` (`tenants/infrastructure/orm.py`, FROZEN @ impersonation-session-
    lifecycle §3 Part D) — `id` (PK, already indexed), `revoked_at`/`expires_at` — the EXISTING
    columns this task reads; ZERO new columns, ZERO new migration. End (`platform_impersonation_
    router.py:239-301`, FROZEN, NOT modified) already durably sets `revoked_at` via one
    conditional `UPDATE ... RETURNING`; this task adds a READ path only, no new writer.
  - `api_keys` revocation precedent (`keys/application/use_cases.py:290-300`) — **directly
    reusable, not merely analogous**: the codebase's HIGHEST-traffic auth path (every `/v1/*`
    data-plane call, sk- key verification) ALREADY pays a per-request DB read checking
    `revoked_at IS NULL` before authorizing, and ALREADY fails CLOSED (`row is None or
    row.revoked_at is not None or not match_ok -> raise InvalidApiKeyError`) — confirmed by
    direct read. This is this codebase's OWN established convention for "is this bearer
    credential still valid," not a novel pattern this task invents.
  - `get_session` (`core/db.py:73-75`) — `async def get_session(request) -> AsyncIterator
    [AsyncSession]: async with request.app.state.sessionmaker() as session: yield session`.
    FastAPI caches a dependency's result per-request by default, so `Depends(get_session)`
    resolved from inside an identity-resolution dependency shares the SAME session/connection
    the route handler's own body will use — confirmed no second connection is opened.
  - `create_async_engine(settings.database_url)` (`main.py:731`) — NO `statement_timeout`/
    `command_timeout` configured anywhere at the engine level (confirmed by search) — there is
    no ambient bounded-timeout safety net today; this task's own bounded timeout (§3) is a new,
    additive one, not a duplicate of an existing mechanism.
  - `RedisCooldownGate`/`RedisBudgetGuard` (`proxy/infrastructure/redis_cooldown_gate.py`,
    `budgets/infrastructure/redis_guard.py`) — this codebase's established FAIL-OPEN convention,
    but for a DIFFERENT class of decision (availability/enforcement tradeoffs: "maybe route to an
    unhealthy model" / "maybe slightly over-spend"). Read in full and explicitly NOT mirrored
    here — see §3 for why this task's own failure mode must be fail-CLOSED instead (a
    credential-revocation check, not an availability gate; matches the `api_keys` precedent's
    own fail-closed posture, not the cooldown/budget gates' fail-open one).
  - `.add/CONVENTIONS.md:9-19` — "Architecture: CLEAN ARCHITECTURE per domain module —
    `domain/` (entities, ports as Protocols, domain errors; zero framework imports) ←
    `application/` ← `infrastructure/` (SQLAlchemy/... adapters implementing ports) ← `api/`.
    Dependencies point INWARD only." Directly shapes §3: the new DB-reading mechanism CANNOT be
    inlined into `tenants/domain/authz.py` (would import SQLAlchemy/`AsyncSession` into a
    `domain/` module) or into `GetIdentityUseCase` (`application/`, would import infrastructure
    directly) without violating this documented rule — forces a Protocol-port + infrastructure-
    adapter split (§3 Part A/B).

Context (working folder): `.add/milestones/tenant-impersonation/MILESTONE.md` (Scope/Tasks —
  this task is explicitly listed, "closes the bounded post-End window... Tin flagged this as a
  live, undecided deploy-timing question: ship impersonation-session-lifecycle alone and accept
  the bounded window for now, or hold production deploy until this task ships alongside it");
  `impersonation-session-lifecycle/TASK.md` §7 OBSERVE's N1 spec-delta (the exact gap this task
  closes) and its Spec-delta note on the unused `ImpersonationSession` domain dataclass
  (investigated below — NOT this task's concern); `apps/gateway/tests/impersonation_session_
  lifecycle/test_impersonation_session_lifecycle.py` (fixture/harness conventions mirrored by
  §4's own new suite: suite-local fixture duplication, real Mint/End via HTTP, direct-SQL seeding
  only for states the API cannot produce).

Honors (patterns / conventions):
  - Reuse-over-invent: the `api_keys` revocation-check-on-every-request precedent (above) is the
    template for this task's own mechanism — a DB read against the ALREADY-FROZEN `revoked_at`/
    `expires_at` columns, not a new Redis denylist requiring a new write path.
  - CLEAN ARCHITECTURE layering (`.add/CONVENTIONS.md:13-19`) — new Protocol port in `domain/`
    (zero framework imports), concrete adapter in `infrastructure/`, wiring in `api/`/
    `application/` — see §3.
  - Design-for-failure (this repo's own standing rule + `.add/CONVENTIONS.md`'s "every outbound
    IO has timeout + bounded jittered retry... + circuit breaker" line) — applied WITH a
    deliberate, justified deviation on fail-open-vs-closed (§3): this is a REVOCATION check, not
    an availability gate, so it mirrors `api_keys`' fail-closed posture, not the cooldown/budget
    gates' fail-open one.
  - Additive-only: zero lines change in `platform_impersonation_router.py` (Mint/End, FROZEN),
    `ImpersonationSessionRow`'s schema, or `JwtTokenService.decode()`'s existing behavior for a
    token with no `impersonation` claim.

Anchors the contract cites: `_resolve_identity` (`authz.py:164-177`), `get_identity`
  (`keys/api/deps.py:45-53`), `get_current_identity` (`catalog/api/deps.py:46-62`),
  `_extract_identity` (`usage/api/router.py:69-79`), `GetIdentityUseCase.execute`
  (`tenants/application/use_cases.py:97-98`) + its DI wiring (`tenants/api/deps.py:50-53`) and 3
  direct-construction call sites (`agent_oauth/api/device_approval_router.py:117`, `auth/api/
  oidc_admin_router.py:134`, `proxy/api/provider_keys_admin_router.py:105`), `ImpersonationSessionRow`
  (`tenants/infrastructure/orm.py`), `InvalidTokenError` (`tenants/domain/errors.py`),
  `AUTH_TOKEN_INVALID` (`core/error_catalog.py:80`, `ErrorSpec(401, "ERR_AUTH_INVALID_TOKEN", ...)`),
  `TokenService` Protocol (`tenants/domain/ports.py:43-48`, the sibling port this task's new
  `ImpersonationSessionGuard` Protocol is modeled on), `object_store_timeout_seconds`
  (`core/config.py:613-614`, the `Field(default=5.0, gt=0)` style this task's own new timeout knob
  mirrors).

Issues/Risks (→ feed §1):
  - **HEADLINE RISK — scope is 5 call sites, not 1**: a fix that only patches `_resolve_identity`
    (the one file this task's own dispatch brief named) would leave FOUR other self-service
    entry points silently unprotected — a WORSE outcome than not building this task at all
    (false confidence: "the guard shipped" while 4/5 doors stay open). This is this task's own
    #1 lowest-confidence/scope flag, surfaced at the freeze (§3), not silently narrowed away.
  - Natural-TTL-expiry is ALREADY closed today (see Touches) — this task must NOT re-implement
    TTL enforcement; scope is PURELY the End-before-natural-expiry window.
  - Fail-open vs fail-closed is a real, consequential choice this codebase does NOT answer
    uniformly (cooldown/budget gates fail open; `api_keys` revocation fails closed) — picking
    wrong here means either (a) a DB blip silently reopens exactly the gap this task exists to
    close (fail-open — unacceptable for a revocation check), or (b) an impersonating superadmin's
    OWN requests transiently 401 during a genuine DB outage (fail-closed — an accepted, narrow,
    superadmin-only cost). §3 resolves this explicitly.
  - `GetIdentityUseCase` (`application/` layer) needs its constructor widened (new dependency) and
    `execute()` made `async` — a signature change touching its ONE shared DI wiring
    (`tenants/api/deps.py:50-53`) AND 3 routers that bypass that wiring and construct it directly.
    Widest single-symbol blast radius of the 5 call sites; named explicitly so Build does not
    discover it mid-flight.
  - The sibling task's own Spec-delta flagged `ImpersonationSession` (the plain domain dataclass,
    `entities.py:112-133`) as dead code — investigated: it is UNRELATED to this task (the sibling
    task's own dead-code residue, not a component this task's live-check needs or should start
    consuming). This task reads `ImpersonationSessionRow` (the ORM class), matching how
    `platform_impersonation_router.py` itself already does. Not this task's job to fix; left as
    the sibling's own open Spec delta.

Related intent: `.add/milestones/tenant-impersonation/MILESTONE.md` Exit criteria — "An ended
  session's token stops working immediately, not just at its natural TTL expiry (←
  impersonation-live-session-guard)" — the ONLY unchecked Exit criterion this milestone has left.
  GLOSSARY term: none yet formally added (milestone fold still pending per MILESTONE.md's own
  "Backfilled 2026-07-05" note) — this task introduces no NEW domain term, only a mechanism
  closing an already-named gap ("impersonation session" / "impersonation identity", both proposed
  by the sibling task's own §3, pending fold).

Ground SHA: cfbb464

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Impersonation live-session guard — per-request revocation check closing the
  End-before-natural-expiry window
Framings weighed:
DB-backed read-through check against the ALREADY-FROZEN `impersonation_sessions.revoked_at`/
`expires_at` columns, scoped to fire ONLY when `identity.impersonation is not None` (chosen —
mirrors this codebase's own `api_keys` revocation-check precedent verbatim; zero new write path,
zero new source of truth to keep in sync, zero new schema/migration) · vs a Redis-backed denylist
of ended `session_id`s populated at End()-time (rejected — End's contract is FROZEN and cannot be
touched, so populating a denylist would need EITHER reopening that freeze OR duplicating a second
write at all 5 read call sites' own risk; worse, it introduces a SECOND source of truth that can
silently drift from the DB row — if the Redis write fails/is skipped, the exact gap this task
exists to close reopens invisibly. A read-through DB check has no such drift: it always answers
against the same row End() already, verifiably, writes) · vs a Redis TTL-mirroring cache of
POSITIVE ("still valid") answers to cut DB load (considered and rejected — caching a positive
result for even a few seconds reintroduces a bounded version of the EXACT staleness window this
task exists to eliminate; traffic volume does not justify it — see below).
Placement — a NEW `ImpersonationSessionGuard` Protocol (domain port, `tenants/domain/ports.py`,
alongside the existing `TokenService` Protocol) + a concrete `DbImpersonationSessionGuard`
adapter (`tenants/infrastructure/`) implementing it (chosen — `.add/CONVENTIONS.md`'s own
"CLEAN ARCHITECTURE... domain/ zero framework imports... dependencies point INWARD only" rule
directly forbids importing `AsyncSession`/`ImpersonationSessionRow` into `tenants/domain/authz.py`
or into `GetIdentityUseCase` (`application/`) — confirmed this would be a genuine, avoidable
layering violation, not a style nitpick) · vs inlining the SQLAlchemy read directly into each of
the 5 call sites (rejected — duplicates security-critical logic 5x, the exact "two copies of a
check silently drift apart" risk this task's own Ground finding already warns about at 1x scale;
also violates the layering rule for 2 of the 5 sites) · vs a bare free function taking `session`+
`settings` directly, no Protocol (considered — simpler, but does not resolve the `GetIdentityUseCase`
layering violation, since `application/` still may not import `infrastructure/` directly).
Fail-CLOSED on any check failure (timeout / DB exception / missing row), NOT fail-open (chosen —
this is a CREDENTIAL-REVOCATION decision, matching this codebase's OWN `api_keys` precedent's
fail-closed posture exactly, not the cooldown/budget gates' fail-open one; a fail-open reading
here would mean a transient DB blip SILENTLY reopens the exact gap this task exists to close,
indistinguishable from "working as designed" — strictly worse than the bounded, TTL-capped status
quo gap this task is meant to eliminate) · vs fail-open, matching cooldown/budget gates (rejected
— those gates trade a WORSE service/cost outcome for availability; this gate would trade away the
task's entire security purpose for availability, a different and unacceptable tradeoff class).
Blast radius of fail-closed accepted as narrow: only impersonation-bearing requests (superadmin-
only, ≤1 active session per superadmin, TTL ≤900s) are affected; every ordinary tenant request is
completely unaffected (short-circuited before the guard is ever constructed or called).
No caching of a positive ("still live") answer, and no circuit breaker (chosen — traffic for this
check is inherently tiny by construction (superadmin-only, ≤1 concurrent session per superadmin,
TTL-capped): the QPS added to Postgres is negligible, so neither a cache (which would reintroduce
staleness) nor a circuit breaker (whose benefit — shedding load from a struggling dependency — is
moot at this volume, and whose absence costs nothing since every other concurrent `/admin/*`
request already depends on the SAME Postgres via its own `get_session` dependency regardless of
this check) earns its complexity) · a bounded per-call timeout IS added (a NEW, additive knob —
no ambient statement_timeout exists today, confirmed at Ground) so a struggling DB fails this
ONE check fast and cleanly (a clean 401) rather than compounding into a long, ambiguous ambient
hang — this satisfies this repo's "design for failure: timeouts" standing rule without a retry
(a retry would extend hot-path latency for no security benefit; a single bounded attempt that
fails closed is already safe) or a circuit breaker (see above).
No new HTTP error code (chosen — reuses the EXISTING `AUTH_TOKEN_INVALID` `ErrorSpec(401,
"ERR_AUTH_INVALID_TOKEN", ...)`, the SAME code every one of the 5 call sites already raises for
ANY other decode failure; from the caller's perspective "this bearer token no longer works" is
exactly true and requires no new documented error shape on any of the ~10+ self-service routers
this reaches) · vs a new dedicated code e.g. `ERR_IMPERSONATION_SESSION_ENDED` (rejected — would
require every self-service router's OpenAPI response set to grow a new documented code for a
condition none of them otherwise need to know about, and would leak a distinguishing signal
between "ordinary invalid token" and "impersonation session ended" that fail-closed reasoning
above already says should NOT be distinguishable from a generic infra-failure rejection either).

Must:
<must>
  - M1: a NEW `ImpersonationSessionGuard` Protocol (`tenants/domain/ports.py`, zero framework
    imports, modeled on the existing `TokenService` Protocol) declares one method:
    `async def ensure_live(self, impersonation: ImpersonationContext) -> None` — raises the
    EXISTING domain `InvalidTokenError` (`tenants/domain/errors.py`) if the session is not live;
    returns `None` (no-op) if it is.
  - M2: a NEW pure domain helper `ensure_impersonation_session_live(identity: Identity, guard:
    ImpersonationSessionGuard) -> None` (`tenants/domain/authz.py`, zero framework imports) —
    `if identity.impersonation is not None: await guard.ensure_live(identity.impersonation)`;
    otherwise an immediate no-op. This is the ONE place the "zero overhead for ordinary
    identities" guarantee is enforced: the guard's own `.ensure_live()` (and therefore any DB
    access at all) is never invoked when `impersonation is None`.
  - M3: a NEW `DbImpersonationSessionGuard` adapter (`tenants/infrastructure/
    impersonation_session_guard.py`, NEW file) implements `ImpersonationSessionGuard`. Constructed
    per-request with the ALREADY-RESOLVED `AsyncSession` (via the existing `get_session`
    dependency — FastAPI's per-request dependency cache means this is the SAME session/
    connection the route handler's own body uses, confirmed at Ground; no second connection).
    `.ensure_live(impersonation)`:
    a. Wraps a single indexed read — `SELECT revoked_at, expires_at FROM impersonation_sessions
       WHERE id = :session_id` (`id` already PK-indexed; zero new index) — in a bounded timeout
       (`settings.impersonation_live_check_timeout_seconds`, M6).
    b. LIVE iff a row is found AND `revoked_at IS NULL` AND `expires_at > now()` — the EXACT
       compound predicate `impersonation-session-lifecycle` TASK.md §3 already declares as
       session validity's sole source of truth ("the session-store row is the sole durable
       source of truth for 'is this session still valid'"); this task does not invent a new
       definition of "live," it re-reads the existing frozen one.
    c. On ANY exception (timeout, DB error, or the compound predicate failing) — raise
       `InvalidTokenError`, no distinction made between "explicitly ended" and "could not
       verify" (fail-closed, M-Framings). Log a WARNING on the infra-failure path only
       (mirrors `RedisCooldownGate`'s own convention: no session_id/token material in the log
       beyond the already-public session UUID).
  - M4: EACH of the 5 confirmed reachable call sites (§0 Touches) is wired to call
    `ensure_impersonation_session_live` immediately after a successful `.decode(token)`, INSIDE
    the SAME existing `try/except InvalidTokenError` block each already has — zero new except
    clauses anywhere:
    a. `_resolve_identity` (`authz.py`) — gains a `session: Annotated[AsyncSession,
       Depends(get_session)]` parameter.
    b. `get_identity` (`keys/api/deps.py`) — currently takes no `Request`/`session` at all; gains
       both.
    c. `get_current_identity` (`catalog/api/deps.py`) — already takes `Request`; gains `session`.
    d. `_extract_identity` (`usage/api/router.py`) — already takes `Request`; gains `session`,
       threaded through its 2 wrapper callers (`_require_usage_read`, `_require_ops_read`) which
       must also gain the parameter.
    e. `GetIdentityUseCase` (`tenants/application/use_cases.py`) — constructor widened to accept
       an `ImpersonationSessionGuard` (injected, per Protocol — `application/` may depend on
       `domain/`, never on a concrete `infrastructure/` adapter directly); `.execute()` becomes
       `async def`. The ONE shared DI wiring (`get_identity_use_case`, `tenants/api/deps.py`)
       AND all 3 direct-construction call sites (`device_approval_router.py`, `oidc_admin_router.py`,
       `provider_keys_admin_router.py`) are updated to construct/await it accordingly.
  - M5: this task performs ZERO writes anywhere — End's existing conditional `UPDATE` (FROZEN,
    unmodified) remains the sole writer of `revoked_at`; this task adds a READ path only.
  - M6: a NEW Settings field `impersonation_live_check_timeout_seconds: float = Field(default=2.0,
    gt=0)` (`core/config.py`, env `GATEWAY_IMPERSONATION_LIVE_CHECK_TIMEOUT_SECONDS`) mirrors
    `object_store_timeout_seconds`'s exact style (`gt=0`, fail-loud at boot on a non-positive
    value — no bespoke error-message convention needed beyond Pydantic's own `gt` violation
    message, matching `object_store_timeout_seconds`'s own lack of a custom `@field_validator`).
  - M7: an ordinary (non-impersonation) identity's request is observably unaffected: same status
    code, same body, and (per M2) zero additional DB query issued by the identity-resolution step
    itself — confirmed at Verify by code review of M2's short-circuit (a WIRING/dead-code-style
    check: `guard.ensure_live` must never be called unconditionally), not by a runtime assertion
    (this repo's own "assert observable behavior, never internals" convention (CONVENTIONS.md)
    means a black-box test cannot itself prove "zero queries" without inspecting internals; §4
    notes this explicitly rather than fabricate a pytest-level proxy for it).
  - M8: natural TTL expiry (JWT `exp` elapsed, session never explicitly Ended) remains handled
    EXCLUSIVELY by the pre-existing, unmodified `jwt.decode()` `exp` validation — this task's own
    guard is never even reached for an already-expired JWT (decode() raises before `identity` is
    available). No duplicate TTL logic is added anywhere.
</must>
Reject:
<reject>
  - R1: an impersonation identity's session has been explicitly Ended (`revoked_at IS NOT NULL`)
    -> reuses "ERR_AUTH_INVALID_TOKEN" (401) — the SAME code/status the decode step already uses
    for any other invalid-token reason; no new code.
  - R2: the live-check itself cannot complete — DB exception, or the bounded timeout elapses ->
    ALSO "ERR_AUTH_INVALID_TOKEN" (401), indistinguishable from R1 (fail-closed, deliberate no
    oracle on WHY the token was rejected).
  - R3: an impersonation claim names a `session_id` with no matching row at all (structurally
    should not happen via the real Mint path — defense-in-depth only, exercised via a
    directly-constructed forged token in tests) -> "ERR_AUTH_INVALID_TOKEN" (401) — same
    treatment as R1 (fail-closed on absence, not only on explicit revocation).
</reject>
After:
<after>
  - The FIVE identity-resolution call sites named in §0 all consult the SAME live-check before
    trusting an impersonation identity; an End'd session's JWT is rejected on its VERY NEXT
    request to ANY of them, regardless of how much of its own `exp` window remains.
  - A request already past its own auth-check when a concurrent End() commits completes normally
    (no retroactive mid-request cancellation) — the guarantee is "the next request is rejected,"
    matching the sibling task's own framing of what End() itself durably guarantees.
  - An ordinary (non-impersonation) identity's request path is byte-identical in behavior; the
    new guard is constructed and consulted only when `identity.impersonation is not None`.
  - Zero schema/migration changes; zero writes added anywhere; End (`platform_impersonation_
    router.py`, FROZEN) is not touched.
  - Zero new HTTP-facing error codes; every self-service router's documented response set is
    unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Scope is all 5 reachable call sites, not just `authz.py::_resolve_identity` (the one file
    this task's own dispatch brief named) — lowest confidence because it is the single biggest
    departure from what was briefed, and the largest Build-surface driver by far (touches 6+
    files across `domain/`, `infrastructure/` (new file), `api/`, and `application/`, plus 4
    routers' own construction call sites for `GetIdentityUseCase`). If wrong (i.e. Tin wants a
    smaller v1 covering fewer entry points): each call site's own change is small and independent
    — narrowing to N<5 sites is a strict subset of this same Scope, no mechanism redesign needed,
    but SHIPS WITH A KNOWN GAP on the omitted sites, which must be said out loud, not silently
    left implicit.
  ⚠ Fail-CLOSED (a DB-outage-triggered 401 for an impersonating superadmin) vs fail-OPEN — high
    confidence in the security reasoning (matches the `api_keys` precedent; a security-revocation
    check failing open would silently defeat this task's entire purpose), lower confidence that
    Tin's own operational tolerance agrees a superadmin transiently losing impersonation access
    during a real DB outage is an acceptable cost (it is superadmin-only and self-recovering, but
    it IS a new failure mode with no precedent in this exact feature). If wrong: flipping to
    fail-open is a one-line change in `DbImpersonationSessionGuard`'s except-branch — bounded,
    no shape ripple — but reopens exactly the gap this task exists to close during any DB blip.
  - [x] No new HTTP error code — reuses `AUTH_TOKEN_INVALID`/`ERR_AUTH_INVALID_TOKEN` — confirmed
    via direct read of `core/error_catalog.py:80`; consistent with fail-closed's own "no oracle on
    why" reasoning.
  - [x] `impersonation_live_check_timeout_seconds` default = 2.0s — a policy knob mirroring
    `object_store_timeout_seconds`'s style; cheaply changed via Settings/env var without any
    contract change.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: End then immediate reuse is rejected — GET /admin/cache (get_identity)   # M4a, R1
  Given a SUPERADMIN has minted an impersonation session (session_id SID) against an eligible
    target user U (any non-superadmin role), and holds the resulting impersonation JWT
  When the SUPERADMIN calls DELETE /admin/platform/impersonation/sessions/{SID} with their
    ORIGINAL (non-impersonation) token — it returns 204
  And the SAME impersonation JWT (for U) is IMMEDIATELY replayed as GET /admin/cache
  Then the replayed request is rejected 401 "ERR_AUTH_INVALID_TOKEN" — even though the JWT's own
    `exp` claim has not yet elapsed
  And the session-store row's revoked_at/revoked_reason are unchanged from End's own write (this
    check performs no write of its own)

Scenario: End then immediate reuse is rejected — GET /admin/auth/me (GetIdentityUseCase)   # M4e, R1
  Given the same End'd session as above
  When the impersonation JWT is replayed as GET /admin/auth/me
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"

Scenario: End then immediate reuse is rejected — GET /admin/catalog/models (get_current_identity)   # M4c, R1
  Given the same End'd session as above
  When the impersonation JWT is replayed as GET /admin/catalog/models
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"

Scenario: End then immediate reuse is rejected — GET /admin/usage (_extract_identity)   # M4d, R1
  Given the same End'd session as above
  When the impersonation JWT is replayed as GET /admin/usage
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"

Scenario: End then immediate reuse is rejected — GET /admin/audit (_resolve_identity, permission-gated)   # M4a, R1
  Given a SUPERADMIN impersonates an ADMIN-role target (ADMIN holds Permission.AUDIT_READ,
    confirmed authz.py:74-75), then Ends that session
  When the impersonation JWT is replayed as GET /admin/audit
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN" — proving the require_permission/
    _resolve_identity path (the one call site the originating brief named) is closed, not just
    the 4 others

Scenario: An ordinary (non-impersonation) identity's request is unaffected   # M7
  Given an ordinary OWNER token minted via the existing self-service /admin/auth/login flow (no
    impersonation claim at all)
  When that token calls GET /admin/cache
  Then the response is 200, byte-identical in shape to before this task
  And no new failure mode is introduced for any non-impersonation caller

Scenario: The live-check itself failing (not "row says revoked") also rejects — fail-closed   # M3c, R2
  Given a LIVE (not-yet-ended, not-yet-expired) impersonation session, and the guard's own DB
    read is forced to fail (simulated fault injection)
  When the impersonation JWT calls a self-service endpoint
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN" — never treated as valid merely because
    validity could not be confirmed within the bounded timeout

Scenario: A forged claim naming a session_id with no matching row is rejected   # M3b, R3
  Given a directly-constructed JWT (test-only, bypassing Mint) whose `impersonation.session_id`
    matches no row in impersonation_sessions at all
  When that JWT calls a self-service endpoint
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN" — absence is treated identically to explicit
    revocation, not as "not my problem, let it through"

Scenario: A naturally expired, never-explicitly-ended session is ALREADY rejected today — confirms M8, no regression
  Given an impersonation JWT whose own `exp` claim has elapsed, and the session-store row was
    never explicitly Ended (revoked_at IS NULL)
  When that JWT calls a self-service endpoint
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN" via the PRE-EXISTING, unmodified jwt.decode()
    exp check — this task's own new guard is never even reached (decode() raises first)
  And this confirms this task does not need to, and does not, duplicate TTL enforcement

Scenario: A request already past its auth-check completes normally despite a concurrent End   # After (boundary), deliberate
  Given an impersonation JWT's auth-check has ALREADY passed and the request is executing its
    own business logic, at the exact instant a DIFFERENT concurrent call Ends that same session
  When the in-flight request's own response is produced
  Then it completes normally/unaffected — no retroactive mid-request cancellation is attempted or
    implied by this task's contract
  And the VERY NEXT request bearing that same JWT is rejected per the first scenario above

Scenario: Concurrent End vs. replay races never produce a torn/inconsistent verdict   # Concurrency
  Given many concurrent replay attempts of a live impersonation JWT racing one concurrent End
    call against the same session_id, under Postgres READ COMMITTED
  When all requests resolve
  Then every replay that observed the pre-commit state succeeds and every replay that observed
    the post-commit state is rejected 401 — none succeeds after End's own commit, and no request
    receives an inconsistent/partial verdict (Postgres MVCC snapshot consistency + End's own
    single atomic conditional UPDATE, both already true today, are sufficient — no new locking
    is added by this task's own read-only check)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
═══════════════════════════════════════════════════════════════════════════════
PART A — new domain port (tenants/domain/ports.py — ADDITIVE, zero framework imports)
═══════════════════════════════════════════════════════════════════════════════

class ImpersonationSessionGuard(Protocol):
    async def ensure_live(self, impersonation: ImpersonationContext) -> None:
        """Raise InvalidTokenError (tenants/domain/errors.py) iff the session named by
        impersonation.session_id is not live, OR liveness cannot be confirmed within the
        adapter's own bounded timeout (fail-CLOSED — no distinction surfaced to the
        caller). No-op (returns None) iff the session IS live. Never called for an
        ordinary (non-impersonation) identity — see ensure_impersonation_session_live."""
        ...

═══════════════════════════════════════════════════════════════════════════════
PART B — new domain helper (tenants/domain/authz.py — ADDITIVE, zero framework imports)
═══════════════════════════════════════════════════════════════════════════════

async def ensure_impersonation_session_live(
    identity: Identity, guard: ImpersonationSessionGuard
) -> None:
    """The ONE place the 'zero overhead for ordinary identities' guarantee is enforced:
    guard.ensure_live() is called iff identity.impersonation is not None. An ordinary
    identity's resolution never constructs a DB-bound guard call, never touches
    impersonation_sessions."""
    if identity.impersonation is not None:
        await guard.ensure_live(identity.impersonation)

═══════════════════════════════════════════════════════════════════════════════
PART C — new infrastructure adapter (tenants/infrastructure/impersonation_session_guard.py — NEW FILE)
═══════════════════════════════════════════════════════════════════════════════

class DbImpersonationSessionGuard:
    """Fail-closed, bounded-timeout live-check against impersonation_sessions — the SAME
    compound predicate impersonation-session-lifecycle TASK.md §3 already declares as
    session validity's sole source of truth (revoked_at IS NULL AND expires_at > now()).
    Constructed per-request with the ALREADY-RESOLVED AsyncSession (get_session's
    FastAPI dependency-cache means this is the SAME session/connection the route
    handler's own body uses — no second DB connection). Zero writes; zero new index
    (id is already the primary key)."""

    def __init__(self, *, session: AsyncSession, timeout_seconds: float) -> None:
        self._session = session
        self._timeout_seconds = timeout_seconds

    async def ensure_live(self, impersonation: ImpersonationContext) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                row = (
                    await self._session.execute(
                        select(
                            ImpersonationSessionRow.revoked_at, ImpersonationSessionRow.expires_at
                        ).where(ImpersonationSessionRow.id == impersonation.session_id)
                    )
                ).one_or_none()
        except Exception as exc:
            # fail-CLOSED: timeout, DB error, or any other exception -> reject. Deliberate
            # deviation from RedisCooldownGate/RedisBudgetGuard's fail-OPEN convention (§1
            # Framings) — this is a credential-revocation decision, not an availability gate.
            structlog.get_logger().warning(
                "impersonation_live_check_failed",
                session_id=str(impersonation.session_id),
                error=type(exc).__name__,
            )
            raise InvalidTokenError from exc
        if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
            raise InvalidTokenError

═══════════════════════════════════════════════════════════════════════════════
PART D — wiring at the 5 existing call sites (each: +1 param, +1-2 lines, 0 new except clauses)
═══════════════════════════════════════════════════════════════════════════════

1. tenants/domain/authz.py::_resolve_identity — gains `session: Annotated[AsyncSession,
   fastapi.Depends(get_session)]`; after a successful decode, inside the EXISTING try block:
     identity = token_service.decode(token)
     await ensure_impersonation_session_live(
         identity, DbImpersonationSessionGuard(
             session=session, timeout_seconds=request.app.state.settings.impersonation_live_check_timeout_seconds
         ),
     )
     return identity
   (existing `except InvalidTokenError: raise AUTH_TOKEN_INVALID.exc() from None` catches both
   the decode failure AND this new call's failure — unchanged.)

2. keys/api/deps.py::get_identity — currently takes only (token, tokens); gains `request: Request`
   and `session: Annotated[AsyncSession, Depends(get_session)]`; same pattern as (1).

3. catalog/api/deps.py::get_current_identity — already takes `request`; gains `session`; same
   pattern as (1).

4. usage/api/router.py::_extract_identity — already takes `request`; gains `session`, threaded
   through `_require_usage_read`/`_require_ops_read` (both must also gain the parameter to pass
   it through); same pattern as (1).

5. tenants/application/use_cases.py::GetIdentityUseCase — constructor widened to accept
   `guard_factory` (or equivalent — a domain-Protocol-typed dependency, NEVER a direct
   `infrastructure/` import per CONVENTIONS.md's application-layer rule); `.execute()` becomes
   `async def execute(self, token: str) -> Identity`. Updated at its ONE shared DI wiring
   (tenants/api/deps.py::get_identity_use_case) AND its 3 direct-construction call sites
   (agent_oauth/api/device_approval_router.py:117, auth/api/oidc_admin_router.py:134,
   proxy/api/provider_keys_admin_router.py:105) — each now `await`s `.execute(token)`.

═══════════════════════════════════════════════════════════════════════════════
PART E — error responses (NO new HTTP-facing error code)
═══════════════════════════════════════════════════════════════════════════════

Every one of the 5 call sites, on ANY guard rejection (R1 explicit-End, R2 check-failure/timeout,
R3 missing-row), raises the SAME EXISTING error each already raises for any other decode failure:
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }   (core/error_catalog.py:80, AUTH_TOKEN_INVALID —
                                                reused verbatim, unmodified)
No self-service router's documented response set changes shape.

═══════════════════════════════════════════════════════════════════════════════
PART F — new config knob (core/config.py — mirrors object_store_timeout_seconds's exact style)
═══════════════════════════════════════════════════════════════════════════════

  impersonation_live_check_timeout_seconds: float = Field(default=2.0, gt=0)
  # GATEWAY_IMPERSONATION_LIVE_CHECK_TIMEOUT_SECONDS — bounds the guard's own DB read; a
  # struggling DB fails THIS check fast/clean (401) rather than compounding into a long ambient
  # hang. Fail-closed on elapse (Part C). No ambient statement_timeout exists today (confirmed at
  # Ground) — this is a new, additive bound, not a duplicate of an existing mechanism.

Schema/access pattern: reads ONLY the EXISTING impersonation_sessions.id/revoked_at/expires_at
  columns (id already PK-indexed) — ZERO new columns, ZERO new index, ZERO new migration, ZERO
  new writes anywhere. End (platform_impersonation_router.py, FROZEN @ impersonation-session-
  lifecycle §3) is not touched and remains the sole writer of revoked_at.

IO note — design for failure: bounded timeout (Part C/F) + fail-CLOSED (deliberate deviation from
  this repo's cooldown/budget-gate fail-OPEN convention — §1 Framings) + no retry (a single
  bounded attempt is sufficient; retrying would extend hot-path latency for impersonation-bearing
  requests with no security benefit, since a failed attempt already fails safely) + no circuit
  breaker (traffic volume for this check is inherently tiny — superadmin-only, ≤1 concurrent
  session per superadmin, TTL-capped — so a breaker's load-shedding benefit is moot; every other
  concurrent /admin/* request already depends on the same Postgres via its own get_session
  dependency regardless of this check's existence).
```

Glossary deltas: none — this task introduces no new domain term; it closes an already-named gap
  ("impersonation session" liveness, per the sibling task's own proposed, fold-pending Glossary
  entry) with a mechanism, not a new concept.

Status: DRAFT
Reported: no — drafted by add-design for the orchestrator's review; the freeze itself (Status ->
  FROZEN @ vN) and its mandatory HARD-STOP security review are the human/orchestrator's own next
  step, never this draft's to declare.

Lowest-confidence flag for the freeze (surfaced here, not decided here):
  ⚠ [contract/scope] This contract's Build surface is 5 call sites across 6+ files (2 in
    `domain/`, 1 NEW in `infrastructure/`, 2 in `api/`, 1 in `application/` + its 4 downstream
    router call sites) — materially larger than "patch the one auth dependency file" that this
    task's own originating brief assumed. The MECHANISM (DB-backed, fail-closed, Protocol+adapter
    shape) is not in question; what deserves an explicit freeze-time decision is whether to ship
    all 5 in this one task, or knowingly defer some (with the omitted ones' gap stated out loud,
    not silently implied closed). Recommendation: ship all 5 — a partial fix is a false-confidence
    outcome for a task whose entire purpose is closing a security gap Tin is holding a production
    deploy on.
  ⚠ [spec] Fail-closed's operational cost (a superadmin transiently loses impersonation access
    during a genuine DB outage) is a deliberate, justified, but NEW failure mode with no directly
    matching precedent in this exact feature (the closest precedent, api_keys revocation, imposes
    the identical cost on ordinary /v1/* callers today, so this is consistent with, not a novel
    departure from, established codebase posture — but it is still worth Tin's explicit eyes-open
    sign-off given the HARD-STOP security weight of this task).

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY (new
     terms declared as a Glossary delta) + the bundle's lowest-confidence flag was surfaced at
     the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: >=90% of new/modified lines (the new Protocol, domain helper, infrastructure
  adapter, config validator, and the small diff at each of the 5 wiring sites)

Test-design constraint (deliberate): NOT ONE test imports a not-yet-existing symbol (no
  `ImpersonationSessionGuard`, `DbImpersonationSessionGuard`, or `ensure_impersonation_session_live`
  is imported anywhere in this suite) — every test drives the ALREADY-EXISTING, real HTTP surface
  (real Mint/End endpoints, real self-service GET routes) plus the ALREADY-EXISTING
  `app.state.token_service.issue(impersonation=..., ttl_seconds=...)` kwargs (shipped by the
  sibling task). This keeps collection green regardless of Build's internal naming choices —
  every failure is an ASSERTION mismatch (expected 401, got 200), never an ImportError/collection
  error, confirmed by actually running the suite below.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_end_then_replay_rejected_admin_cache: mint (POST Mint, real endpoint) / GET /admin/cache
    succeeds (200) / End (DELETE, real endpoint) 204 / replay SAME token -> assert 401
    ERR_AUTH_INVALID_TOKEN · covers: M4b, R1
  - test_end_then_replay_rejected_admin_me: same shape, target GET /admin/auth/me · covers: M4e, R1
  - test_end_then_replay_rejected_admin_catalog_models: same shape, target GET /admin/catalog/models
    (seeds one minimal active model + pricing_snapshot first, else this route's own unrelated
    409 ERR_CATALOG_EMPTY on an empty catalog would mask the auth-layer signal — confirmed by
    an actual pre-fix test run, corrected before this draft) · covers: M4c, R1
  - test_end_then_replay_rejected_admin_usage: same shape, target GET /admin/usage · covers: M4d, R1
  - test_end_then_replay_rejected_admin_audit: mint impersonating an ADMIN-role target (holds
    Permission.AUDIT_READ), target GET /admin/audit · covers: M4a, R1 — the require_permission/
    _resolve_identity path, the one call site the originating brief named
  - test_ordinary_identity_unaffected: ordinary OWNER login (no impersonation) -> GET /admin/cache
    -> assert 200, byte-identical shape · covers: M7 (confirmatory/regression-style — legitimately
    GREEN both before and after Build; "zero added DB query" is a code-review-time WIRING check
    per M7's own note, not a black-box assertion — see Coverage/Verify, not fabricated here)
  - test_naturally_expired_session_already_rejected_today: mint, do NOT End, wait past (or
    directly construct a token with) an elapsed exp claim -> replay -> assert 401
    ERR_AUTH_INVALID_TOKEN · covers: M8 (confirmatory — ALREADY GREEN today via the pre-existing
    jwt.decode() exp check; included to document/pin the boundary, not as red evidence)
  - test_forged_session_id_with_no_matching_row_rejected: directly construct a token via
    app.state.token_service.issue(impersonation=ImpersonationContext(session_id=uuid.uuid4(), ...))
    naming a session_id that was never minted -> replay -> assert 401 ERR_AUTH_INVALID_TOKEN
    · covers: M3b, R3
  - test_live_check_infra_failure_fails_closed: a genuinely LIVE session, DB read forced to raise
    via a surgical monkeypatch (patches `AsyncSession.execute`, filtered to the
    `impersonation_sessions` SELECT only — a library-level patch target, stable regardless of
    Build's internal function/class names) -> replay -> assert 401 ERR_AUTH_INVALID_TOKEN
    · covers: M3c, R2
  - test_concurrent_end_vs_replay_no_torn_verdict: asyncio.gather one End call against N
    concurrent replay attempts of the same live impersonation token -> assert every replay that
    lands is either a clean success (pre-commit) or a clean 401 (post-commit), zero partial/
    inconsistent results, and re-verify final DB state (revoked_at set exactly once) · covers:
    Concurrency scenario (mirrors this repo's own `asyncio.gather` dual-dispatch race-test idiom,
    per impersonation_session_lifecycle's own precedent)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build. Of the 10 tests
  above, 8 are RED today (fail because the guard does not exist yet: `test_end_then_replay_*` x5,
  `test_forged_session_id_*`, `test_live_check_infra_failure_*`,
  `test_concurrent_end_vs_replay_*`) and 2 are confirmatory/already-green
  (`test_ordinary_identity_unaffected`, `test_naturally_expired_session_already_rejected_today`) —
  included for regression coverage and to pin the exact boundary of what this task does NOT need
  to (re)implement, not counted as red evidence. Verified by actually running the suite (see
  below): all 10 collect cleanly (zero ImportError); the 8 RED ones fail on an assertion mismatch
  (expected 401, actual 200 — confirmed the right kind of red, not a fixture/harness bug); the 2
  confirmatory ones already pass.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/tenants/domain/ports.py` (NEW `ImpersonationSessionGuard` Protocol —
    additive)
  `apps/gateway/src/gateway/tenants/domain/authz.py` (NEW `ensure_impersonation_session_live`
    helper; `_resolve_identity` gains a `session` param + 1 call, inside its existing try block)
  `apps/gateway/src/gateway/tenants/infrastructure/impersonation_session_guard.py` (NEW file —
    `DbImpersonationSessionGuard`)
  `apps/gateway/src/gateway/keys/api/deps.py` (`get_identity` gains `request`+`session` params +
    1 call)
  `apps/gateway/src/gateway/catalog/api/deps.py` (`get_current_identity` gains `session` param +
    1 call)
  `apps/gateway/src/gateway/usage/api/router.py` (`_extract_identity` gains `session` param + 1
    call; `_require_usage_read`/`_require_ops_read` thread `session` through)
  `apps/gateway/src/gateway/tenants/application/use_cases.py` (`GetIdentityUseCase` constructor
    widened + `.execute()` becomes async)
  `apps/gateway/src/gateway/tenants/api/deps.py` (`get_identity_use_case` DI wiring updated for
    the widened constructor)
  `apps/gateway/src/gateway/agent_oauth/api/device_approval_router.py` (its direct
    `GetIdentityUseCase(...)` construction + `.execute(token)` call site updated to match)
  `apps/gateway/src/gateway/auth/api/oidc_admin_router.py` (same, its own direct construction site)
  `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py` (same, its own direct
    construction site)
  `apps/gateway/src/gateway/core/config.py` (add `impersonation_live_check_timeout_seconds`)
  `apps/gateway/tests/impersonation_live_session_guard/` (this task's own test directory)
Strategy (ordered batches): 1. domain (ImpersonationSessionGuard Protocol in ports.py +
  ensure_impersonation_session_live helper in authz.py — pure, zero framework imports, confirm
  they type-check standalone) 2. infrastructure (DbImpersonationSessionGuard — the bounded-
  timeout, fail-closed SELECT; unit-test it in isolation against a real test DB session before
  wiring any call site) 3. wire call site #1 (_resolve_identity, authz.py) + its own scenario
  (GET /admin/audit) green before moving on, to validate the wiring PATTERN once cheaply 4. wire
  the remaining 4 call sites (get_identity, get_current_identity, _extract_identity +
  its 2 wrappers, GetIdentityUseCase + its 4 call sites) using the SAME now-proven pattern 5. add
  impersonation_live_check_timeout_seconds to config.py 6. run the full new suite green, then the
  full existing gateway suite (regression: zero change to any ordinary-identity test's outcome)
  7. the 2 concurrency/fault-injection tests last (highest setup cost, benefit most from the
  mechanism already being proven correct on the simple path).

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. The Advisor 3-lens verdict and the Refute-read verdict are both measured by `add.py audit` (`advisor_verdict_unrecorded` · `refute_unrecorded`) — neither is engine-blocked; a human spot-audit is the backstop for any finding the AI did not surface or record. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
