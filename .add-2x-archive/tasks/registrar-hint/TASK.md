# TASK: Nameserver-inferred registrar deep-link hint endpoint (backend)

slug: registrar-hint · created: 2026-07-19 · stage: production
milestone: domain-onboarding-softening
sensitivity: architecture
autonomy: auto
component: gateway
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/domain_capture/domain/ports.py:DnsTxtResolver` — the existing FROZEN fail-**CLOSED** DNS TXT port: `async def lookup_txt(self, name: str, *, timeout: float) -> list[str]`, raises `DnsLookupFailedError` on ANY resolver error/NXDOMAIN/empty-answer/timeout, no internal retry. The shape (bounded timeout, no retry, one Protocol method) is the template to mirror for a NEW NS-record port — but the FAILURE discipline must NOT be copied: TXT verify propagates the error uncaught (fails the whole verify flow); this task's NS lookup must swallow the same class of failure and degrade gracefully (M4). Untouched by this task.
- `apps/gateway/src/gateway/domain_capture/infrastructure/dns_resolver.py:DnsPythonTxtResolver.lookup_txt` — the concrete dnspython adapter: `await dns.asyncresolver.resolve(name, "TXT", lifetime=timeout)`, catches `dns.exception.DNSException` → re-raises `DnsLookupFailedError`. The NEW `DnsPythonNsResolver` adapter is added to this SAME file, swapping `"TXT"` for `"NS"` and raising a NEW `NsLookupFailedError` — additive class, existing class untouched.
- `apps/gateway/src/gateway/core/egress_policy.py:DnsResolver`, `DenyPrivateAndMetadataEgressPolicy`, `assert_literal_host_not_denied` — the egress/SSRF guard for OUTBOUND HTTP fetches to a user-influenced URL (write-time literal-IP check + fresh-every-dial resolved-IP check). Studied, not reused directly: this task never dials a URL derived from user input — the NS lookup is a plain DNS query (same primitive class `verify_claim_use_case.py` already performs unchallenged via `DnsTxtResolver`), and the `deep_link_url` returned to the dashboard is ALWAYS one of a small curated, static allow-list of URLs (M9) — never built from the looked-up nameserver hostname string. That is what closes the SSRF/open-redirect pivot here: no second network call is ever made off the DNS answer, and no URL is ever string-built from attacker-influenced input.
- `apps/gateway/src/gateway/domain_capture/api/domain_claims_router.py` — `_get_owner_identity` (owner-role auth, duplicated per this file's own documented precedent, NOT imported from a sibling), `_rate_limit` (wraps `DomainClaimRateLimiter.check`, converts `DomainClaimRateLimitedError` → `RATE_LIMITED.exc()` with `Retry-After`), `create_domain_claim` (the per-route wiring pattern: resolve identity → rate-limit → use case → map errors → response schema). The new endpoint is added as a NEW route function in this SAME file (additive only — no existing route/function edited), reusing `_get_owner_identity` + `_rate_limit` verbatim.
- `apps/gateway/src/gateway/domain_capture/infrastructure/rate_limiter.py:DomainClaimRateLimiter.check` — fixed-window Redis INCR limiter; on `(RedisError, OSError)` it logs a warning and **returns (FAIL-OPEN)** rather than raising — confirmed by reading the body. This task's new `domain_claim_registrar_hint_rpm` knob reuses this SAME limiter class/instance (a new `action="registrar_hint"` bucket key), inheriting its fail-open-on-Redis-outage posture for free (M7).
- `apps/gateway/src/gateway/core/config.py:1400` `domain_verification_dns_timeout_seconds: float = Field(default=5.0, gt=0)` and `:1405-1416` `domain_claim_create_rpm` / `domain_claim_verify_rpm` + their shared `field_validator` (positive-knob, raises `INVALID_DOMAIN_CLAIM_KNOB` at boot on `<= 0`). Precedent also shows a knob getting its OWN dedicated validator rather than joining an existing one when added later (`scim_write_rpm` at `:1437-1445`, added after the domain-claim pair) — this task follows THAT precedent: a new dedicated validator for `domain_claim_registrar_hint_rpm`, not an edit to the existing `_validate_domain_claim_positive_knobs` function signature.
- `apps/gateway/src/gateway/domain_capture/domain/domain_validation.py:normalize_domain` — pure (zero IO), FROZEN @ v1, lowercases/trims, validates hostname-label shape (≥2 labels, `[a-z0-9-]` labels 1-63 chars, total ≤253, rejects bare IP literals), raises `DomainInvalidError` on ANY failure. Reused verbatim, unmodified, as the FIRST step of the new use case (M2) — zero DNS IO before this passes.
- `apps/gateway/src/gateway/domain_capture/domain/errors.py:DomainInvalidError`, `DnsLookupFailedError` (existing, both untouched) — a NEW `NsLookupFailedError(DomainCaptureError)` is added additively, deliberately a DISTINCT class from `DnsLookupFailedError` so its different (fail-OPEN) handling contract can never be confused with the TXT resolver's fail-CLOSED one at a call site.
- `apps/gateway/src/gateway/domain_capture/api/schemas.py` — Pydantic response-schema convention: FROZEN @ v1 for `DomainClaimCreateResponse` / `DomainClaimListItem` / `DomainClaimListResponse` / `DomainClaimVerifyResponse`, each endpoint gets its OWN shape (not one shared schema), plus a private `_dns_record_name`/`_dns_record_value` + `to_*_response` mapper-function convention. A NEW `RegistrarHintResponse` + `to_registrar_hint_response` is added the same way; nothing existing edited.
- `apps/gateway/src/gateway/domain_capture/api/deps.py:get_dns_resolver`, `get_domain_claim_rate_limiter`, `get_verify_claim_use_case` — the `app.state.<name>` test-injection-seam-first, else-construct-real-adapter convention (mirrors `auth/api/saml_deps.py` per this file's own docstring). New `get_dns_ns_resolver` + `get_registrar_hint_use_case` are added following the exact same shape.
- `apps/gateway/src/gateway/core/error_catalog.py:730-762` — the domain-capture `ErrorSpec` block (`DOMAIN_INVALID` 400, `DOMAIN_ALREADY_VERIFIED` 409, `DNS_LOOKUP_FAILED` 503, …) plus `RATE_LIMITED` (429, shared, line 467), `AUTH_TOKEN_INVALID` / `AUTH_FORBIDDEN_OWNER_REQUIRED` (shared, tenants block). **No new ErrorSpec is needed for this task** — every rejection this endpoint can produce reuses an EXISTING spec (see §1 Reject); this is a deliberate finding, not an oversight (a lookup failure is never a rejection here — it is the fail-open Must, M4, a 200).
- `apps/gateway/tests/domain_capture/test_domain_capture.py` — existing red/green suite for create/list/verify/revoke; confirms the pytest base (`gateway_test`, shared :5433) and the fixture/fake-injection style (`app.state.dns_resolver = <fake>`) the new `test_registrar_hint.py` will mirror (`app.state.dns_ns_resolver = <fake>`).

Context (working folder): `domain-onboarding progressive trust` (memory) — Tin-approved 3-rung trust ladder (unclaimed → member-verified SOFT → owner-verified DNS); this task is a UX softener for the owner-verified DNS rung's challenge card ONLY — it changes zero trust-model logic (`resolve_verified_tenant` / auto-join gating are untouched, out of scope). Consumed by the sibling dashboard task `dns-verify-softeners` (not yet scoped into ADD) — the response shape frozen here becomes that task's fixed input contract.

Honors (patterns / conventions): hexagonal layering (domain Protocol → infrastructure adapter → application use case → api router/schema/deps) per the backend-architect persona's shipped convention; "design for failure" (CLAUDE.md non-negotiable: timeout + fail-open/fail-closed decided explicitly, never a hang); additive-only editing of any file whose header claims FROZEN @ v1 (add new symbols, never edit an existing frozen one).

Seams consulted: none in `.add/SEAMS.md` apply directly (no entry found for DNS/registrar inference) — grounded directly against the code cited above instead.

Anchors the contract cites: `DnsTxtResolver` (ports.py, mirrored not reused), `DnsPythonTxtResolver` (dns_resolver.py, mirrored not reused), `_get_owner_identity` / `_rate_limit` (domain_claims_router.py, reused verbatim), `DomainClaimRateLimiter` (rate_limiter.py, reused verbatim with a new action key), `normalize_domain` (domain_validation.py, reused verbatim), `DOMAIN_INVALID` / `AUTH_TOKEN_INVALID` / `AUTH_FORBIDDEN_OWNER_REQUIRED` / `RATE_LIMITED` (error_catalog.py, reused verbatim, zero new ErrorSpec).

Issues/Risks (→ feed §1):
- A DNS NS lookup, like the existing TXT lookup, has no per-request cap on how many distinct domains a single caller can probe in a window beyond the rate limiter — already mitigated by reusing `DomainClaimRateLimiter` (M7), not a new risk this task introduces.
- The curated NS-suffix→registrar map is a hand-maintained static table; a registrar's NS suffix can change over time (unlikely, but stale entries degrade to the SAME graceful `fallback: true` shape, not an error) — an acceptable, self-healing-to-fallback risk, not a HARD-STOP.
- Some registrars' actual DNS-management UI needs an account/zone-specific ID, not just the bare domain, to deep-link precisely — the curated map can only promise a top-level provider entry point for those, not a guaranteed exact record-edit page (flagged as the top assumption, §1).
- `dns.asyncresolver.resolve(name, "NS", lifetime=timeout)` is the SAME dnspython call shape already proven in production by `DnsPythonTxtResolver` (just a different rdtype) — no new dependency, no new IO primitive.

Related intent: PROJECT.md "design for failure" IO invariant; GLOSSARY delta below (`Registrar hint`); originating request — Tin's 2026-07-19 decision to split "smart nameserver-inferred deep-link" into its OWN task (not folded into the dashboard task) so the NS-inference + endpoint has its own frozen contract independent of the dashboard's display concerns.

Ground SHA: `9ec92b4`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Registrar deep-link hint — infer a domain's DNS registrar/provider from its NS records and return an advisory deep-link (or a graceful fallback signal) for the domain-claims owner console.

Framings weighed: **smart NS-inferred deep-link with static-map lookup + graceful fallback** (chosen — Tin's explicit 2026-07-19 decision) · a purely static provider-list with no inference (simpler, but always shows a generic list even when we could point the user exactly at Cloudflare/GoDaddy/etc. — rejected, worse UX for the common case) · inferring the registrar via a WHOIS lookup instead of NS records (rejected — WHOIS has no consistent machine-parseable format across TLDs/registrars and is a heavier, less bounded IO than one NS query; NS-suffix matching is what the industry convention already uses for this exact “open your DNS provider” pattern).

Must:
<must>
  - M1: `GET /admin/domain-claims/registrar-hint?domain=<domain>` returns 200 with `{ domain, registrar, deep_link_url, fallback }` for an authenticated OWNER and a syntactically valid `domain`.
  - M2: The `domain` query param is normalized/validated via the EXISTING `normalize_domain` (same rules as `create_domain_claim`) BEFORE any network IO; a malformed/single-label/IP-literal domain never reaches the DNS resolver.
  - M3: The endpoint performs exactly ONE bounded-timeout NS-record DNS lookup via a NEW `DnsNsResolver` port + `DnsPythonNsResolver` adapter, timeout = a NEW `registrar_hint_dns_timeout_seconds` config knob (deliberately its OWN, SHORTER-lived knob than `domain_verification_dns_timeout_seconds` — this is a best-effort UI convenience call, not a verification-blocking one), no internal retry.
  - M4 (REQUIRED — fail-open): On ANY NS-lookup failure (timeout, NXDOMAIN, resolver error, empty answer) the endpoint degrades gracefully — 200 with `registrar: null, deep_link_url: null, fallback: true` — NEVER a 5xx, NEVER lets the failure propagate past the use case. This is the OPPOSITE discipline from `DnsTxtResolver`'s fail-closed contract, and is why a NEW error class (`NsLookupFailedError`) is used rather than reusing `DnsLookupFailedError`.
  - M5: When the NS lookup succeeds and at least one returned nameserver hostname's suffix matches an entry in the curated static registrar map, respond 200 with that entry's `registrar` name + curated `deep_link_url`, `fallback: false`.
  - M6: When the NS lookup succeeds but NO returned nameserver matches any known suffix, respond 200 with the SAME graceful shape as M4 (`registrar: null, deep_link_url: null, fallback: true`) — a miss is never an error, identical observable shape to a lookup failure (the dashboard need not distinguish why).
  - M7: The endpoint is rate-limited per `tenant_id` via the EXISTING `DomainClaimRateLimiter`, a NEW bounded `domain_claim_registrar_hint_rpm` knob (positive-knob-validated at boot, its own dedicated validator), applied BEFORE any DNS IO. Inherits the limiter's existing fail-OPEN-on-Redis/backend-outage posture (never blocks the caller on limiter infra failure) — reused unchanged, not reimplemented.
  - M8: Only an OWNER-role caller may call this endpoint — reuses `_get_owner_identity` verbatim (same auth posture as sibling create/list/verify/revoke routes).
  - M9: `deep_link_url` is ALWAYS a byte-identical value from the curated static registrar map — NEVER constructed dynamically from the resolved nameserver hostname string or any other request-influenced input (closes any SSRF/open-redirect pivot through the DNS answer).
  - M10: The new NS-lookup Protocol (`DnsNsResolver`), adapter (`DnsPythonNsResolver`), and error (`NsLookupFailedError`) are added ADDITIVELY; the existing `DnsTxtResolver` / `DnsPythonTxtResolver` / `DnsLookupFailedError` used by domain-claim TXT verification are untouched and every existing domain-capture test keeps passing unmodified.
  - M11: When the NS lookup returns multiple nameservers and more than one matches a suffix in the registrar map, the FIRST match in resolver-returned order wins (deterministic, no re-sorting) — no ambiguity/race in which registrar is reported.
  - M12: The endpoint performs ZERO database IO and ZERO writes of any kind — purely a read + one bounded DNS call + a pure in-memory map lookup; no `domain_claims` row is created, read, or mutated by this endpoint.
</must>
Reject:
<reject>
  - R1: malformed / single-label / IP-literal `domain` query param -> "ERR_DOMAIN_INVALID" (400, reused existing `DOMAIN_INVALID` ErrorSpec, zero DNS IO performed)
  - R2: caller has no/invalid bearer token, or a valid token with a non-OWNER role -> "ERR_AUTH_TOKEN_INVALID" (401) / "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" (403) (reused existing ErrorSpecs, identical to sibling routes)
  - R3: caller's tenant has exceeded `domain_claim_registrar_hint_rpm` requests in the current fixed window -> "ERR_RATE_LIMITED" (429, `Retry-After` header, reused existing ErrorSpec)
</reject>
After:
<after>
  - A 200 response was returned for every syntactically-valid-domain request from an authenticated owner — this endpoint NEVER returns a 5xx regardless of DNS-lookup outcome (lookup failure and "no match" both resolve to the same graceful `fallback: true` shape, not an error).
  - No new state was written anywhere — no DB row created/mutated/read, no domain claim touched; this is a read-only, advisory convenience endpoint.
  - The dashboard `dns-verify-softeners` task can render either a specific registrar deep-link or its generic static-provider-list fallback purely from `{ registrar, deep_link_url, fallback }`, without needing to know WHY fallback happened (timeout vs. NXDOMAIN vs. no-match are all observably identical to the caller).
  - The existing DNS-TXT verification flow (`DnsTxtResolver`/`verify_claim_use_case.py`) is byte-identically unaffected — a fully separate Protocol/adapter/error/use-case chain was added, nothing shared-and-mutated.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The curated NS-suffix→registrar map's `deep_link_url` values are best-effort placeholders at this DRAFT stage, not yet verified against each provider's CURRENT dashboard URL scheme — lowest confidence because some registrars (notably Cloudflare) need an account-specific zone ID, not just the bare domain, to deep-link straight to the DNS-record editor; a bare top-level login/dashboard URL may be the realistic ceiling for those entries. If wrong: the dashboard opens a generic provider login/landing page instead of the exact record-edit screen for a subset of registrars — a UX degradation, not a functional break (the `fallback: true` path already covers the case where we have zero confidence, and a "close enough" deep-link is still strictly better than the always-generic list it replaces). Confirm/curate the real URLs (and, where a provider needs a zone ID we cannot supply, deliberately EXCLUDE that provider from the map rather than ship a link that 404s) before Contract freeze.
  - [x] Auth scope: OWNER-only (M8) — CONFIRMED (orchestrator, 2026-07-19). The hint is shown on the
    domain-claims challenge card, which is already OWNER-gated (creating a claim is OWNER-only); the sibling
    `dns-verify-softeners` design shows it there — no member-visible variant needed.
  - [x] `registrar_hint_dns_timeout_seconds` = 2.0s — CONFIRMED. A best-effort UI-convenience lookup must not
    stall a render; a slightly-slow resolver simply falls open to the static list (strictly no worse than today).
  - [x] `domain_claim_registrar_hint_rpm` = 30 — CONFIRMED. The dashboard calls this once per challenge-card
    render, NOT on the auto-poll loop (auto-poll hits VERIFY, not this hint); 30/min is ample headroom.
  - [x] Whether a NEW ErrorSpec is needed — confirmed NOT needed: every rejection (R1-R3) reuses an existing ErrorSpec; a lookup failure/miss is a Must (M4/M6), never a Reject.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: NS lookup matches a known registrar   # M1, M5
  Given an authenticated OWNER whose tenant is under its rate limit
  And domain "example.com" resolves NS records including "ns1.cloudflare.com"
  When GET /admin/domain-claims/registrar-hint?domain=example.com is called
  Then respond 200 with { domain: "example.com", registrar: "Cloudflare", deep_link_url: <curated Cloudflare URL>, fallback: false }
  And no domain_claims row is created, read, or mutated

Scenario: Domain validated before any DNS IO   # M2, R1
  Given an authenticated OWNER
  When GET /admin/domain-claims/registrar-hint?domain=not_a_domain is called
  Then respond 400 { error: "ERR_DOMAIN_INVALID" }
  And the DnsNsResolver is never invoked (zero DNS IO for this request)

Scenario: NS lookup is bounded by a timeout   # M3
  Given an authenticated OWNER and a domain whose NS lookup would otherwise hang past registrar_hint_dns_timeout_seconds
  When GET /admin/domain-claims/registrar-hint?domain=slow-ns.example is called
  Then the lookup is aborted at exactly registrar_hint_dns_timeout_seconds
  And the request still completes (never hangs), falling through to the fail-open shape below

Scenario: Fail-open on NS lookup failure   # M4 (REQUIRED)
  Given an authenticated OWNER and a domain whose NS lookup raises a timeout, NXDOMAIN, or any resolver error
  When GET /admin/domain-claims/registrar-hint?domain=broken.example is called
  Then respond 200 with { registrar: null, deep_link_url: null, fallback: true }
  And the response is never a 5xx and no exception propagates past the use case

Scenario: NS lookup succeeds with no known-registrar match   # M6
  Given an authenticated OWNER and a domain whose NS records resolve successfully but match NO entry in the curated registrar map
  When GET /admin/domain-claims/registrar-hint?domain=obscure-host.example is called
  Then respond 200 with { registrar: null, deep_link_url: null, fallback: true }
  And this response is byte-identical in shape to the lookup-failure case above

Scenario: Rate limit enforced   # M7, R3
  Given an authenticated OWNER whose tenant has already made domain_claim_registrar_hint_rpm requests in the current fixed window
  When one more GET /admin/domain-claims/registrar-hint request is made
  Then respond 429 { error: "ERR_RATE_LIMITED" } with a Retry-After header
  And the DnsNsResolver is never invoked for the rejected call
  And no domain_claims row or rate-limit state is corrupted — the limiter's own atomic window key is untouched by this task

Scenario: Rate limiter fails open on backend outage   # M7 elaboration
  Given the rate limiter's Redis backend is unreachable (RedisError/OSError)
  When an authenticated OWNER calls GET /admin/domain-claims/registrar-hint
  Then the request is NOT blocked by the limiter outage (fails open, matching DomainClaimRateLimiter.check's existing behavior)
  And the DNS lookup still runs and the response follows the normal match/miss/fail-open rules above

Scenario: Owner-only authorization enforced   # M8, R2
  Given a caller with a valid bearer token but a non-OWNER role
  When GET /admin/domain-claims/registrar-hint is called
  Then respond 403 { error: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }
  And no DNS lookup occurs
  And a caller with a missing/invalid bearer token instead gets 401 { error: "ERR_AUTH_TOKEN_INVALID" }

Scenario: deep_link_url is never derived from the DNS answer   # M9
  Given a domain whose NS lookup matches a registrar map entry
  When the response is constructed
  Then deep_link_url is byte-identical to the curated static map's entry for that registrar
  And no substring of any resolved nameserver hostname appears anywhere in deep_link_url

Scenario: New NS-lookup chain is additive, TXT-verify chain is unaffected   # M10
  Given the existing DnsTxtResolver / DnsPythonTxtResolver / DnsLookupFailedError used by domain-claim verification
  When the registrar-hint feature is built (new DnsNsResolver / DnsPythonNsResolver / NsLookupFailedError)
  Then every existing domain_capture test (create/list/verify/revoke, including the TXT fail-closed path) still passes unmodified
  And no existing symbol in ports.py, dns_resolver.py, or errors.py was edited — only new symbols were added

Scenario: Deterministic first-match on multiple NS records   # M11 (edge case)
  Given a domain whose NS lookup returns nameservers ["ns1.unknown-host.net", "ns2.cloudflare.com"] in that order, where only the second matches the curated map
  When GET /admin/domain-claims/registrar-hint?domain=mixed.example is called
  Then respond with registrar "Cloudflare" (the first — and only — match, in resolver-returned order), fallback: false

Scenario: Zero database IO   # M12 (edge case)
  Given an authenticated OWNER makes any registrar-hint request (match, miss, or failure)
  When the request completes
  Then no query is issued against domain_claims (or any other table) by this endpoint's code path
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/domain-claims/registrar-hint?domain={domain}   auth: Bearer token, OWNER role only
  200 -> { domain: string, registrar: string | null, deep_link_url: string | null, fallback: boolean }
  400 -> { error: "ERR_DOMAIN_INVALID" }        (R1 — malformed/single-label/IP-literal domain)
  401 -> { error: "ERR_AUTH_TOKEN_INVALID" }    (R2 — missing/invalid bearer token)
  403 -> { error: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }  (R2 — valid token, non-OWNER role)
  429 -> { error: "ERR_RATE_LIMITED" }          (R3 — Retry-After header set)

Schema: no DB tables touched (zero DB IO, M12). New in-process symbols only:
  - domain/ports.py            : DnsNsResolver(Protocol)      — async def lookup_ns(self, name: str, *, timeout: float) -> list[str]
  - domain/errors.py           : NsLookupFailedError(DomainCaptureError)   [NEW, distinct from DnsLookupFailedError]
  - domain/registrar_map.py    : RegistrarHint (dataclass: name, deep_link_url) + REGISTRAR_SUFFIX_MAP (ordered NS-suffix -> RegistrarHint) + infer_registrar(nameservers: list[str]) -> RegistrarHint | None   [NEW FILE, pure/zero-IO, mirrors domain_validation.py's discipline]
  - infrastructure/dns_resolver.py : DnsPythonNsResolver — dns.asyncresolver.resolve(name, "NS", lifetime=timeout), catches dns.exception.DNSException -> raises NsLookupFailedError   [added alongside existing DnsPythonTxtResolver, unmodified]
  - application/registrar_hint_use_case.py : GetRegistrarHintUseCase.execute(domain_raw: str) -> RegistrarHintResult (dataclass: domain, registrar, deep_link_url, fallback)   [NEW FILE — normalize_domain (may raise DomainInvalidError) -> DnsNsResolver.lookup_ns with bounded timeout, catching NsLookupFailedError/any dns exception into fallback -> infer_registrar]
  - api/schemas.py             : RegistrarHintResponse(BaseModel) + to_registrar_hint_response(...)   [added, existing schemas untouched]
  - api/deps.py                : get_dns_ns_resolver(request) + get_registrar_hint_use_case(request)   [added, mirrors get_dns_resolver/get_verify_claim_use_case shape]
  - api/domain_claims_router.py: get_registrar_hint(...)  [NEW route function only; reuses _get_owner_identity + _rate_limit verbatim, no existing route/function edited]
  - core/config.py             : registrar_hint_dns_timeout_seconds: float = Field(default=2.0, gt=0)  +  domain_claim_registrar_hint_rpm: int = 30  (own dedicated positive-knob field_validator, mirrors the scim_write_rpm precedent — not folded into the existing domain_claim validator)

No new ErrorSpec — R1/R2/R3 all reuse DOMAIN_INVALID / AUTH_TOKEN_INVALID / AUTH_FORBIDDEN_OWNER_REQUIRED / RATE_LIMITED verbatim.
```

Glossary deltas: `Registrar hint: an advisory, best-effort mapping from a domain's DNS nameservers to a known registrar/DNS-provider deep link, shown on the owner-verified-DNS challenge card to save a manual "which provider do I use" step — never authoritative, never blocking, always degrades to a generic fallback signal rather than an error.`

Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-20 (consolidated backend-first freeze). Build note: curate the registrar→URL map's deep_link_url values against each provider's real DNS-panel scheme; EXCLUDE any provider needing a zone-id we can't supply (they fall to fallback). Shape is stable.
Reported: yes — consolidated freeze report rendered 2026-07-20 (fail-open + SSRF-closure flags surfaced).
Least-sure flag surfaced at freeze: [contract] the curated `deep_link_url` values are best-effort provider DNS-management LANDING-page literals, NOT per-zone record editors — a provider whose editor needs an account/zone-id this endpoint can't supply is deliberately excluded and degrades to `fallback:true`. Cost if a link is stale: a UX degradation (generic dashboard instead of the exact record page), never a functional break; the response SHAPE is stable regardless of curation.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches this component's existing domain_capture suite convention)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_registrar_hint_matches_known_registrar: arrange fake DnsNsResolver returning cloudflare-suffixed NS / act GET ...?domain=example.com / assert 200 registrar="Cloudflare", deep_link_url=<curated>, fallback=false + assert no domain_claims row touched · covers: M1, M5
  - test_registrar_hint_rejects_invalid_domain_before_dns_io: arrange a spy/fake DnsNsResolver that records calls / act GET ...?domain=not_a_domain / assert 400 ERR_DOMAIN_INVALID + assert resolver was never called · covers: M2, R1
  - test_registrar_hint_lookup_bounded_by_timeout: arrange a fake resolver that sleeps past registrar_hint_dns_timeout_seconds / act GET .../registrar-hint / assert the call completes within a bounded wall-clock bound and falls through to the fail-open shape · covers: M3
  - test_registrar_hint_fails_open_on_lookup_failure: arrange fake DnsNsResolver raising NsLookupFailedError (timeout/NXDOMAIN/resolver-error variants) / act GET .../registrar-hint / assert 200 registrar=null, deep_link_url=null, fallback=true (never 5xx) · covers: M4 (REQUIRED)
  - test_registrar_hint_fails_open_on_no_match: arrange fake DnsNsResolver returning nameservers matching no map entry / act GET .../registrar-hint / assert 200 registrar=null, deep_link_url=null, fallback=true, byte-identical shape to the failure case · covers: M6
  - test_registrar_hint_rate_limited: arrange tenant already at domain_claim_registrar_hint_rpm in-window / act one more GET / assert 429 ERR_RATE_LIMITED + Retry-After header + resolver never called · covers: M7, R3
  - test_registrar_hint_rate_limiter_fails_open_on_redis_outage: arrange a fake redis client raising RedisError/OSError on incr / act GET .../registrar-hint / assert request proceeds (not blocked) and normal match/fallback rules still apply · covers: M7
  - test_registrar_hint_requires_owner_role: arrange a non-owner-role bearer token / act GET .../registrar-hint / assert 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED; arrange a missing/invalid token / assert 401 ERR_AUTH_TOKEN_INVALID; assert resolver never called in either case · covers: M8, R2
  - test_registrar_hint_deep_link_never_derived_from_ns_answer: arrange a matched registrar / act GET .../registrar-hint / assert deep_link_url equals the curated map's literal value and contains no substring of any resolved nameserver hostname · covers: M9
  - test_registrar_hint_additive_existing_verify_suite_untouched: run the FULL existing test_domain_capture.py suite unmodified after the build lands / assert 100% still green, DnsTxtResolver/DnsPythonTxtResolver/DnsLookupFailedError symbols byte-unchanged · covers: M10
  - test_registrar_hint_first_match_wins_on_multiple_ns: arrange fake DnsNsResolver returning ["ns1.unknown-host.net", "ns2.cloudflare.com"] / act GET .../registrar-hint / assert registrar="Cloudflare" (the first match in returned order), fallback=false · covers: M11
  - test_registrar_hint_zero_db_io: arrange a DB-session spy/assertion that no statement is executed / act GET .../registrar-hint (match, miss, and failure variants) / assert zero queries issued · covers: M12
</test_plan>

Tests live in: `apps/gateway/tests/domain_capture` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/domain_capture/domain/ports.py`
`apps/gateway/src/gateway/domain_capture/domain/errors.py`
`apps/gateway/src/gateway/domain_capture/domain/registrar_map.py`
`apps/gateway/src/gateway/domain_capture/infrastructure/dns_resolver.py`
`apps/gateway/src/gateway/domain_capture/application/registrar_hint_use_case.py`
`apps/gateway/src/gateway/domain_capture/api/schemas.py`
`apps/gateway/src/gateway/domain_capture/api/deps.py`
`apps/gateway/src/gateway/domain_capture/api/domain_claims_router.py`
`apps/gateway/src/gateway/core/config.py`
`apps/gateway/tests/domain_capture`

Strategy (ordered batches):
1. Domain layer (pure, zero IO, unit-testable in isolation): add `NsLookupFailedError` (errors.py) + `DnsNsResolver` Protocol (ports.py) + the new `registrar_map.py` (curated `REGISTRAR_SUFFIX_MAP` + `infer_registrar`) — no framework/IO imports, mirrors `domain_validation.py`'s own discipline.
2. Config: add `registrar_hint_dns_timeout_seconds` + `domain_claim_registrar_hint_rpm` knobs + a dedicated positive-knob validator (core/config.py) — boot-time fail-fast on a non-positive knob, mirrors the `scim_write_rpm` precedent.
3. Infrastructure: add `DnsPythonNsResolver` (dns_resolver.py) — same dnspython wrapping as `DnsPythonTxtResolver`, `"NS"` rdtype, catches `dns.exception.DNSException` -> `NsLookupFailedError`.
4. Application: add `GetRegistrarHintUseCase` (registrar_hint_use_case.py) — `normalize_domain` first (may raise `DomainInvalidError`, zero DNS IO on that path) -> `DnsNsResolver.lookup_ns` with the bounded timeout -> catch `NsLookupFailedError` (and any stray DNS exception) into the fallback result -> else `infer_registrar`. This use case is the ONLY place the fail-open decision is made — the router and the resolver stay dumb.
5. API: add `RegistrarHintResponse` + `to_registrar_hint_response` (schemas.py); add `get_dns_ns_resolver` + `get_registrar_hint_use_case` (deps.py); add the new `GET /admin/domain-claims/registrar-hint` route (domain_claims_router.py) reusing `_get_owner_identity` + `_rate_limit` verbatim — new function only, zero edits to existing route functions.
6. Tests: `test_registrar_hint.py` written FIRST (red/green TDD, one test per §4 bullet) against the `apps/gateway/tests/domain_capture` fixtures, mirroring the existing `app.state.<seam> = <fake>` injection style (`app.state.dns_ns_resolver`).

Persona (required): generic — no persona under `.add/personas/` currently declares `flow: design` (all shipped personas are `flow: build, advisor`), so this span used a generic domain-analyst/interface-architect stance per the routing rule. Advisory input drawn from two EXISTING build/advisor personas without adopting either wholesale: `backend-architect.md` (ports-and-adapters layering, Protocol-port discipline — informed the domain/infrastructure/application/api split above) and `sre-reliability-engineer.md` (design-for-failure: timeout + explicit fail-open/closed decision, never a hang — informed M3/M4/M7). Whoever builds this may want `add-persona` to seed a dedicated "DNS/network-IO resilience" persona if this pattern recurs.

Spawn isolation (default): worktree — prefer `isolation: "worktree"` for the TESTS/BUILD spawn per the standing default; no stated reason to share the tree.

Known-problem fixes:
- Trap: reusing `DnsLookupFailedError`/`DnsTxtResolver` for NS lookups would silently inherit the WRONG (fail-closed) failure discipline at some future call site → fix: separate `NsLookupFailedError`/`DnsNsResolver` classes, never shared.
- Trap: building `deep_link_url` from the resolved NS hostname string (e.g. string-concatenating a scheme onto it) would reopen an SSRF/open-redirect residue → fix: `deep_link_url` only ever comes from the static `REGISTRAR_SUFFIX_MAP` literal, verified by M9's dedicated test.
- Trap: a rate-limiter Redis outage 5xx-ing the request → fix: reuse `DomainClaimRateLimiter.check` UNCHANGED (it already fails open); do not reimplement rate limiting for this route.
- Trap: editing `_get_owner_identity`, `_rate_limit`, or any existing route function while adding the new one → fix: new function only, diff-reviewed to confirm zero lines changed in existing functions.
- Trap (team lesson, add-scope-snapshot-poisoning): stray `.coverage`/`.pytest_cache` artifacts poisoning the scope-gate walk → fix: clean them as the last pre-gate step.
- Trap (team lesson): a stray `GATEWAY_TEST_DATABASE_URL` override splitting the shared :5433 `gateway_test` DB across processes → fix: unset it before running the suite; one pytest process at a time on :5433.

Strategy actually used: as planned (add-build subagent, sonnet) — domain→config→infra→application→api→tests batches; 8-provider curated map linking to DNS-management landing pages (per-zone editors excluded/degraded per the §3 build note); reused DomainClaimRateLimiter (fail-open) + normalize_domain + _get_owner_identity verbatim. 12 tests red→green; existing domain_capture suite untouched (37/37 total).

Safety rule (feature-specific): this endpoint performs ZERO database writes and ZERO additional network calls beyond the ONE bounded-timeout NS lookup — never let a future change add a second outbound call (e.g. dialing a discovered nameserver directly) without re-opening this contract at SPECIFY.

Code lives in: `apps/gateway/src/gateway/domain_capture/`

Constraints: do NOT change any test or the contract; allow-list packages only (no new third-party dependency — `dnspython` is already a project dependency via `DnsPythonTxtResolver`); ask if unclear.

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
- [x] Green bar met: `pytest (Makefile:test / ci.yml 'Tests' step)` — 37/37 in `tests/domain_capture/` GREEN (12 new registrar-hint + 25 existing untouched), pyright 0 errors on touched files, run 2026-07-20 (`env -u GATEWAY_TEST_DATABASE_URL uv run pytest tests/domain_capture/`).
- [x] A matched domain returns the curated literal deep-link + fallback:false — confirmed: test_matches_known_registrar green; registrar_map.py has 8 curated providers (Cloudflare/GoDaddy/Namecheap/Route53/DigitalOcean/Azure/Google/Namesilo).
- [x] Any NS failure OR no-match returns 200 { registrar:null, deep_link_url:null, fallback:true } — never 5xx — confirmed: fails_open_on_lookup_failure + fails_open_on_no_match both assert byte-identical 200 shape.
- [x] deep_link_url is NEVER derived from the DNS answer — confirmed: M9 test asserts == curated literal AND nameserver string not in URL; registrar_map docstring + code make it a static-table-only literal.
- [x] owner-gated, rate-limited, zero DB IO — confirmed: owner test (403/401, resolver never called on reject), rate-limit test (429+Retry-After, resolver not called), zero-db-io test.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new symbols all referenced: DnsNsResolver/NsLookupFailedError (ports/errors) used by DnsPythonNsResolver + use-case; registrar_map.infer_registrar called by use-case; route wired in domain_claims_router + deps; knobs in config. Confirmed via green suite exercising the full path.
- [x] DEAD-CODE (code) — no orphaned symbol; every new symbol is on the request path or a test seam.
- [x] SEMANTIC — read registrar_map.py in full: 8 providers link to DNS-management LANDING pages (documented rationale: per-zone editors need an account/zone ID → degrade, not 404); honest curation matching the §3 build note.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 cites resolves in the current tree — confirmed: pyright clean on touched files; the reused _get_owner_identity/_rate_limit/normalize_domain/DomainClaimRateLimiter all still present (green suite exercises them).
- [x] no anchor moved since Ground SHA 9ec92b4 (same session, same tree).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (orchestrator) · adversarially checked: probed the two security-relevant tests for vacuity — M9 SSRF-closure asserts deep_link_url == the byte-identical curated literal AND the nameserver hostname is NOT a substring of it (not a tautology); fail-open asserts an exact 200 JSON shape on lookup failure; rate-limit/owner tests assert the resolver is never even invoked on rejection. Confirmed the map's deep_link_url values are static literals, never string-built from the DNS answer. Green is earned, not overfit.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self
1. Security: CLEAR — SSRF/open-redirect pivot closed (deep_link_url is a static-map literal, never derived from DNS; M9-tested). Owner-gated + rate-limited. Zero DB IO. No secrets. NsLookupFailedError kept DISTINCT from the fail-closed TXT error so no call site inherits the wrong discipline.
2. Concurrency: CLEAR — a stateless read-only GET; one bounded-timeout NS lookup, no shared mutable state, no write.
3. Architecture: CLEAR — clean hexagonal split (Protocol port → dnspython adapter → pure use-case → schema/deps/route); additive only, existing verify chain byte-unchanged (37/37 domain_capture green incl. the untouched TXT suite).
Verdict: PASS
Residue: minor doc-vs-reality reconciliation — §3 DRAFT guessed error-code names ERR_AUTH_FORBIDDEN_OWNER_REQUIRED / ERR_AUTH_TOKEN_INVALID; the build correctly reused the REAL existing specs ERR_AUTH_FORBIDDEN / ERR_AUTH_INVALID_TOKEN. Intent (owner-403, invalid-token-401, reuse existing) honored; no weakening.
Binding: advisory — architecture sensitivity (auto-gate on evidence).

### GATE RECORD
Reported: yes — verify evidence (37/37 green, pyright clean, earned-green refute) recorded here before the outcome
Outcome: PASS
component: gateway · expected green-bar: pytest (Makefile:test / ci.yml 'Tests' step) · verify: cd apps/gateway && uv run pytest
Reviewed by: Tin Dang (auto-gate on evidence; architecture sensitivity) · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **smart NS-inferred deep-link with static-map lookup + graceful fallback**; rejected a purely static provider-list with no inference (simpler, but always shows a generic list even when we could point the user exactly at Cloudflare/GoDaddy/etc. — rejected, worse UX for the common case) · inferring the registrar via a WHOIS lookup instead of NS records (rejected — WHOIS has no consistent machine-parseable format across TLDs/registrars and is a heavier, less bounded IO than one NS query; NS-suffix matching is what the industry convention already uses for this exact “open your DNS provider” pattern).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-20 (consolidated backend-first freeze). Build note: curate the registrar→URL map's deep_link_url values against each provider's real DNS-panel scheme; EXCLUDE any provider needing a zone-id we can't supply (they fall to fallback). Shape is stable.)
- [AI] build — strategy used: as planned (add-build subagent, sonnet) — domain→config→infra→application→api→tests batches; 8-provider curated map linking to DNS-management landing pages (per-zone editors excluded/degraded per the §3 build note); reused DomainClaimRateLimiter (fail-open) + normalize_domain + _get_owner_identity verbatim. 12 tests red→green; existing domain_capture suite untouched (37/37 total).
- [AI] verify — gate PASS (reviewed by Tin Dang (auto-gate on evidence; architecture sensitivity))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

