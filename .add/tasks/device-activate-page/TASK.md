# TASK: /activate device-approval page over existing approve/deny + verification_uri default

slug: device-activate-page · created: 2026-07-17 · stage: production · sensitivity: security · risk: high
milestone: commercial-self-serve
component: gateway, dashboard
autonomy: conservative   <!-- lowered from auto: SECURITY + risk:high authorization surface — the engine refuses an unguarded completion (`unguarded_high_risk_auto`); the freeze is a human decision. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- BACKEND (gateway)
  - `agent_oauth/api/device_approval_router.py` — the FROZEN authed surface (device-approval-flow §3). `device_approve` / `device_deny` (POST /oauth/device/approve|deny). Shared helpers to REUSE: `_require_identity(request, session)` (resolves Identity from session JWT via `GetIdentityUseCase`, ANY role — no role restriction), `_normalize_user_code(raw)` (whitespace/upper/XXXX-XXXX), `_parse_approval_body` (bounded 4096-byte read → 422 `invalid_request`), `_ApprovalBody` (`extra="ignore"`, drops injected tenant_id/user_id). Approve/deny map outcomes to DISTINCT codes: 404 `authorization_not_found` · 409 `authorization_not_pending` · 410 `authorization_expired` · 401 `unauthorized` · 429 `rate_limited`+Retry-After.
  - `agent_oauth/api/device_authorize_router.py` — `device_authorize` (POST /oauth/device/authorize, PUBLIC). Builds RFC 8628 body; reads `settings.agent_oauth_verification_uri` (line ~133); emits `verification_uri_complete = f"{uri}?user_code={code}"` ONLY when uri non-empty (line ~142). This is the sole consumer of the config default this task changes.
  - `agent_oauth/domain/entities.py:DeviceAuthorization` — a pending grant carries ONLY `id · status · scope · interval_seconds · created_at · expires_at`; `tenant_id`/`user_id` are `None` until approval; NO agent name, NO budget. `AgentPrincipal` (name + `monthly_budget_usd`) attaches to a token POST-mint, not to a pending grant.
  - `agent_oauth/infrastructure/repository.py:SqlAlchemyAgentOAuthRepository.get_by_user_code_hash(user_code_hash) -> DeviceAuthorization | None` — the lookup a preview use-case REUSES (returns the row regardless of status/expiry; the caller decides previewability).
  - `agent_oauth/infrastructure/ip_rate_limiter.py:AgentOAuthIpRateLimiter.check(ip, limit)` — fixed 60 s window, FAIL-OPEN on Redis error, keyed by an arbitrary string (approve/deny key off `f"approve:{user_id}"`). Preview reuses this, keyed `f"preview:{user_id}"`.
  - `core/config.py` — `agent_oauth_verification_uri: str = ""` (line ~997, empty today) · `environment: str = "dev"` (line 86) · existing boot-guard precedent at line ~988 (`jwt_secret` must be set when `environment not in ("dev","test")`) · positive-knob validators (~line 1060) · `agent_oauth_default_budget_usd` (~line 1032, the cap applied to minted tokens). NO public-origin/app-base-url setting exists anywhere.
- FRONTEND (dashboard)
  - `app/api/gw/[...path]/route.ts:proxyRequest` — the BFF passthrough. `/oauth/device/*` is neither `/admin/*` (control-plane 401→cookie-clear) nor `/v1/*` (data-plane); it forwards with the session-JWT Bearer and passes any 401/4xx VERBATIM without clearing the cookie — exactly the transport an authed page needs to reach approve/deny/preview.
  - `components/invoices/InvoiceStatusSeal.tsx:InvoiceStatusSeal` — the seal idiom to TRANSLATE: `Badge variant + Lock icon + sr-only final-state copy` (state by icon+text, not color alone). Source for the new `AuthorizationSeal` (pending / granted / denied / expired).
  - `app/(app)/app/layout.tsx:DashboardLayout` (wraps `DashboardShell`) + `app/(app)/app/page.tsx` (comment: "no cookie → 307 /login before this renders") — the authed-shell + cookie-presence guard pattern. The `(app)` group adds no path segment; today every child lives under `app/` → `/app/*`.
  - `lib/bff-client.ts:handleBffResponse` — on `ERR_AUTH_SESSION_EXPIRED|ERR_AUTH_NO_SESSION` sets `window.location.href = "/login"` with NO return-path preservation (the round-trip gap).
  - `app/(auth)/login/page.tsx` — reads only `sso_error` today; NO `next`/`redirect`/`callbackUrl` convention exists. No `middleware.ts` in the app.

Context (working folder): milestone `commercial-self-serve` shared decision — "/activate reuses the existing session-JWT approve/deny endpoints unchanged; adds no new auth path; user-code entry is rate-limited by the existing per-IP/per-user limiters." UI signature: the approval card mirrors the `InvoiceStatusSeal` dated-header idiom translated to an authorization document. Config knobs are env-driven (`GATEWAY_*`).

Honors (patterns / conventions): no outbound IO without timeout+bounded retry+circuit breaker (BFF already does; preview reuses the fail-open limiter) · plaintext `user_code` NEVER logged (approve/deny take it in a POST BODY, never a URL — preview MUST do the same) · tenant-scoped/unknown lookups return byte-identical responses (appsec enumeration-oracle rule; `AgentPrincipalNotFoundError` docstring precedent) · config misconfig → clear BOOT error, mirror line-988 guard (EmptyUpstreamKeyError family) · Airier tokens + WCAG 2.2 AA floor + keyboard-first (ui-designer default requirement) · frozen device-approval-flow approve/deny wire shape stays byte-identical (additive-only to its file).

Anchors the contract cites: `device_approval_router` helpers (`_require_identity`, `_normalize_user_code`, `_parse_approval_body`, `_ApprovalBody`) · `repository.get_by_user_code_hash` · `AgentOAuthIpRateLimiter.check` · `DeviceAuthorization` (status/scope/expires_at) · `config.agent_oauth_verification_uri` + `config.environment` + line-988 boot-guard · `device_authorize.verification_uri_complete` builder · BFF `proxyRequest` · `InvoiceStatusSeal` · login `next` param.

Issues/Risks (→ feed §1):
- R-A (data-model gap, HIGH): "review agent identity + budget" is only PARTLY backed. `scope` + `expires_at` exist; a per-agent IDENTITY and per-agent BUDGET do NOT exist on a pending grant. Any displayed "identity" would be either absent or an UNVERIFIED client-supplied string (consent-phishing surface). Budget is only the system-wide `agent_oauth_default_budget_usd` cap that WILL apply — honest but not agent-specific. → §1 must scope-cut identity/budget to server-known, honestly-labeled facts.
- R-B (enumeration oracle, SECURITY): a preview endpoint returning grant facts on a valid code is the reconnaissance surface. Unknown vs expired vs approved/denied/consumed MUST return one byte-identical error, else it is a validity oracle over the short `user_code` space. Approve/deny already distinguish 404/409/410 — that residual oracle is the FROZEN task-1 contract (per-user rate-limited, and probing via approve MUTATES/binds the grant, so it is self-defeating and audit-visible); this task must NOT widen it and MUST make preview strictly non-leaky.
- R-C (verification_uri default, SECURITY): default is `""`; there is no public-origin config to derive from. A hardcoded absolute default silently points at localhost in prod. → define a dev default + a boot-guard (mirror line 988) rejecting empty/localhost/non-https when `environment not in ("dev","test")`.
- R-D (open redirect, SECURITY): preserving the code through login needs a `next` param that does not exist today; an unvalidated `next` is an open-redirect. → `next` MUST be a same-origin relative path only (reject absolute URLs / `//host` / scheme).
- R-E (code in URL): RFC 8628 `verification_uri_complete` puts the code in the query string (standard, short-lived) and the /activate page reads `?user_code=`; the preview/approve CALLS must use a POST body so the code never re-enters a server access log.
- R-F (placement): `(app)` group = no path segment but the guard/shell live only under `app/`; a root `/activate` needs its own authed guard + a FOCUSED layout (milestone: "focused single-purpose screen", not the full nav shell).

Related intent: milestone `commercial-self-serve` exit criterion — "A headless client's user code can be entered at /activate by a logged-in member, showing the requesting agent + scopes + budget before approve/deny; the device-authorize response carries a non-empty verification_uri by default." EXTENDS agent-gateway-v1 (RFC 8628 device flow shipped server-side, missing its human approval surface). GLOSSARY: device authorization grant · user_code · scope.

Ground SHA: 102ec65 — symbols cited above; any line number is "as of" this commit and may drift (re-resolve at VERIFY).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A logged-in member enters an RFC 8628 user_code at /activate, PREVIEWS the pending grant's server-known facts, and approves or denies it over the existing session-JWT endpoints — plus a sensible, prod-guarded verification_uri default so the device-authorize response points at a real page.

Framings weighed:
- (chosen) Thin authorization consent screen over a new non-leaky PREVIEW read + the FROZEN approve/deny — preview shows only server-known facts (scope, expiry, the default budget cap), collapses all non-previewable states to one uniform error, adds no new auth path. Honest, ships within the frozen-reuse mandate.
- (rejected) Extend POST /oauth/device/authorize to accept a client-declared agent name/budget and display them — gives a richer "identity" card but the strings are UNVERIFIED and attacker-controlled: a consent-phishing surface on an authorization screen. Out of the frozen-reuse mandate; deferred as a separate spec delta if ever wanted (with a "claimed/unverified" treatment).
- (rejected) GET /oauth/device/preview?user_code=… (REST-pure read) — puts the secret code in a URL/query → server access logs, browser history, referrer. Violates the never-log-the-code rule. Preview uses a POST body like approve/deny.

Must:
<must>
  - M1 — /activate renders under an authed context; an unauthenticated visit to `/activate?user_code=CODE` round-trips through /login and RETURNS to /activate with the same code preserved (validated same-origin `next`).
  - M2 — On load with a `?user_code=`, the page auto-populates the code field; the member may also type it (loose input: lowercase/spaces/missing dash accepted, normalized to XXXX-XXXX).
  - M3 — POST /oauth/device/preview (session-JWT required, ANY role) on a PENDING, non-expired grant returns its server-known facts: `scope`, `expires_in` (seconds remaining), `interval`, `status:"pending"`, and the default monthly budget cap that WILL apply (from `agent_oauth_default_budget_usd`, labeled a system default — NOT agent-specific).
  - M4 — The approval card presents scope + expiry + default-budget with an AuthorizationSeal (translated InvoiceStatusSeal idiom: icon+text state, not color alone) and Approve / Deny actions wired to the existing POST /oauth/device/approve|deny VERBATIM (body `{user_code}`, session-JWT via BFF).
  - M5 — Preview, approve, and deny each pass the user_code in a POST BODY (never a URL); the plaintext code is never logged client- or server-side.
  - M6 — Preview is rate-limited by the existing per-USER limiter keyed `preview:{user_id}` (fail-open on Redis error), independent of the approve/deny budget.
  - M7 — All non-previewable states — unknown code, expired, already approved, denied, consumed — return ONE byte-identical response (404 `authorization_not_previewable`); the page shows a single generic "invalid or expired" message for all of them (no oracle, and clean UX).
  - M8 — `agent_oauth_verification_uri` has a non-empty dev default (`http://localhost:3000/activate`) so `verification_uri` + `verification_uri_complete` are populated by default; approve/deny/authorize wire shapes stay byte-identical otherwise.
  - M9 — A boot-guard rejects startup when `environment not in ("dev","test")` and `agent_oauth_verification_uri` is empty, points at localhost/127.0.0.1, or is non-https (mirrors the line-988 jwt_secret guard; clear coded boot error).
  - M10 — WCAG 2.2 AA + keyboard-first: labeled code input, visible focus-visible, ≥44px targets, seal/error state conveyed by icon+text (not color), expiry countdown via aria-live polite, errors via role="alert", correct landmark order.
</must>
Reject:
<reject>
  - unknown / expired / already-approved / denied / consumed user_code (preview) -> "authorization_not_previewable"  (404, byte-identical for every case)
  - missing / malformed / oversized preview body (>4096 B, non-string user_code) -> "invalid_request"  (422)
  - missing or invalid session JWT (preview / approve / deny) -> "unauthorized"  (401)
  - per-user preview rate window exceeded -> "rate_limited"  (429 + Retry-After)
  - `next` that is not a same-origin relative path (absolute URL, `//host`, scheme, backslash-smuggled) -> ignored; login falls back to the default post-login destination (never redirected off-origin)
  - production boot with empty / localhost / non-https verification_uri -> boot error "INVALID_AGENT_OAUTH_VERIFICATION_URI" (process refuses to start)
</reject>
After:
<after>
  - A member holding a valid pending code sees its scope, time-to-expiry, and the default budget cap, and can grant or deny it; the agent's poll then succeeds (approved) or is rejected (denied) unchanged by task-1's flow.
  - device-authorize responses carry a non-empty `verification_uri` (+ `verification_uri_complete`) by default; production cannot boot pointing at localhost.
  - An attacker enumerating codes against preview gets one indistinguishable 404 for every non-previewable state and is per-user rate-limited; no agent facts leak for a non-pending code.
  - approve/deny/authorize wire contracts are byte-identical to pre-task; no new auth path exists.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A1 — Showing only server-known facts (scope, expiry, system default budget) and NO per-agent identity/budget is an acceptable read of the milestone's "requesting agent + scopes + budget." LOWEST confidence: the milestone/exit-criteria wording implies a per-agent identity/budget that the data model does not carry pre-approval; the honest options are scope-cut (chosen) vs. displaying unverified client-declared strings (phishing risk). If wrong: the human wants richer identity → re-opens as a change request that must FIRST extend the frozen authorize contract (new spec delta), not a UI-only tweak.
  ⚠ A2 — Preview MUST collapse unknown/expired/used to one uniform 404 even though the FROZEN approve/deny distinguish 404/409/410. LOW confidence only on whether a reviewer accepts the deliberate asymmetry: preview is pre-commit reconnaissance (must not be an oracle); approve/deny are terminal, mutating, rate-limited actions on a code already held. If wrong (reviewer wants preview to mirror approve/deny codes): re-introduces a validity oracle — I would push back as an appsec HARD-STOP rather than comply silently.
  - [ ] A3 — /activate is a FOCUSED authed screen (its own minimal layout + cookie guard), not wrapped in the full DashboardShell nav — confirm vs. placing it at `(app)/app/activate` (`/app/activate`) inside the existing shell; milestone says "focused single-purpose screen" and verification_uri is "/activate" (root).
  - [ ] A4 — Dev default origin is `http://localhost:3000` (the dashboard dev port) and the value is the FULL page URL `…/activate` (RFC 8628 verification_uri is the page the human visits) — confirm the dashboard dev origin/port.
  - [ ] A5 — Preview gets its OWN rate knob `agent_oauth_preview_rpm` (default 30) rather than sharing `agent_oauth_approve_rpm` — confirm (separate budget prevents page-load previews exhausting the approve allowance).
  - [ ] A6 — The default budget cap shown is `agent_oauth_default_budget_usd` (the value actually applied to minted tokens), labeled "default cap," accepted as the honest "budget" review item.
  - [ ] A7 — Additive changes to `device_approval_router.py` (a file whose approve/deny endpoints are FROZEN) are permitted so long as approve/deny wire behavior is byte-identical; the freeze is at the endpoint/wire level, not file-immutability.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Unauthenticated visit round-trips through login preserving the code   # M1
  Given a visitor with no session cookie
  When they open /activate?user_code=BCDF-GHJK
  Then they are sent to /login with next=%2Factivate%3Fuser_code%3DBCDF-GHJK
  And after logging in they land back on /activate with the code prefilled

Scenario: Prefilled code auto-previews a pending grant   # M2, M3
  Given a logged-in member and a pending, non-expired grant for BCDF-GHJK
  When they open /activate?user_code=BCDF-GHJK
  Then POST /oauth/device/preview (body {user_code}) returns 200 { scope, expires_in, interval, status:"pending", default_budget_usd }
  And the code appears in the POST body, never in any request URL or log line

Scenario: Loosely typed code is normalized before preview   # M2
  Given a logged-in member on /activate with no query code
  When they type " bcdfghjk " and submit
  Then it is normalized to BCDF-GHJK and previewed as pending
  And the raw typed string is never logged

Scenario: Approval card shows scope, expiry and default budget with a seal   # M4, M10
  Given a previewed pending grant with scope "proxy"
  When the card renders
  Then it shows scope, a live time-to-expiry, and the default monthly budget cap labeled a system default
  And the pending state is conveyed by an AuthorizationSeal icon+text (not color alone), Approve/Deny are keyboard-reachable with visible focus

Scenario: Approve grants access over the frozen endpoint verbatim   # M4
  Given a previewed pending grant for BCDF-GHJK
  When the member clicks Approve
  Then POST /oauth/device/approve {user_code:"BCDF-GHJK"} returns 200 {status:"approved"} and the card shows a granted seal
  And the approve request/response bytes are identical to the pre-task contract

Scenario: Deny rejects the grant over the frozen endpoint verbatim   # M4
  Given a previewed pending grant for BCDF-GHJK
  When the member clicks Deny
  Then POST /oauth/device/deny {user_code:"BCDF-GHJK"} returns 200 {status:"denied"} and the card shows a denied seal
  And the deny request/response bytes are identical to the pre-task contract

Scenario: Preview is per-user rate limited independently   # M6, R:rate_limited
  Given a member who has exceeded agent_oauth_preview_rpm in the current 60s window
  When they trigger another preview
  Then the response is 429 {error:"rate_limited"} with Retry-After
  And their approve/deny budget (keyed approve:{user_id}) is unaffected

Scenario: Every non-previewable state returns one identical error   # M7, R:authorization_not_previewable
  Given a logged-in member
  When they preview an UNKNOWN code, then an EXPIRED code, then an already-APPROVED code, then a DENIED code, then a CONSUMED code
  Then all five return byte-identical 404 {error:"authorization_not_previewable"}
  And the page shows the same single "invalid or has expired" message for each, leaking no distinction

Scenario: Malformed / oversized preview body is rejected   # R:invalid_request
  Given a logged-in member
  When the preview body is >4096 bytes or user_code is missing/non-string
  Then the response is 422 {error:"invalid_request"}
  And no grant lookup is performed and no state changes

Scenario: Missing/invalid session JWT is rejected on preview   # R:unauthorized
  Given a request to /oauth/device/preview with no or an invalid bearer token
  When it is handled
  Then the response is 401 {error:"unauthorized"}
  And no grant facts are returned

Scenario: An off-origin next is refused (no open redirect)   # R:next not same-origin
  Given an unauthenticated visit to /activate?user_code=BCDF-GHJK&next=https://evil.example/steal
  When login completes
  Then the user is sent to the default post-login destination, never to evil.example
  And the injected absolute/scheme-relative next is ignored

Scenario: verification_uri is populated by default   # M8
  Given default configuration in dev
  When an agent calls POST /oauth/device/authorize
  Then the 200 body carries a non-empty verification_uri "http://localhost:3000/activate" and verification_uri_complete "…?user_code=…"
  And expires_in/interval and all other fields are byte-identical to before

Scenario: Production refuses to boot on an unsafe verification_uri   # M9, R:INVALID_AGENT_OAUTH_VERIFICATION_URI
  Given GATEWAY_ENVIRONMENT=production and agent_oauth_verification_uri empty (or localhost, or http://)
  When the app boots
  Then it fails with a clear INVALID_AGENT_OAUTH_VERIFICATION_URI boot error
  And no request is ever served with a localhost/empty verification_uri in production
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── NEW backend endpoint (session-JWT authed, ANY role; reuses approve/deny helpers) ──
POST /oauth/device/preview   body: { user_code: string }        # code in BODY, never a URL
  200 -> { scope: string, status: "pending", expires_in: int, interval: int,
           default_budget_usd: string }   # default_budget_usd = agent_oauth_default_budget_usd, 2dp, a SYSTEM default cap (not agent-specific)
  401 -> { error: "unauthorized" }                 # missing / invalid session JWT
  404 -> { error: "authorization_not_previewable" } # unknown | expired | approved | denied | consumed — BYTE-IDENTICAL for every case (no oracle)
  422 -> { error: "invalid_request" }              # missing / malformed / oversized (>4096 B) body
  429 -> { error: "rate_limited" } + Retry-After   # per-USER window (key "preview:{user_id}")

# ── UNCHANGED, reused VERBATIM (device-approval-flow FROZEN @ v1 — byte-identical) ──
POST /oauth/device/approve   body: { user_code }   ->  200 { status:"approved" } | 401|422|404|409|410|429
POST /oauth/device/deny      body: { user_code }   ->  200 { status:"denied"   } | 401|422|404|409|410|429

# ── UNCHANGED wire, config default only ──
POST /oauth/device/authorize (public) -> 200 { …, verification_uri, verification_uri_complete? }  # now non-empty by default

# ── Frontend route + BFF path ──
GET  /activate[?user_code=XXXX-XXXX]   # authed page (focused layout + cookie guard); unauth -> /login?next=<same-origin relative, validated>
     browser calls gateway via BFF: POST /api/gw/oauth/device/{preview|approve|deny}  (session-JWT attached server-side)

Config (core/config.py Settings):
  agent_oauth_verification_uri: str = "http://localhost:3000/activate"   # was ""; dev default (A4)
  agent_oauth_preview_rpm: int = 30   # NEW per-user preview limit (A5); positive-knob validated
  boot-guard: environment not in ("dev","test") AND (uri=="" OR host in {localhost,127.0.0.1} OR scheme!="https")
             -> raise INVALID_AGENT_OAUTH_VERIFICATION_URI  (mirrors line-988 jwt_secret guard)

Schema: NO migration, NO new table/column. Read-only reuse of repository.get_by_user_code_hash(user_code_hash)
        (SHA-256 of the normalized code); a preview use-case returns facts ONLY when status=="pending" AND expires_at > now(),
        else raises the single not_previewable outcome. tenant_id/user_id stay None (unbound pre-approval) — nothing tenant-scoped to leak.
```

Glossary deltas:
- device-authorization preview: a read-only, session-authed, rate-limited peek at a PENDING device-authorization grant's server-known facts (scope, time-to-expiry, default budget cap) shown to the human before approve/deny; returns one indistinguishable error for any non-pending state.
- verification_uri (default + prod-guard): the RFC 8628 absolute page URL the human visits to approve; non-empty by default (dev), boot-refused if empty/localhost/non-https in a non-dev/test environment.
Status: FROZEN @ v1 — approved by orchestrator under Tin's standing full-auto directive (2026-07-17).
Reported: yes — flags A1–A7 triaged in-session; rulings below.
Decided at freeze (verbatim rulings):
- A1 ACCEPTED (scope-cut): the approval card shows server-known facts only — scope, time-to-expiry, and the system default budget cap (`agent_oauth_default_budget_usd`, labeled "default cap"). NO client-declared identity strings (consent-phishing surface). Richer per-agent identity = a future spec delta extending the frozen authorize contract, never a UI tweak. [SPEC · open] logged in §7.
- A2 ACCEPTED as drafted: preview collapses ALL non-pending states to one byte-identical 404 `authorization_not_previewable`; the approve/deny 404/409/410 asymmetry is deliberate (terminal, mutating, rate-limited) and MUST NOT be "harmonized" — a reviewer request to mirror codes is an appsec HARD-STOP push-back.
- A3 DECIDED: /activate is a FOCUSED authed screen with its own minimal layout + cookie guard at the root path (matches verification_uri "/activate" and the milestone's "focused single-purpose screen"); NOT inside the DashboardShell nav.
- A4 CONFIRMED: dev default `http://localhost:3000/activate` (dashboard dev origin is :3000); the value is the full page URL per RFC 8628. Kept an INDEPENDENT setting from transactional-email's `dashboard_public_origin` (no cross-task coupling); a "derive when origin set" refinement is logged as an observe delta.
- A5 CONFIRMED: dedicated `agent_oauth_preview_rpm` (default 30), key `preview:{user_id}` — page-load previews never exhaust the approve allowance.
- A6 CONFIRMED: `agent_oauth_default_budget_usd` shown as the honest "budget" item.
- A7 CONFIRMED: additive-only edits to `device_approval_router.py` are permitted; the freeze is at the endpoint/wire level — approve/deny wire bytes re-proven identical at verify.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (backend preview use-case + router branch; FE AuthorizationSeal + sanitizeNext + code-entry lib)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  BACKEND — `gateway/tests/device_activate_preview/test_device_activate_preview.py`
  - test_prefilled_code_previews_pending: seed pending BCDF-GHJK / POST /oauth/device/preview {user_code} / 200 {scope,status:"pending",expires_in>0,interval,default_budget_usd} · covers M2,M3
  - test_preview_default_budget_is_system_cap_2dp: preview a pending grant / assert default_budget_usd == str(agent_oauth_default_budget_usd @ 2dp) NOT agent-specific · covers M3,A6
  - test_preview_uniform_404_byte_identical[unknown|expired|approved|denied|consumed]: PARAMETRIZED over all 5 non-previewable states / preview each / assert status==404 AND resp.content BYTE-IDENTICAL across all five AND body=={"error":"authorization_not_previewable"} — the enumeration-oracle proof · covers M7,R:authorization_not_previewable,R-B
  - test_preview_malformed_body_422: missing/non-string user_code, non-JSON / 422 {"error":"invalid_request"}; no lookup, no state change · covers R:invalid_request
  - test_preview_oversized_body_422: >4096 B (Content-Length + raw) / 422 invalid_request · covers R:invalid_request
  - test_preview_requires_auth_401: no / invalid bearer / 401 {"error":"unauthorized"}; no facts returned · covers R:unauthorized
  - test_preview_is_post_only_code_never_in_url: GET /oauth/device/preview?user_code=… is NOT the interface (405/404) — code only ever travels in a POST body · covers M5,R-E
  - test_preview_per_user_rate_limit_429: preview_rpm=2 app / 3rd preview → 429 {"error":"rate_limited"}+Retry-After; approve budget (approve:{user_id}) unaffected — a subsequent approve still 200 · covers M6,R:rate_limited,A5
  - test_verification_uri_populated_by_default: default dev Settings / POST /oauth/device/authorize / 200 verification_uri=="http://localhost:3000/activate" AND verification_uri_complete=="…?user_code=<code>"; expires_in/interval byte-identical · covers M8
  - test_approve_wire_byte_identical / test_deny_wire_byte_identical: approve/deny a pending grant / 200 {status:"approved"|"denied"} — re-proof the FROZEN device-approval-flow wire bytes are UNCHANGED after the additive preview route · covers M4,A7 (byte-identity guard)
  CONFIG boot-guard — same module (no DB):
  - test_dev_boots_with_default_uri: Settings(environment="dev") → agent_oauth_verification_uri=="http://localhost:3000/activate" · covers M8
  - test_prod_refuses_empty_uri / _localhost_uri / _127_uri / _http_scheme_uri: Settings(environment="production", uri in {"",localhost,127.0.0.1,http://…}) raises ValueError INVALID_AGENT_OAUTH_VERIFICATION_URI · covers M9,R:INVALID_AGENT_OAUTH_VERIFICATION_URI,R-C
  - test_prod_boots_with_https_uri: Settings(environment="production", uri="https://app.example/activate") succeeds · covers M9
  - test_preview_rpm_positive_knob: Settings(agent_oauth_preview_rpm=0) raises INVALID_AGENT_OAUTH_KNOB · covers A5
  FRONTEND — `dashboard/tests-bff/device-activate-page.test.tsx` (+ `dashboard/tests-bff/activate-next-redirect.test.ts`)
  - sanitizeNext: relative "/activate?user_code=X" kept; absolute https://evil, "//evil", "http:…", "\\evil", scheme-smuggled → null (open-redirect guard) · covers M1,R:next,R-D
  - AuthorizationSeal renders state by icon+text (pending/granted/denied/expired), not color alone; sr-only final-state copy · covers M4,M10
  - normalizeUserCode: " bcdf ghjk " → "BCDF-GHJK" (loose input) · covers M2
  - ActivationCard auto-previews on prefilled code via bffPost /api/gw/oauth/device/preview (code in POST body), shows scope+expiry+default-budget, Approve/Deny call approve|deny; not-previewable 404 → single generic message (no state leak) · covers M2,M3,M4,M7
  - loginNextTarget: post-login destination = validated next or default /app/keys · covers M1
</test_plan>

Tests live in: `gateway/tests/device_activate_preview/` `dashboard/tests-bff/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `gateway/src/gateway/agent_oauth/api/device_approval_router.py`  (ADDITIVE: preview route + reuse helpers; approve/deny untouched)
  `gateway/src/gateway/agent_oauth/application/use_cases.py`        (new PreviewDeviceAuthorizationUseCase)
  `gateway/src/gateway/core/config.py`                              (verification_uri default + agent_oauth_preview_rpm + boot-guard validator)
  `gateway/src/gateway/main.py`                                     (register preview route / knob wiring only if needed)
  `dashboard/app/(app)/activate/`                                   (new focused authed page + layout + cookie guard)
  `dashboard/components/agent-activation/`                          (AuthorizationSeal + approval card + code-entry form)
  `dashboard/app/(auth)/login/page.tsx`                             (honor a validated same-origin next)
  `dashboard/lib/bff-client.ts`                                     (ADDITIVE: preserve return path on the /login bounce)
  `./tests/`  and the sibling gateway/dashboard test trees for the above
Strategy (ordered batches):
  1. Config first: verification_uri default + boot-guard + agent_oauth_preview_rpm (+ tests: dev boots, prod refuses on empty/localhost/http). Freeze the config shape before anything depends on it.
  2. Backend preview: PreviewDeviceAuthorizationUseCase (pending+non-expired → facts, else single not_previewable) → additive POST /oauth/device/preview reusing _require_identity/_normalize_user_code/_parse_approval_body + the per-user limiter keyed preview:{user_id}. Prove the uniform-404 across all 5 non-pending states with one parametrized test asserting BYTE-IDENTICAL bodies.
  3. Login next: validate same-origin relative next (reject absolute/scheme/`//`/backslash); bff-client appends encoded return path on bounce.
  4. Frontend /activate: focused authed route + cookie guard; code-entry (loose input), auto-preview on ?user_code=; AuthorizationSeal translated from InvoiceStatusSeal; Approve/Deny over BFF; all states (idle/prefilled/loading/pending/granted/denied/not-previewable/rate-limited/network/unauth). WCAG-AA + keyboard-first verified on a live authed render.
  5. Cross-check approve/deny wire bytes unchanged; refute-read the enumeration surface.

Persona (required): appsec-engineer (security stance — enumeration-oracle + fail-direction discipline binds the backend; a finding is HARD-STOP) with frontend-engineer for the BFF-trust-boundary/SSR-safety of the page and ui-designer/accessibility-auditor for the AA floor. (Design span authored as ui-designer carrying the appsec critical rules; build should foreground appsec-engineer.)
Spawn isolation (default): isolation: "worktree" for any build/verify subagent.
Known-problem fixes:
  - Preview leaking a validity oracle → one uniform 404 body for unknown/expired/approved/denied/consumed; parametrized byte-identity test.
  - Code in a URL/log → POST body only; assert no log line or request URL carries the plaintext code.
  - Open redirect via next → same-origin relative allowlist; reject absolute URL, scheme, `//host`, backslash smuggling.
  - Silent localhost verification_uri in prod → boot-guard on environment (mirror line 988); Tailwind-v4/next-font live-render pitfalls do NOT apply (no token/font change) but verify the seal renders on a LIVE authed build, not just a green unit.
  - Touching frozen approve/deny → additive-only; re-run approve/deny wire tests to prove byte-identity (tamper-tripwire: edit a frozen test → re-cross tests→build).
Strategy actually used: Followed the §5 batch order 1→5 with ONE deviation and one reuse-uplift, both improvements:
  (1) Config first (default + `_validate_verification_uri` boot-guard mirroring line-988 + `agent_oauth_preview_rpm` added to the existing positive-knobs validator) — froze the config shape; 9 no-DB Settings tests green before anything depended on it.
  (2) Backend preview — put `NotPreviewableError` + `DeviceAuthorizationPreview` + `PreviewDeviceAuthorizationUseCase` in the in-scope `application/use_cases.py` (NOT `domain/errors.py`, which is OUT of the declared Scope — kept the uniform-outcome error application-layer). Additive `POST /oauth/device/preview` in `device_approval_router.py` reusing `_require_identity`/`_normalize_user_code`/`_parse_approval_body`/`_ApprovalBody` verbatim + the limiter keyed `preview:{user_id}`. NO `main.py` change was needed (the route rides the already-registered `agent_oauth_approval_router`; the limiter is already on `app.state`) — so `main.py`, though in Scope, was left untouched. The uniform-404 is proved by one test asserting BYTE-IDENTICAL `resp.content` across unknown/expired/approved/denied/consumed.
  (3+4) FE: to STAY IN the declared Scope, the redirect-safety helpers (`sanitizeNext`/`loginNextTarget` + `buildLoginBounceUrl`) live in the in-scope `lib/bff-client.ts` beside the /login bounce they guard; the card client (`normalizeUserCode` + preview/approve/deny BFF calls + types) lives in the in-scope `components/agent-activation/client.ts`. AuthorizationSeal (InvoiceStatusSeal idiom translated) + ActivationCard render the states; the focused root `/activate` page has its OWN server-component cookie-guard (A3) reading `cookies()` and building a validated `next` — deliberately NOT via proxy.ts (kept proxy.ts's matcher untouched, guard lives with the screen). Login page validates `?next=` through `loginNextTarget`; LoginForm gained an optional `nextPath` prop.
  UPLIFT (reported): the /login-bounce return-path preservation is uplifted ONCE into the low BFF layer — `buildLoginBounceUrl` defined in `bff-client.ts`. It fails safe: a non-meaningful path (root `/`, empty, `//host`, already-`/login`, or a partial `window.location` with undefined pathname) yields a bare `/login`, which kept the two PRE-EXISTING `bff-client.test.tsx` bounce tests GREEN WITHOUT editing them (they run at path `/`).
  SCOPE NOTE (proposed, not decided — orchestrator/verify to ratify): one touched file sits OUTSIDE the literal §5 Scope `may touch` list — `dashboard/components/auth/LoginForm.tsx`. It is UNAVOIDABLE for M1: the post-login `router.push` destination lives in LoginForm, so honoring `next` (Scope named only `login/page.tsx`) requires the component to accept the validated `nextPath`. The change is additive (an optional prop, default `/app/keys`). All other FE work was relocated to stay inside the declared Scope tokens (`lib/bff-client.ts`, `components/agent-activation/`, `app/(app)/activate/`, `app/(auth)/login/page.tsx`). Recommend adding `dashboard/components/auth/LoginForm.tsx` to §5 Scope.
  (5) Re-proved approve/deny wire bytes byte-identical (`test_approve_wire_byte_identical`/`test_deny_wire_byte_identical` assert exact `b'{"status":"approved"}'`/`b'{"status":"denied"}'`) and re-ran the full frozen agent_oauth suites (59 green).
Safety rule (feature-specific): the preview response is computed from a status/expiry check whose FAILURE direction is closed (any non-pending → uniform 404), and the boot-guard fails CLOSED (prod refuses to start on an unsafe uri) — never a permissive default in production.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] `POST /oauth/device/preview {user_code}` on a pending grant returns `200 {scope,status:"pending",expires_in>0,interval,default_budget_usd:"100.00"}` and the code is in the BODY (not the URL) — confirmed by `test_prefilled_code_previews_pending` + `test_preview_default_budget_is_system_cap_2dp` (backend, 20/20 green).
- [x] Every non-previewable state (unknown/expired/approved/denied/consumed) returns ONE byte-identical `404 {"error":"authorization_not_previewable"}` — confirmed by `test_preview_uniform_404_byte_identical` asserting `len(set(resp.content)) == 1` across all five (the enumeration-oracle proof).
- [x] `POST /oauth/device/authorize` carries a non-empty `verification_uri "http://localhost:3000/activate"` + `verification_uri_complete` by default — confirmed by `test_verification_uri_populated_by_default`.
- [x] Production refuses to boot on an empty/localhost/127.0.0.1/non-https verification_uri (`INVALID_AGENT_OAUTH_VERIFICATION_URI`); dev boots on the default — confirmed by `test_prod_refuses_unsafe_uri` (5 params) + `test_dev_boots_with_default_uri`.
- [x] Preview is per-user rate-limited (`preview:{user_id}`) independently of approve/deny — a 3rd preview under rpm=2 is `429`+Retry-After yet a following approve is still `200` — confirmed by `test_preview_per_user_rate_limit_429`.
- [x] approve/deny wire bytes are byte-identical to pre-task (`b'{"status":"approved"}'`/`b'{"status":"denied"}'`) — confirmed by the two wire-identity tests + the full frozen agent_oauth suites (59 green).
- [x] An off-origin `next` never redirects off-origin; the /activate cookie-guard round-trips through `/login?next=<encoded /activate?user_code=…>` — confirmed by `sanitizeNext`/`loginNextTarget`/`buildLoginBounceUrl` unit suites (open-redirect vectors) + the server-component guard in `app/(app)/activate/page.tsx`.
- [x] The approval card conveys state by AuthorizationSeal icon+text (not color) with sr-only final-state copy, an accessible labeled code input, and collapses a not-previewable 404 to ONE generic message — confirmed by `device-activate-page.test.tsx` (37 FE tests green).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
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

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
