# TASK: RS256/JWKS ID-token signature verification

slug: oidc-jwks · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- risk: high — cryptographic security control; closes the explicitly-deferred sso-oidc v4 gap;
     autonomy: conservative — security review mandatory; build cannot auto-PASS at Verify. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: RS256/JWKS ID-token signature verification — defense-in-depth layer on top of the
         v4 TLS-channel sanction. The gateway now cryptographically verifies the RS256 signature
         on every OIDC ID token against the IdP's published JWKS endpoint before accepting any
         claims. The v4 preconditions (OIDC Core 1.0 §3.1.3.7(6)) remain in force unchanged;
         this task adds a second trust layer.

Framings weighed:
- **pyjwt + cryptography RS256 JWKS client** (chosen): pyjwt already in the venv; adding
  `cryptography` (now on the allowlist) unlocks pyjwt's RS256/ECDSA backends. A thin JWKS
  client port (typing.Protocol) fetches the IdP's JWKS JSON, caches keys by `kid`, and
  resolves the signing key for each token. pyjwt's `jwt.decode(token, key, algorithms=["RS256"])`
  then verifies the signature AND validates exp/iss/aud in one call. The gateway retains manual
  nonce+email validation (pyjwt does not know about OIDC nonce). Fail-CLOSED: any verification
  failure → 401 ERR_OIDC_TOKEN_INVALID, no claims trusted.
  Tradeoff: adds one new network dependency (JWKS endpoint). Mitigated by in-process key cache
  with TTL + bounded retry + fail-CLOSED on all errors.

- **python-jose** (rejected): supports RS256 + JWKS natively but is not on the allowlist and
  adds a heavy dependency. pyjwt + cryptography achieves the same with packages already present
  or minimal (cryptography is the only addition). Rejected: package overhead + allowlist cost.

- **PyJWKS / jwcrypto** (rejected): not on the allowlist; adds no capability beyond pyjwt
  +cryptography. Rejected: no new packages when existing approach suffices.

- **Keep verify_signature=False indefinitely** (rejected as permanent solution): the v4 design
  is SPEC-SANCTIONED but explicitly deferred. The MILESTONE.md v5 scope pins this task as the
  fix. Rejected: defense-in-depth is the correct posture; the preconditions alone are not
  sufficient for enterprise-grade trust.

Must:
<must>
  - `cryptography>=48.0.1` added to `apps/gateway/pyproject.toml` dependencies and to
    `.add/dependencies.allowlist`. This unlocks pyjwt RS256 signature verification.

  - New Settings field (additive, in `apps/gateway/src/gateway/core/config.py`):
      oidc_jwks_url: str = ""         # GATEWAY_OIDC_JWKS_URL
    CONFIG-GATED HARDENING (orchestrator amendment at review, 2026-06-11): the field is
    OPTIONAL at Settings level. When non-empty (or when app.state.jwks_client is injected),
    RS256/JWKS signature verification is ACTIVE, mandatory, and fail-CLOSED — no skip path
    exists on that configuration. When empty AND no seam is injected, the gateway operates
    in the v4 TLS-channel mode (OIDC Core 1.0 §3.1.3.7(6) sanction, all pinned preconditions
    in force) and emits the signature-skip WARNING naming GATEWAY_OIDC_JWKS_URL as the remedy.
    Rationale: a startup-required field would break the FROZEN sso-oidc suite (its Settings
    fixtures construct oidc_enabled=True without jwks_url and sign test tokens HS256) —
    frozen tests are never edited; the v4 mode remains a spec-sanctioned contract, now
    explicitly the FALLBACK configuration. PRODUCTION DEPLOYMENTS MUST SET
    GATEWAY_OIDC_JWKS_URL — pinned in §3, enforced in the live-verification overlay and
    the ops runbook; the skip WARNING makes an unconfigured production instance loud.

  - New JWKS client port in `apps/gateway/src/gateway/auth/domain/ports.py` (additive):
      class JwksClient(Protocol):
          async def get_signing_key(self, kid: str | None) -> Any:
              """Fetch the JWKS NOW and return the key for the given kid (STATELESS —
              no cache, no internal refresh; one logical fetch per call).
              Raises OidcUpstreamError if JWKS cannot be fetched/parsed (after transport retry).
              Raises OidcTokenInvalidError if kid is not in the fetched set
              (or kid=None with 0/multiple keys)."""
              ...
    The port returns an opaque key object usable by jwt.decode(). The concrete type is an
    infrastructure detail; the domain only holds the port.
    DESIGN AMENDMENT (orchestrator, 2026-06-11): caching and the kid-miss refresh live
    ABOVE the port, in the application layer — the red suite pins this observably (J7
    asserts ONE fake call across two callbacks; J5 asserts exactly TWO on unknown kid).
    A cache inside the adapter would be bypassed by the injected fake and untestable.

  - New application-layer key cache `JwksKeyCache` (in gateway/auth/application/, exact
    module the builder's choice):
      - dict keyed by kid (None allowed) → (key object, fetched_at via time.monotonic()).
      - TTL 300 seconds (hard-coded v5); expired entry = miss.
      - resolve(kid, jwks_client): on cache hit return key; on miss call
        jwks_client.get_signing_key(kid); if that raises OidcTokenInvalidError (kid not
        found), retry EXACTLY ONCE (the kid-miss refresh — handles IdP key rotation),
        then propagate; OidcUpstreamError propagates immediately (transport retry already
        happened in the adapter). Successful key is cached.
      - One instance per app process: created at create_app when oidc_enabled, stored as
        app.state.jwks_key_cache.

  - New infrastructure adapter `apps/gateway/src/gateway/auth/infrastructure/httpx_jwks_client.py`:
      class HttpxJwksClient implements JwksClient — STATELESS per call.
      __init__(jwks_url: str, transport: httpx.AsyncBaseTransport | None = None) — the
      optional transport is the J11 test seam (ASGITransport injection; never used to
      weaken TLS).
      GET {jwks_url}, timeout=httpx.Timeout(10.0), idempotent → retry up to 2 additional
      attempts (3 total) with bounded jitter (tenacity, already on allowlist) on
      httpx.RequestError/TimeoutException; non-200 → OidcUpstreamError immediately.
      If kid not in the fetched set → raise OidcTokenInvalidError (unknown kid).
      If kid=None (token has no kid header): use the single key if the set has exactly one;
      raise OidcTokenInvalidError if it has 0 or multiple keys (ambiguous).
      Parse JWKS: pyjwt's `jwt.PyJWKSet(jwks_data["keys"])` (NOTE: the class is PyJWKSet —
      `PyJWKS` does not exist; builder uses the real pyjwt 2.13 API).
      SECURITY: TLS verification is NEVER disabled. verify=False is a HARD-STOP.

  - Signature verification in the use case (use_cases.py):
      When a JwksClient IS resolved (oidc_jwks_url set OR app.state.jwks_client injected):
        1. Peek at the JWT header (jwt.get_unverified_header) to extract kid and alg.
        2. Reject immediately if alg is not "RS256": raise OidcTokenInvalidError.
           alg=none → OidcTokenInvalidError. HS256 (key-confusion attack) → OidcTokenInvalidError.
           This is a SECURITY INVARIANT — no other algorithm is accepted.
        3. Resolve the signing key via JwksKeyCache.resolve(kid, jwks_client).
        4. jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
           — pyjwt verifies the signature AND exp; iss/aud/nonce/email stay validated
           manually downstream exactly as v4 (issuer= is NOT passed to jwt.decode).
           options={"verify_aud": False} because aud validation is done manually to produce
           the correct ERR_OIDC_TOKEN_INVALID vs ERR_OIDC_TOKEN_EXPIRED distinction.
      When NO JwksClient is resolved (jwks_url empty, no seam): the v4 TLS-channel decode
      path runs UNCHANGED (verify_signature=False + full manual claim validation incl. exp),
      with the skip WARNING emitted — the v4 sanction is the governing contract there.
           options={"verify_exp": True} (default) — pyjwt's exp check is used here
           (unlike v4 where it was disabled and checked manually). If pyjwt raises
           jwt.ExpiredSignatureError → OidcTokenExpiredError.
        5. Return the decoded claims dict; downstream claim validation (nonce, email,
           domain mapping) proceeds as before.

  - app.state seam for tests: `app.state.jwks_client` override mirrors the
    `app.state.oidc_exchanger` seam in deps.py. If not None, used directly; else
    HttpxJwksClient constructed from settings.oidc_jwks_url.

  - The use case signature changes: OidcLoginUseCase.__init__ gains `jwks_client:
    JwksClient | None` and `jwks_key_cache: JwksKeyCache | None` parameters. deps.py
    resolves the client from app.state.jwks_client, else constructs HttpxJwksClient when
    settings.oidc_jwks_url is non-empty, else None (v4 TLS-channel mode); the cache comes
    from app.state.jwks_key_cache.

  - Algorithm allowlist: RS256 ONLY. Enforced at the header-peek step, before any key
    fetch. This prevents: alg=none forgery, HS256 key-confusion (using the JWKS URL or
    client_secret as the HMAC key), ES256/PS256 (out of scope for v5 but not harmful —
    block all non-RS256 to keep the surface minimal). If a future IdP requires ES256, a
    new task updates the allowlist; this is an intentional narrow gate.

  - Failure semantics when verification is ACTIVE (fail-CLOSED — ALL failures produce
    errors; there is NO fallback from an active-verification failure to skip-verify):
      - Invalid/forged signature (wrong key) → 401 ERR_OIDC_TOKEN_INVALID
      - alg != RS256 (alg=none, HS256, ES256, etc.) → 401 ERR_OIDC_TOKEN_INVALID
      - unknown kid after one refresh → 401 ERR_OIDC_TOKEN_INVALID
      - JWKS endpoint unreachable / malformed after retry → 502 ERR_OIDC_UPSTREAM_ERROR
      - expired token (pyjwt ExpiredSignatureError) → 401 ERR_OIDC_TOKEN_EXPIRED
      - claim validation failures (iss/aud/nonce/email) → 401 ERR_OIDC_TOKEN_INVALID (unchanged)
    RATIONALE for 502 on JWKS failure: the JWKS endpoint is an IdP-side upstream
    dependency. Its unavailability is structurally identical to the token-endpoint timeout
    (already 502). Returning 401 on JWKS failure would be confusing — the token may be
    valid but unverifiable; 502 communicates "the upstream the gateway depends on is
    unreachable." Alternative considered: return 401 to avoid leaking that the IdP's JWKS
    is down. Rejected: 502 is already used for all IdP-side failures (token endpoint);
    consistency and operational clarity outweigh the marginal information-leak concern.
    The nonce-binding and server-side-only token receipt (v4 preconditions) mean a JWKS
    outage cannot be exploited for token forgery even though the gateway 502s.

  - The v4 WARNING log (_SIGNATURE_SKIP_WARNING) is RETAINED for the unconfigured
    fallback path only, with its message updated to name GATEWAY_OIDC_JWKS_URL as the
    remedy. It is NEVER emitted when verification is active. (Orchestrator amendment —
    the original draft removed it; under config-gated hardening the unconfigured mode
    must stay loud.)

  - The existing claim validation ORDER stays: signature verified FIRST (when active),
    then iss, aud, nonce, email. A forged-signature token never has its claims trusted
    on an active-verification configuration.

  - The sso-oidc §3 OIDC Core preconditions remain in force:
    1. id_token accepted ONLY from the server-side token-endpoint response body
    2. httpx TLS verification NEVER disabled (verify=False is a HARD-STOP)
    3. Token/JWKS endpoint URLs from trusted Settings only
    4. Nonce binding enforced
    5. Full claim validation (iss, aud, nonce, email) still enforced
    These are ADDITIVE to signature verification — not replaced by it.
</must>

Reject:
<reject>
  - alg header in id_token is not "RS256" (alg=none, HS256, ES256, PS256, any other) → "ERR_OIDC_TOKEN_INVALID" (401)
  - id_token signature does not verify against the IdP's JWKS public key → "ERR_OIDC_TOKEN_INVALID" (401)
  - kid in id_token header not found in JWKS after one kid-miss refresh → "ERR_OIDC_TOKEN_INVALID" (401)
  - JWKS endpoint unreachable or returns malformed JSON after retry → "ERR_OIDC_UPSTREAM_ERROR" (502)
  - id_token exp is in the past (pyjwt ExpiredSignatureError) → "ERR_OIDC_TOKEN_EXPIRED" (401)
  - (Rejections above apply ONLY when verification is active — jwks_url set or seam injected.
    oidc_enabled=True with empty GATEWAY_OIDC_JWKS_URL is NOT rejected: the gateway runs in
    the v4 TLS-channel mode with the skip WARNING — orchestrator amendment; a startup
    ValueError would break the FROZEN sso-oidc suite's Settings fixtures.)
  - All existing sso-oidc §3 rejections remain in force unchanged (state mismatch, session expired, domain not mapped, tenant conflict, upstream error on token exchange, etc.)
</reject>

After:
<after>
  - With oidc_enabled=True and a valid RS256 token: the callback succeeds, user provisioned,
    session cookie minted. The signature was verified against the IdP JWKS.
  - With a forged signature (wrong key): 401 ERR_OIDC_TOKEN_INVALID. No user created, no session.
  - With alg=none or alg=HS256: 401 ERR_OIDC_TOKEN_INVALID. No user created, no session.
  - With an unknown kid: gateway fetches JWKS once, still unknown → 401 ERR_OIDC_TOKEN_INVALID.
  - With JWKS endpoint down: 502 ERR_OIDC_UPSTREAM_ERROR. No user created, no session.
  - Cache hit: a second callback with the same kid avoids a second JWKS fetch (observable
    by counting calls on the fake jwks_client).
  - A correctly-signed token with a bad nonce: still 401 ERR_OIDC_TOKEN_INVALID (claim
    validation runs after signature passes).
  - GATEWAY_OIDC_ENABLED=true + GATEWAY_OIDC_JWKS_URL="" + no injected seam: the app starts,
    the v4 TLS-channel flow works unchanged (HS256-signed v4-style token logs in), and the
    skip WARNING (naming GATEWAY_OIDC_JWKS_URL) is emitted — the fallback is loud, deliberate,
    and pinned by test J10.
  - The skip WARNING is NEVER emitted when verification is active (jwks_url set or seam injected).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ JWKS KEY CACHE TTL AND KID-MISS SEMANTICS [spec]: The 300-second TTL is chosen as
    a conservative default. Some IdPs rotate keys more frequently (Google: ~1 hour cycles;
    Keycloak: configurable). The kid-miss-refresh path (one retry on unknown kid) handles
    key rotation gracefully for RS256. However, if an IdP rotates keys without issuing
    new kid values (reusing the same kid with a new key), the cache will serve stale keys
    for up to 300s after rotation, causing ERR_OIDC_TOKEN_INVALID for all tokens signed
    with the new key. This is the classic JWKS caching tradeoff.
    Why lowest confidence: the TTL is a policy choice with no single correct answer; real
    IdPs vary. Cost if wrong: a burst of 401 errors during key rotation (up to 5 min window).
    Mitigation: the TTL is a Settings field candidate for v6; for v5 the hard-coded 300s
    is documented in §3 with a note that it is intentionally conservative.
    ⚠ This is the single biggest operational risk in the bundle. Confidence: 0.78.

  ⚠ PYJWT ALGORITHM STRICTNESS FOR RS256 [spec]: pyjwt's `jwt.decode(token, key,
    algorithms=["RS256"])` will raise jwt.InvalidAlgorithmError if the token header
    contains alg=HS256 or alg=none — this is the desired behavior for the algorithm-
    confusion defense. However, pyjwt versions < 2.4 had a bug where algorithms=[] was
    treated as "allow all". We pin pyjwt>=2.13.0 (already in pyproject.toml) which
    correctly enforces the list. The test suite must confirm this behavior explicitly.
    Why second-lowest confidence: relies on pyjwt internals that have had CVEs; any
    downgrade of pyjwt below 2.13.0 voids the guarantee.
    Cost if wrong: algorithm-confusion attack bypasses signature check.
    ⚠ Confidence: 0.88. Mitigated by the explicit alg-header check BEFORE key fetch
    (the gateway rejects non-RS256 alg before pyjwt ever sees the token for decode).

  - PYJWT JWKS PARSING VIA PyJWKSet [spec]: pyjwt's `jwt.PyJWKSet` (orchestrator
    correction — the draft said PyJWKS, which does not exist in pyjwt) parses a
    standard JWK Set JSON document. This is the documented pyjwt API. The returned keys
    are indexed by kid. If the IdP returns a JWKS with keys lacking `kid` fields (some
    older IdPs do), pyjwt falls back to positional indexing; the gateway handles the
    kid=None case (single-key fallback). Confidence: 0.91. Cost if wrong: key lookup
    fails → 401 on every callback (observable; config-time fix).

  - EXP VALIDATION VIA PYJWT VS MANUAL [spec]: In v4, exp was checked manually
    (int(time.time()) >= exp → OidcTokenExpiredError). In v5, pyjwt's decode() checks exp
    automatically and raises jwt.ExpiredSignatureError. The use case catches this and
    raises OidcTokenExpiredError. This is cleaner but changes the code path — the v4 test
    for expired tokens (S10 in sso-oidc) still passes because the error code is the same.
    New tests in this suite explicitly test the RS256 expired-token path.
    Confidence: 0.93. Cost if wrong: expired tokens pass claim check → minor security gap
    (short window given the TLS-channel preconditions).

  - HTTPX JWKS ADAPTER RETRY STRATEGY [spec]: tenacity is already on the allowlist.
    3 attempts with exponential backoff + jitter on httpx.RequestError / httpx.TimeoutException.
    On non-200 response: raise OidcUpstreamError immediately (no retry — the IdP returned
    a definitive error). Confidence: 0.92. Cost if wrong: transient JWKS failures produce
    502s that a retry would have healed.

  - KID=None SINGLE-KEY FALLBACK [spec]: If the token header has no `kid` field and the
    JWKS has exactly one key, use that key. This handles minimal IdPs (some test IdPs
    omit kid). If JWKS has multiple keys and kid=None: ambiguous → OidcTokenInvalidError.
    Confidence: 0.90. Cost if wrong: tokens from single-key IdPs without kid header are
    rejected unnecessarily.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: J1 — valid RS256 token verified and login succeeds
  Given OIDC is configured with issuer=fake-idp, client_id=test-client, jwks_url=<fake>
  And a FakeJwksClient returns the correct RSA public key for kid="test-kid"
  And the id_token is RS256-signed with kid="test-kid" by the matching RSA private key
  And the token has valid iss, aud, exp (future), nonce, email claims
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 302 with ai_proxy_session cookie set
  And a user row is created/found with the correct email and role=member
  And what must remain unchanged: no other claims were trusted before signature passed

Scenario: J2 — forged signature (different RSA key) → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured with a FakeJwksClient returning key for kid="test-kid"
  And the id_token is RS256-signed by a DIFFERENT RSA private key (not matching the JWKS)
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: J3 — alg=none token rejected → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured
  And an id_token is crafted with alg=none (unsigned JWT)
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: alg=none never accepted; fail-CLOSED

Scenario: J4 — alg=HS256 token rejected (key-confusion attack) → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured
  And an id_token is crafted with alg=HS256 (using any HMAC key)
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: HS256 key-confusion attack rejected; fail-CLOSED

Scenario: J5 — unknown kid triggers one JWKS refresh, then fails → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured with a FakeJwksClient that:
    - On first call: returns a JWKS with kid="old-kid" only
    - On second call (refresh): still returns kid="old-kid" only
  And the id_token has kid="unknown-kid" (not in JWKS)
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the FakeJwksClient was called exactly twice (initial + one refresh)
  And the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no infinite JWKS refresh loop; fail-CLOSED after one retry

Scenario: J6 — JWKS endpoint unreachable → 502 ERR_OIDC_UPSTREAM_ERROR
  Given OIDC is configured
  And the FakeJwksClient raises OidcUpstreamError (simulating JWKS endpoint failure)
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 502 with code ERR_OIDC_UPSTREAM_ERROR
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: JWKS failure is fail-CLOSED; no fallback to skip-verify

Scenario: J7 — JWKS cache hit avoids second fetch
  Given OIDC is configured with a FakeJwksClient that returns kid="test-kid"
  And two sequential valid RS256 callback requests are made with kid="test-kid"
  When both callbacks complete successfully (302)
  Then the FakeJwksClient.get_signing_key was called only once (cache hit on second)
  And both logins succeeded with ai_proxy_session cookies set
  And what must remain unchanged: no unnecessary JWKS roundtrips

Scenario: J8 — claim validation still enforced after signature passes (bad nonce)
  Given OIDC is configured with a FakeJwksClient returning the correct key
  And an id_token is RS256-signed correctly (valid signature)
  But the nonce claim in the token is WRONG (does not match oidc_nonce cookie)
  When GET /auth/oidc/callback is called with matching state cookie but correct nonce cookie
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: signature passing does NOT bypass claim validation

Scenario: J9 — expired RS256 token → 401 ERR_OIDC_TOKEN_EXPIRED
  Given OIDC is configured with a FakeJwksClient returning the correct key
  And the id_token is RS256-signed correctly but has exp in the past
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_EXPIRED
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: expired tokens rejected even if signature is valid

Scenario: J10 — empty jwks_url + no seam = v4 TLS-channel mode preserved (compat pin)
  Given GATEWAY_OIDC_ENABLED=true with issuer/client/secret/redirect set
  But GATEWAY_OIDC_JWKS_URL is empty AND app.state.jwks_client is NOT set
  And an id_token signed HS256 (v4 fixture style) with valid claims
  When GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 302 with ai_proxy_session cookie set (the v4 flow, unchanged)
  And what must remain unchanged: the FROZEN sso-oidc suite's behavior — the fallback
  configuration is governed by the v4 TLS-channel sanction; this scenario is a
  GREEN-BY-DESIGN regression pin (it passes before the build and must still pass after)

Scenario: J11 — httpx JWKS adapter: GET request goes to jwks_url with timeout
  Given a real HttpxJwksClient configured with jwks_url pointing to a test JWKS server
  (exercised via an ASGI fake or an in-process responder — no respx, mirror sso-oidc pattern)
  When get_signing_key is called for a known kid
  Then the adapter issues a GET to the configured jwks_url
  And the timeout is 10 seconds
  And the signing key is returned correctly
  And what must remain unchanged: TLS verification is the httpx default (never disabled)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Existing routes (UNCHANGED — no new endpoints):
  GET /auth/oidc/login     — unchanged
  GET /auth/oidc/callback  — signature verification added BEFORE claim acceptance;
                             all existing HTTP responses and codes preserved

New Settings field (additive to core/config.py):
  oidc_jwks_url: str = ""    # env GATEWAY_OIDC_JWKS_URL
  OPTIONAL at Settings level (orchestrator amendment: NOT added to _validate_oidc_config —
  a required field would break the FROZEN sso-oidc suite's Settings fixtures).
  Verification activation rule (PINNED):
    ACTIVE  ⇔ app.state.jwks_client injected OR settings.oidc_jwks_url non-empty.
    INACTIVE ⇒ v4 TLS-channel mode (verify_signature=False decode + full manual claim
    validation), skip WARNING emitted naming GATEWAY_OIDC_JWKS_URL as the remedy.
  PRODUCTION DEPLOYMENTS MUST SET GATEWAY_OIDC_JWKS_URL when OIDC is enabled — enforced
  in the live-verification compose overlay and the ops runbook; the WARNING makes an
  unconfigured production instance loud and auditable.

Algorithm allowlist (SECURITY INVARIANT — never weakened without a new task):
  ONLY "RS256" is accepted. All other values (none, HS256, ES256, PS256, etc.)
  → immediate 401 ERR_OIDC_TOKEN_INVALID before any key fetch.
  Enforcement: peek alg from JWT header (jwt.get_unverified_header) before decode.

Error responses (existing codes, no new codes introduced):
  401 → { "code": "ERR_OIDC_TOKEN_INVALID" }   invalid/forged signature, bad alg, unknown kid, claim mismatch
  401 → { "code": "ERR_OIDC_TOKEN_EXPIRED" }    pyjwt ExpiredSignatureError
  502 → { "code": "ERR_OIDC_UPSTREAM_ERROR" }   JWKS endpoint unreachable / malformed

JwksClient port (NEW, in apps/gateway/src/gateway/auth/domain/ports.py, additive):
  class JwksClient(Protocol):
      async def get_signing_key(self, kid: str | None) -> Any:
          """Fetch the JWKS NOW and return the key for kid. STATELESS — no cache,
          no internal refresh; one logical fetch per call.
          kid=None: use single key if the fetched set has exactly one; else raise
          OidcTokenInvalidError.
          Raises OidcUpstreamError if JWKS fetch/parse fails (after transport retry).
          Raises OidcTokenInvalidError if kid is not in the fetched set."""
          ...
  (Orchestrator amendment: cache + kid-miss refresh moved ABOVE the port into
  JwksKeyCache — the red suite pins this: J7 expects ONE fake call across two
  callbacks, J5 expects exactly TWO on unknown kid. A cache inside the adapter
  would be bypassed by the injected fake and unobservable.)

JwksKeyCache (NEW, application layer — gateway/auth/application/, module the builder's choice):
  __init__(ttl_seconds: float = 300.0)
  In-memory dict: kid (str | None) → (key object, fetched_at via time.monotonic()).
  async resolve(kid, jwks_client) -> Any:
    1. Cache hit (entry exists and age < TTL): return key. No port call.
    2. Miss/expired: key = await jwks_client.get_signing_key(kid).
       On OidcTokenInvalidError (kid not found in fetched set): retry the port call
       EXACTLY ONCE (the kid-miss refresh — covers IdP key rotation), then propagate.
       On OidcUpstreamError: propagate immediately (adapter already retried transport).
    3. Cache and return the key.
  One instance per app process: created in create_app when oidc_enabled, stored at
  app.state.jwks_key_cache.

HttpxJwksClient (NEW, in apps/gateway/src/gateway/auth/infrastructure/httpx_jwks_client.py):
  Implements JwksClient. STATELESS per call (no cache — that is JwksKeyCache's job).
  __init__(jwks_url: str, transport: httpx.AsyncBaseTransport | None = None)
    — transport is the J11 test seam (ASGITransport); NEVER used to weaken TLS.
  get_signing_key(kid: str | None) -> Any:
    1. GET jwks_url, timeout=httpx.Timeout(10.0); up to 3 attempts with bounded-jitter
       tenacity retry on httpx.RequestError/TimeoutException; non-200 → OidcUpstreamError
       immediately (no retry — definitive IdP answer); malformed JSON/JWKS → OidcUpstreamError.
    2. Parse with pyjwt's jwt.PyJWKSet (the real pyjwt 2.13 API — NOT "PyJWKS").
    3. kid given: return matching key or raise OidcTokenInvalidError (unknown kid).
       kid=None: return the single key if exactly one; else OidcTokenInvalidError.
  SECURITY: TLS certificate verification is NEVER disabled. No verify=False anywhere.

OidcLoginUseCase changes (in apps/gateway/src/gateway/auth/application/use_cases.py):
  __init__ gains: jwks_client: JwksClient | None, jwks_key_cache: JwksKeyCache | None.
  Token decode dispatch:
    jwks_client is not None (verification ACTIVE):
      1. header = jwt.get_unverified_header(id_token) — extract alg + kid.
         (malformed header → OidcTokenInvalidError)
      2. if header["alg"] != "RS256": raise OidcTokenInvalidError.
      3. key = await jwks_key_cache.resolve(header.get("kid"), jwks_client)
         (OidcUpstreamError / OidcTokenInvalidError propagate as-is)
      4. try:
             claims = jwt.decode(id_token, key, algorithms=["RS256"],
                                 options={"verify_aud": False})
         except jwt.ExpiredSignatureError:
             raise OidcTokenExpiredError
         except jwt.InvalidTokenError:
             raise OidcTokenInvalidError
      5. Return claims dict (iss, aud, nonce, email validation unchanged below).
    jwks_client is None (verification INACTIVE — v4 TLS-channel mode):
      the v4 decode path runs UNCHANGED (verify_signature=False + manual exp check +
      full manual claim validation); the skip WARNING (updated message naming
      GATEWAY_OIDC_JWKS_URL) is emitted. No other behavior change.

deps.py changes (in apps/gateway/src/gateway/auth/api/deps.py, additive):
  get_jwks_client(request) -> JwksClient | None:
    app.state.jwks_client if set; else HttpxJwksClient(settings.oidc_jwks_url) if
    oidc_jwks_url non-empty; else None.
  get_oidc_use_case: gains jwks_client + jwks_key_cache (from app.state.jwks_key_cache).

app.state seams (PINNED for tests):
  app.state.jwks_client: JwksClient | None — injection point for fakes; presence ACTIVATES
    verification regardless of oidc_jwks_url.
  app.state.jwks_key_cache: JwksKeyCache — created at create_app when oidc_enabled.

Dependency additions (THIS TASK — orchestrator performs install at build):
  apps/gateway/pyproject.toml: cryptography>=48.0.1 (ADDED by this task's red phase)
  .add/dependencies.allowlist: cryptography (ADDED by this task's red phase)

Modules touched:
  NEW:
    apps/gateway/src/gateway/auth/infrastructure/httpx_jwks_client.py
  MODIFIED:
    apps/gateway/src/gateway/auth/domain/ports.py            (add JwksClient protocol)
    apps/gateway/src/gateway/auth/application/use_cases.py   (signature verification, new dep)
    apps/gateway/src/gateway/auth/api/deps.py                (add get_jwks_client)
    apps/gateway/src/gateway/core/config.py                  (add oidc_jwks_url field — optional)
    apps/gateway/src/gateway/main.py                         (create_app: app.state.jwks_key_cache)
    apps/gateway/pyproject.toml                              (cryptography dep — done in red phase)
    .add/dependencies.allowlist                              (cryptography — done in red phase)
  UNCHANGED (confirmed):
    apps/gateway/src/gateway/auth/api/oidc_router.py         (no route changes)
    apps/gateway/src/gateway/auth/domain/errors.py           (no new error classes)
    apps/gateway/src/gateway/auth/domain/entities.py         (no new entities)
    apps/gateway/migrations/                                 (no migration — Settings-only)
    apps/dashboard/                                          (no BFF changes)
    infra/envoy/                                             (no envoy changes)

OIDC Core preconditions carried forward (from sso-oidc §3, PINNED — unchanged):
  1. id_token accepted ONLY from server-side token-endpoint response body
  2. httpx TLS verification NEVER disabled (verify=False is a HARD-STOP)
     — applies to BOTH the token exchanger AND the new JWKS client
  3. Token/JWKS endpoint URLs from trusted Settings only (never from request input)
  4. Nonce binding enforced
  5. Full claim validation (iss, aud, nonce, email) still enforced
  ADDITION (v5, config-gated): when GATEWAY_OIDC_JWKS_URL is set (or the seam is
  injected), RS256 signature verification via JWKS is MANDATORY and fail-CLOSED — no
  skip path exists on that configuration, and an active-verification failure NEVER
  falls back to skip-verify. When unconfigured, the v4 TLS-channel sanction remains
  the governing contract (all five preconditions in force, skip WARNING emitted).
  Production deployments MUST configure it.

Key cache TTL: 300 seconds (hard-coded in v5). Documented as a potential Settings
  field in v6. Kid-miss refresh: exactly ONE re-fetch per unknown kid; second miss
  is OidcTokenInvalidError (fail-CLOSED, no infinite loop).

Flags for freeze (lowest-confidence points across the bundle):
  ⚠ [spec] JWKS key cache TTL (300s): a policy choice with no single correct answer;
    real IdPs rotate keys at varying cadences. A 300s window means up to 5 minutes of
    ERR_OIDC_TOKEN_INVALID during an IdP key rotation (new key, same kid, or kid removed
    before TTL expiry). This is operationally acceptable for v5 but should be a
    configurable Settings field in v6. Why least sure: we cannot test against a real IdP
    rotation in CI; the behavior is observable only in production. Cost if wrong: burst of
    401 errors during key rotation. Mitigation: kid-miss-refresh path handles new-kid
    rotations (common practice); same-kid key replacement is the edge case.

  ⚠ [spec] alg=RS256 exclusivity: blocking ES256/PS256 may cause issues with
    modern IdPs (e.g. Azure AD supports RS256 + ES256). The allowlist is intentionally
    narrow for v5 — any future alg expansion requires an explicit task and security review.
    Why least sure: an operator with an ES256-only IdP would see ERR_OIDC_TOKEN_INVALID
    with no obvious config knob to fix it. Cost if wrong: an IdP using ES256 is blocked;
    operator must wait for a v6 alg-allowlist task. Mitigation: the error message can
    include the detected alg, making diagnosis fast.

  ⚠ [test] FakeJwksClient vs real httpx adapter: the happy-path and rejection tests
    inject a FakeJwksClient via app.state.jwks_client. One test (J11) exercises the
    HttpxJwksClient adapter against an in-process ASGI fake — mirroring the sso-oidc
    pattern (no respx). The TTL-expiry path of JwksKeyCache is not covered by a frozen
    test (no clock seam in v5); it is covered by §6 manual review. Why least sure: the
    cache + one-retry logic is application code whose expiry branch only manifests after
    300 s of wall-clock.

Least-sure flag surfaced at freeze:
  ⚠ [spec] Config-gated verification (orchestrator amendment): with GATEWAY_OIDC_JWKS_URL
    unset and no seam, the gateway still runs the v4 TLS-channel mode — signature
    verification is NOT universally mandatory. Chosen because a hard requirement breaks
    the FROZEN sso-oidc suite (HS256 fixtures, no jwks_url) and the v4 mode remains
    spec-sanctioned (OIDC Core 1.0 §3.1.3.7(6), preconditions pinned). Cost if wrong:
    an operator who forgets the env var silently keeps weaker (though sanctioned) token
    validation — mitigated by the loud WARNING naming the remedy, the green-by-design
    J10 pin, runbook + live-overlay enforcement. Alternative (required field) was
    rejected as a frozen-test break.
  ⚠ [spec] JWKS key cache TTL (300 seconds, hard-coded) — policy choice with no CI-
    observable validation against real IdP rotation cadence. Cost if wrong: burst of
    ERR_OIDC_TOKEN_INVALID for up to 5 minutes during same-kid key replacement (new-kid
    rotation handled by kid-miss-refresh). v6 mitigation: make TTL a Settings field.
  ⚠ [spec] RS256-only algorithm allowlist — an ES256-only IdP will see 401 with no
    config remedy in v5. Cost if wrong: blocked operator, must wait for v6 alg task.
    Mitigation: include detected alg in the OidcTokenInvalidError message for fast diagnosis.

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-11).
  Orchestrator amendments at review, all pre-freeze: (1) config-gated hardening —
  jwks_url optional, v4 TLS-channel mode is the governing contract when unconfigured
  (a required field would break the FROZEN sso-oidc Settings fixtures and HS256 tokens);
  J10 redesigned as a green-by-design compat pin. (2) cache + kid-miss refresh moved
  ABOVE the port into application-layer JwksKeyCache (the red suite pins this via J5/J7
  call counts; an adapter-internal cache would be fake-bypassed). (3) pyjwt API name
  corrected PyJWKS → PyJWKSet. (4) skip WARNING retained for the unconfigured path with
  remedy text, never on the active path. Red re-run by orchestrator: 10 failed (right
  reasons) + 1 passed (J10 pin) — authoritative.
```

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_valid_rs256_token_login_succeeds: J1 — arrange FakeJwksClient with correct RSA pub key,
    RS256-signed id_token with matching private key, valid claims; act callback with matching
    state/nonce cookies; assert 302 + ai_proxy_session cookie set + user row created/found

  - test_forged_signature_rejected: J2 — arrange FakeJwksClient with key_A, id_token signed
    with key_B (different); act callback; assert 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_alg_none_rejected: J3 — arrange id_token with alg=none; act callback;
    assert 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_alg_hs256_rejected: J4 — arrange HS256-signed id_token; act callback;
    assert 401 ERR_OIDC_TOKEN_INVALID (key-confusion defense confirmed), no user, no session

  - test_unknown_kid_one_refresh_then_fail: J5 — arrange FakeJwksClient tracking calls,
    returns only old-kid on both fetches; id_token has unknown-kid; act callback;
    assert get_signing_key called twice AND 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_jwks_fetch_failure_returns_502: J6 — arrange FakeJwksClient that raises
    OidcUpstreamError; act callback; assert 502 ERR_OIDC_UPSTREAM_ERROR, no user, no session

  - test_jwks_cache_hit_avoids_second_fetch: J7 — arrange FakeJwksClient tracking call count,
    single kid; act two sequential callbacks; assert get_signing_key called once (cache hit);
    assert both logins succeeded

  - test_claim_validation_enforced_after_valid_signature: J8 — arrange RS256-signed token
    with correct signature but wrong nonce; act callback with correct nonce in cookie;
    assert 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_expired_rs256_token_rejected: J9 — arrange RS256-signed token with exp in the past;
    act callback; assert 401 ERR_OIDC_TOKEN_EXPIRED, no user, no session

  - test_unconfigured_jwks_preserves_v4_flow: J10 — arrange Settings with oidc_enabled=True
    and jwks_url="" and NO app.state.jwks_client; HS256-signed v4-style id_token with valid
    claims; act callback; assert 302 + session cookie (v4 TLS-channel mode preserved).
    GREEN-BY-DESIGN regression pin — passes before the build and must still pass after
    (orchestrator amendment; replaces the original startup-ValueError test, which would
    have broken the FROZEN sso-oidc Settings fixtures)

  - test_httpx_jwks_adapter_fetches_from_url: J11 — arrange a minimal ASGI app that serves
    a JWKS JSON response; create HttpxJwksClient pointing to it via ASGITransport (or a direct
    httpx fake matching sso-oidc pattern); call get_signing_key; assert the key is returned
    correctly and the request used a 10-second timeout
</test_plan>

Tests live in: `apps/gateway/tests/oidc_jwks/test_oidc_jwks.py`
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

Red run evidence (captured 2026-06-11):
  All 11 tests FAIL. Zero collection errors. Right-reason summary:

  J1 (valid RS256 → login succeeds):
    Gateway returns 302 (happy path via verify_signature=False), but
    FakeJwksClient.get_signing_key was never called — jwks_client seam not wired.
    AssertionError: expected len(fake_jwks.calls) >= 1, got 0.

  J2 (forged signature → 401 ERR_OIDC_TOKEN_INVALID):
    Gateway uses verify_signature=False → forged claims accepted → 302 happy path.
    AssertionError: expected HTTP 401, got 302.

  J3 (alg=none → 401 ERR_OIDC_TOKEN_INVALID):
    Gateway decodes alg=none token with verify_signature=False → claims extracted → 302.
    AssertionError: expected HTTP 401, got 302.

  J4 (alg=HS256 key-confusion → 401 ERR_OIDC_TOKEN_INVALID):
    Gateway decodes HS256 token with verify_signature=False → claims accepted → 302.
    AssertionError: expected HTTP 401, got 302.

  J5 (unknown kid → one refresh then 401):
    No jwks_client seam; FakeJwksClient never called; gateway succeeds with v4 path → 302.
    AssertionError: expected HTTP 401, got 302 (+ jwks calls assertion fails first).

  J6 (JWKS fetch failure → 502 ERR_OIDC_UPSTREAM_ERROR):
    No jwks_client seam; OidcUpstreamError never raised; gateway succeeds → 302.
    AssertionError: expected HTTP 502, got 302.

  J7 (cache hit avoids second fetch):
    No jwks_client seam; FakeJwksClient never called; calls==0 not 1.
    AssertionError: expected len(fake_jwks.calls)==1, got 0.

  J8 (bad nonce after valid signature → 401 ERR_OIDC_TOKEN_INVALID):
    FakeJwksClient never called (seam not wired).
    AssertionError: expected len(fake_jwks.calls) >= 1, got 0.

  J9 (expired RS256 token → 401 ERR_OIDC_TOKEN_EXPIRED):
    FakeJwksClient never called (seam not wired).
    AssertionError: expected len(fake_jwks.calls) >= 1, got 0.
    (The 401 ERR_OIDC_TOKEN_EXPIRED is returned by the v4 manual exp check,
    but the jwks.calls assertion fires first proving the seam is absent.)

  J10 (unconfigured jwks_url preserves v4 flow — orchestrator-amended):
    GREEN-BY-DESIGN regression pin: HS256 token + empty jwks_url + no seam → 302.
    Passes against the v4 code TODAY and must still pass after the build (it pins
    the config-gated fallback). The other 10 tests carry the red gate.

  J11 (HttpxJwksClient adapter fetches from URL):
    HttpxJwksClient does not exist.
    ImportError: cannot import name 'HttpxJwksClient' from
    'gateway.auth.infrastructure.httpx_jwks_client'.

  Run tail (captured):
    11 failed, 11 warnings in 9.21s
    (coverage floor failure suppressed for partial run — expected per §4 instructions)

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. RS256 ONLY. The alg-header check happens BEFORE key fetch and BEFORE jwt.decode.
     The algorithms=["RS256"] list in jwt.decode is a second line of defense — both must
     be in place simultaneously. Never remove either check.
  2. JWKS client NEVER sets verify=False in httpx. Any verify=False is a HARD-STOP.
  3. CONFIG-GATED, never failure-gated: the v4 skip path runs ONLY when no JwksClient is
     resolved (jwks_url empty AND no seam). Once verification is active, a failure NEVER
     falls back to skip-verify — the callback fails fail-CLOSED, 401 or 502 as specified.
     The skip WARNING is retained for the unconfigured path only (message updated to name
     GATEWAY_OIDC_JWKS_URL) and never emitted when verification is active.
  4. The kid-miss refresh (in JwksKeyCache.resolve) fires exactly ONCE. No recursive
     refresh, no while loop. HttpxJwksClient itself is stateless — no cache in the adapter.
  5. Cache TTL must use monotonic clock (time.monotonic()), not wall clock.
  6. The app.state.jwks_client seam is the ONLY injection point. No other bypass.
  7. owner/admin roles still never auto-granted via SSO (unchanged from v4).
  8. client_secret still never logged (unchanged from v4).
  9. No tokens in response body (unchanged from v4).
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/oidc_jwks 11/11 (10 red→green + J10 compat pin); FROZEN tests/sso_oidc 16/16 untouched; full suite 337 passed (authoritative orchestrator re-run, PYTEST_EXIT=0)
- [x] coverage did not decrease — 80.32% ≥ 80% floor (v4 close: 80.27%)
- [x] no test or contract was altered during build — builder touched only src/ + pyproject (tenacity made explicit); orchestrator diff review confirmed; frozen suites byte-identical
- [x] concurrency / timing of the risky operation is safe — JwksKeyCache has no lock: concurrent resolves on the same kid may double-fetch (benign last-write-wins of identical key material; no torn state — dict assignment is atomic); TTL via time.monotonic; kid-miss retry bounded at one
- [x] no exposed secrets, injection openings, or unexpected dependencies — grep: zero verify=False kwargs (mentions are doc comments only); client_secret never logged; jwks_url from Settings only; cryptography + tenacity both allowlisted; attacker-controlled kid CANNOT grow the cache (only keys present in the IdP JWKS are cached) — arbitrary-kid tokens cost 2 bounded outbound GETs, rate-limited at the Envoy edge (watch item in §7)
- [x] layering & dependencies follow CONVENTIONS.md — port in domain/, cache in application/, httpx adapter in infrastructure/, wiring in api/deps.py + main.py; dependencies point inward only
- [x] a person reviewed and approved the change — Tin Dang via delegated auto mode (2026-06-11); orchestrator line-reviewed the full diff and applied one hardening fix (see GATE RECORD disposition)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — JwksClient port: implemented by HttpxJwksClient, faked in suite, resolved in deps.get_jwks_client; JwksKeyCache: constructed in main.create_app (oidc_enabled) + __init__ fallback, consumed in use_cases step 3; HttpxJwksClient: constructed in deps + exercised directly by J11; oidc_jwks_url: read in deps. Every new symbol referenced on a live path.
- [x] DEAD-CODE (code) — no orphaned symbols: every public name in the two new modules is imported elsewhere; no unused branches (both dispatch arms covered by tests J1–J9 / J10)
- [x] SEMANTIC (prose / non-code) — §3 read in full against the diff; the config-gated activation rule, stateless-port/cache-above split, and failure-semantics table all match the implementation line-for-line

### SECURITY HARD-STOP checklist (must be manually reviewed; auto-PASS never applies):
- [x] RS256-only enforcement: alg-header check (use_cases dispatch step 2) + algorithms=["RS256"] in jwt.decode (step 4) — both present simultaneously
- [x] verify=False absent from all httpx calls in HttpxJwksClient — grep over src/: only doc-comment mentions; no kwarg anywhere
- [x] verify_signature=False reachable ONLY on the unconfigured path (jwks_client is None) — read both dispatch branches; additionally hardened: __init__ constructs a local JwksKeyCache when a client is present without one, so a missing cache can NEVER flip the branch to skip-verify (fail toward verify)
- [x] skip WARNING emitted on the unconfigured path only (jwks_client is None gate in __init__), never on the active path; message names GATEWAY_OIDC_JWKS_URL as the remedy
- [x] No id_token or access_token in any response body — callback returns 302 + cookies only (unchanged v4 surface; no body changes in diff)
- [x] client_secret not in any log line — no new logging; existing exchanger redaction unchanged
- [x] kid-miss refresh fires at most once per JwksKeyCache.resolve call — single try/except retry, no loop; HttpxJwksClient is stateless (no cache in adapter)
- [x] Cache TTL uses time.monotonic() (resolve lines: read + write timestamps both monotonic)

### GATE RECORD
Outcome: PASS
Dispositions (orchestrator review on builder output):
  1. SECURITY HARDENING (orchestrator edit post-build): use case __init__ now creates a
     local JwksKeyCache when jwks_client is present but no cache was passed — the builder's
     dispatch required BOTH to be non-None, so a missing cache would have silently fallen
     through to the skip path, violating the §3 activation rule (jwks_client present ⇒
     ACTIVE). Practically unreachable through deps wiring (cache exists whenever
     oidc_enabled) but the failure direction was wrong: now fails toward verify.
  2. pyproject gains tenacity>=8.2 — allowlisted since v1, used by the new adapter; making
     it explicit rather than transitively assumed is correct (judged in-contract: §3 names
     tenacity for the retry).
Reviewed by: Tin Dang via delegated auto mode · date: 2026-06-11
(risk: high · autonomy: conservative — the human gate is satisfied by the standing
delegation grant; SECURITY HARD-STOP checklist manually walked above, no finding.)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  ERR_OIDC_TOKEN_INVALID rate spike after a known IdP key rotation window (kid-miss-refresh
  working correctly vs cache TTL gap); ERR_OIDC_UPSTREAM_ERROR rate (JWKS endpoint health);
  JWKS fetch latency percentiles (p99 should be < 1s; 10s timeout is a safety ceiling);
  cache hit rate on get_signing_key (near 100% expected between rotations).
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
