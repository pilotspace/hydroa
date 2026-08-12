# TASK: AWS SigV4 signer for bedrock-runtime + credential resolution (pure, total, secret-safe)

slug: bedrock-sigv4-auth · created: 2026-06-15 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> v20 task 1/6 — the foundational, security-sensitive seam every Bedrock call consumes: a PURE AWS
> Signature V4 signer for the `bedrock-runtime` service + credential resolution. No upstream/chat here
> (that is bedrock-chat); this task delivers ONLY the signer + credential resolution + the config knobs.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py` (NEW) — the pure signer + credential
  resolution. stdlib only (hashlib · hmac · datetime · urllib.parse). NO boto3/botocore (allowlist).
- `apps/gateway/src/gateway/core/config.py` — add `GATEWAY_BEDROCK_*` settings (config.py:127-222 is the
  provider-settings block to mirror) + add the two Bedrock key env vars to the empty-key boot guard
  (`_UPSTREAM_KEY_ENV_VARS`, config.py:29).

Context (working folder): apps/gateway. Tests run `cd apps/gateway && uv run pytest -p no:cacheprovider
--no-cov -q`. No DB/Redis/network needed for this task (pure functions + settings).

Honors (patterns / conventions):
- SECRET DISCIPLINE (foundation + v18): the AWS secret access key + session token are a NEW secret class —
  never logged/echoed/in metric labels/span attrs/exception messages/cache keys/URLs. The signer uses the
  secret ONLY to derive the HMAC signing key; it is never returned or serialized. (mirrors the SHA-256
  hasher's timing-safe, no-plaintext discipline in keys/infrastructure/sha256_hasher.py.)
- DEFAULT-OFF / BYTE-IDENTICAL (foundation rule): no creds configured ⇒ `resolve_aws_credentials` returns
  None ⇒ the Bedrock provider is absent ⇒ gateway byte-identical to today. `GATEWAY_BEDROCK_*` default
  empty (region defaults "us-east-1" but is inert without keys).
- DESIGN-FOR-FAILURE (CLAUDE.md): the signer is PURE/TOTAL/DETERMINISTIC (timestamp injected, never reads
  the clock; no IO) so it cannot fail-slow or raise on the hot path; it is unit-provable against vectors.
- Provider-settings naming: `GATEWAY_<PROVIDER>_<FIELD>` env ↔ `<provider>_<field>` Settings attr
  (pydantic `env_prefix="GATEWAY_"`); empty-string default = feature disabled (config.py convention).

Anchors the contract cites: `bedrock_sigv4.AwsCredentials`, `bedrock_sigv4.resolve_aws_credentials`,
`bedrock_sigv4.sign_request`; `Settings.bedrock_*`; AWS SigV4 published known-answer test vectors.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a pure AWS Signature Version 4 request signer for the `bedrock-runtime` service, plus
credential resolution from gateway config — the auth primitive every Bedrock upstream call will use.

Framings weighed: a self-contained pure-Python SigV4 signer in its own module tested against AWS's
published vectors (chosen — zero new dependency, deterministic, secret-safe, unit-provable; matches the
"raw, no-SDK" gateway stance) · add boto3/botocore for SigV4 (rejected — heavyweight dep, violates the
allowlist/no-SDK discipline, drags in AWS client machinery we don't need) · an httpx.Auth subclass
(rejected — needs the request body at sign time and couples signing to the HTTP client; a pure function
is more testable and reused by both complete() and stream()).

Must:
<must>
  - `sign_request` produces, for a given (method, url, body, service, region, credentials, timestamp), the
    EXACT AWS SigV4 `Authorization` header, matching AWS's published known-answer test vectors byte-for-byte.
  - the returned headers include `x-amz-date` (YYYYMMDDTHHMMSSZ from the injected timestamp) and
    `x-amz-content-sha256` (lowercase hex SHA-256 of the body; the empty-body hash for an empty body).
  - when `credentials.session_token` is set, the headers include `x-amz-security-token` AND it is part of
    the signed (canonical) headers; when absent, neither appears.
  - the signer is PURE / TOTAL / DETERMINISTIC: same inputs → same output; no clock read, no IO, no global
    state; it never raises on well-formed inputs.
  - the secret access key NEVER appears in the returned headers, in any string the signer logs, or in the
    AwsCredentials repr/str (only the DERIVED signature MAC may appear, and only inside Authorization).
  - `resolve_aws_credentials(settings)` returns an `AwsCredentials` iff access_key_id AND
    secret_access_key AND region are all non-empty; otherwise returns None (provider absent).
  - with no Bedrock env configured, importing/constructing nothing changes existing behavior
    (default-off byte-identical) — verified by a settings-default test.
</must>

Reject:
<reject>
  - missing/empty access_key_id OR secret_access_key OR region at resolution -> resolve returns None
    (NOT an exception) so the composition root simply omits the provider.
  - a present-but-EMPTY `GATEWAY_BEDROCK_ACCESS_KEY_ID` / `GATEWAY_BEDROCK_SECRET_ACCESS_KEY` env var at
    boot -> existing empty-key boot guard raises `EmptyUpstreamKeyError` (operator misconfig, fail-fast).
</reject>

After:
<after>
  - a Bedrock upstream (next task) can obtain valid SigV4 headers for any bedrock-runtime request from a
    single pure call; correctness is pinned by the AWS vector tests.
  - credentials are resolved once at composition; secrets stay out of every observable surface.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ AWS VECTOR FIDELITY — lowest confidence: the canonical-request edge rules (URI-encoding of the path,
    trailing newline of the canonical request, header trimming/lowercasing, the empty-payload SHA-256
    sentinel) are easy to get subtly wrong. Mitigation: test against AWS's PUBLISHED known-answer vectors
    (AKIDEXAMPLE / wJalrXUtnFEMI… → documented signature), not self-authored expectations. If wrong: every
    Bedrock call 403s — caught HARD by the vector tests before any upstream is built. Confidence: 0.8.
  - [ ] bedrock-runtime uses the standard SigV4 (header-based, not query/presigned) with service name
    "bedrock" — confirm against AWS docs in the test vectors. Confidence: 0.9.
  - [ ] static keys + optional STS session token cover v20's auth scope (no IMDS/SSO/AssumeRole) —
    already fixed by the milestone Out-scope. Confidence: 0.95.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SV1 — AWS canonical vector (GET, no body)
  Given the AWS published creds AKIDEXAMPLE / wJalrXUtnFEMI…, region us-east-1, a fixed timestamp
  When sign_request signs the documented canonical GET request
  Then the Authorization header equals AWS's published expected signature byte-for-byte

Scenario: SV2 — POST with JSON body (bedrock-runtime converse shape)
  Given the same creds and a non-empty JSON body
  When sign_request signs a POST to a /model/{id}/converse path
  Then x-amz-content-sha256 is the hex SHA-256 of the body and the signature verifies against a
       recomputed canonical request (round-trip check)

Scenario: SV3 — session token included
  Given credentials WITH a session_token
  When sign_request signs a request
  Then x-amz-security-token is present AND appears in SignedHeaders
  And with NO session_token, neither x-amz-security-token nor that signed-header entry appears

Scenario: SV4 — determinism / purity
  Given identical inputs including the timestamp
  When sign_request is called twice
  Then the two results are identical
  And no clock/IO/global state was read (the timestamp is the only time source)

Scenario: SV5 — secret never leaks
  Given any credentials
  When sign_request returns and AwsCredentials is repr'd/str'd
  Then the secret_access_key substring appears in NEITHER the returned headers NOR the repr/str
  And only the derived signature MAC appears (inside Authorization)

Scenario: SV6 — credential resolution present
  Given GATEWAY_BEDROCK_ACCESS_KEY_ID + SECRET_ACCESS_KEY + REGION all set
  When resolve_aws_credentials(settings) runs
  Then it returns an AwsCredentials carrying those values (session_token None when unset)

Scenario: SV7 — credential resolution absent (default-off)
  Given any one of access_key_id / secret_access_key / region empty
  When resolve_aws_credentials(settings) runs
  Then it returns None
  And the default Settings (nothing set) yields None (byte-identical default-off)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Module: apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py
  stdlib ONLY (hashlib · hmac · datetime · urllib.parse). NO boto3/botocore/awscrt.

@dataclass(frozen=True)
class AwsCredentials:
    access_key_id: str
    secret_access_key: str          # secret — excluded from repr (field(repr=False)); never serialized
    region: str
    session_token: str | None = None
    # __repr__/__str__ must NOT expose secret_access_key (redacted)

def resolve_aws_credentials(settings) -> AwsCredentials | None
    # returns AwsCredentials iff settings.bedrock_access_key_id AND .bedrock_secret_access_key AND
    #   .bedrock_region are all truthy; session_token = settings.bedrock_session_token or None; else None

SERVICE = "bedrock"   # bedrock-runtime SigV4 service name

def sign_request(*, method: str, url: str, body: bytes, service: str, region: str,
                 credentials: AwsCredentials, timestamp: datetime) -> dict[str, str]
    # returns headers to ADD to the outgoing request:
    #   "x-amz-date":            "<YYYYMMDDTHHMMSSZ>"                       (from timestamp, UTC)
    #   "x-amz-content-sha256":  "<lowercase hex sha256(body)>"            (empty-body hash for b"")
    #   "Authorization":         "AWS4-HMAC-SHA256 Credential=<akid>/<YYYYMMDD>/<region>/<service>/
    #                             aws4_request, SignedHeaders=<lc;sorted;>, Signature=<hex>"
    #   "x-amz-security-token":  "<session_token>"   ONLY when credentials.session_token is set
    # Canonical request = METHOD\nCanonicalURI\nCanonicalQuery\nCanonicalHeaders\n\nSignedHeaders\nPayloadHash
    # SignedHeaders ⊇ {host, x-amz-content-sha256, x-amz-date} (+ x-amz-security-token when present),
    #   lowercased + sorted; host derived from url.
    # Signing key chain: kDate=HMAC("AWS4"+secret, YYYYMMDD); kRegion=HMAC(kDate, region);
    #   kService=HMAC(kRegion, service); kSigning=HMAC(kService, "aws4_request"); all HMAC-SHA256.
    # PURE · TOTAL · DETERMINISTIC (timestamp is the only time source; no IO; no globals).
    # The secret is consumed ONLY to derive kSigning; it never appears in the returned dict.

Config (apps/gateway/src/gateway/core/config.py — additive to Settings):
  bedrock_access_key_id: str = ""        # GATEWAY_BEDROCK_ACCESS_KEY_ID      (secret-adjacent)
  bedrock_secret_access_key: str = ""    # GATEWAY_BEDROCK_SECRET_ACCESS_KEY  (SECRET)
  bedrock_region: str = "us-east-1"      # GATEWAY_BEDROCK_REGION
  bedrock_session_token: str = ""        # GATEWAY_BEDROCK_SESSION_TOKEN      (optional, STS)
  bedrock_endpoint_url: str = ""         # GATEWAY_BEDROCK_ENDPOINT_URL       (override for tests/e2e)
  + append "GATEWAY_BEDROCK_ACCESS_KEY_ID" and "GATEWAY_BEDROCK_SECRET_ACCESS_KEY" to _UPSTREAM_KEY_ENV_VARS

Correctness gate: AWS's PUBLISHED SigV4 known-answer vectors (AKIDEXAMPLE / wJalrXUtnFEMI…).
NO behavior change beyond these additions; default-off byte-identical (no creds → resolve None → absent).
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, v20 fully-autonomous mandate 2026-06-15).

Least-sure flag surfaced at freeze:
  ⚠ [test] AWS VECTOR FIDELITY — the canonical-request edge rules (path URI-encoding, the empty-payload
    SHA-256 sentinel, header trim/lowercase/sort, the canonical-request trailing structure) are subtly
    easy to get wrong. Mitigation: pin against AWS's PUBLISHED known-answer vectors, not self-authored
    expectations. Cost if wrong: every Bedrock call 403s — but caught HARD by the vector tests pre-build.
  ⚠ [contract] SECRET-SAFETY SHAPE — AwsCredentials must exclude secret_access_key from repr and the
    signer must never return it; proven by the SV5 leak test asserting the secret substring is absent from
    headers + repr. Cost if wrong: a secret-in-logs finding (HARD-STOP) — so the test is mandatory.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% (pure module; the signer + resolution are fully unit-coverable).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sigv4_aws_vector_get (SV1): sign the AWS documented GET vector → Authorization == published value.
  - test_sigv4_post_body_content_hash + round-trip (SV2): x-amz-content-sha256 == sha256(body) hex;
    recompute canonical request → signature matches.
  - test_session_token_signed (SV3): with token → x-amz-security-token present + in SignedHeaders; without
    → absent from both.
  - test_determinism (SV4): two identical calls → identical dicts.
  - test_secret_never_leaks (SV5): secret substring not in any returned header value, not in repr(creds).
  - test_resolve_present (SV6): all three set → AwsCredentials with values; session_token None when unset.
  - test_resolve_absent_and_default_off (SV7): each-field-empty → None; default Settings → None.
  - test_config_boot_guard_lists_bedrock_keys: the two Bedrock key env vars are in _UPSTREAM_KEY_ENV_VARS.
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_sigv4/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py` `apps/gateway/src/gateway/core/config.py`
Strategy (ordered batches): 1. config.py Settings additions + boot-guard entries. 2. bedrock_sigv4.py:
  AwsCredentials (secret excluded from repr) + resolve_aws_credentials. 3. the signer: sha256 hex,
  canonical request, string-to-sign, HMAC signing-key chain, Authorization assembly. 4. iterate against
  the AWS vector tests until byte-exact.
Safety rule (feature-specific): the secret is used ONLY to derive the HMAC key; never returned, logged, or
  placed in repr; the signer reads no clock and does no IO (timestamp injected).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py` + config.py
Constraints: do NOT change any test or the contract; stdlib only (NO boto3/botocore); ask if unclear.
Test-format note (v19 lesson): run `ruff format` on the new test files DURING the tests phase, before the
  tests→build snapshot, so the build never touches test files (avoids scope-gate + tamper trips).

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 13/13 green in tests/bedrock_sigv4/ (incl. SV0 AWS-authoritative + SV8 path-encoding).
- [x] coverage did not decrease — new pure module fully unit-covered; no-DB fast floor re-run exit 0.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; the SV8 test was added via a
      SANCTIONED tests→build RE-CROSS (re-snapshot), not edited during build; build touched only the 2 §5 src files.
- [x] the green was EARNED, not gamed — adversarial refute-read FOUND a REAL bug: all original fixtures used
      path "/", hiding that sign_request signed the RAW path. Real Bedrock model IDs carry ':' (version suffix)
      → AWS canonicalizes to %3A → raw-':' signing 403s every versioned-model call. Added SV8 (pinned via the
      AWS-verified _signature oracle), confirmed it RED against the buggy impl, then fixed the encoding. SV0
      pins the signing math byte-for-byte to AWS's published get-vanilla vector (5fa00fa3…) — not self-referential.
- [x] concurrency / timing safe — signer is PURE/TOTAL/DETERMINISTIC: no clock read (timestamp injected), no
      IO, no globals; cannot fail-slow or raise on the hot path. SV4 asserts determinism.
- [x] no exposed secrets, injection openings, or unexpected dependencies — AwsCredentials.secret_access_key is
      field(repr=False); SV5 asserts the secret substring is absent from every returned header AND from
      repr/str; secret consumed ONLY to derive the HMAC signing key. stdlib only (NO boto3). Boot guard lists
      both Bedrock key env vars (empty-env fail-fast).
- [x] layering & dependencies follow CONVENTIONS.md — module sits in proxy/infrastructure (the upstream tier);
      config additions mirror the existing provider-settings block; zero new package dependency.
- [x] a person reviewed and approved the change — Tin's v20 fully-autonomous mandate + auto-mode delegation;
      this is autonomy:auto with complete evidence and a refute-read-EARNED green → auto-resolved PASS. NO
      security FINDING (the path bug was a correctness 403, not a vuln; secret discipline is satisfied).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `sign_request` delegates to `_signature` (shared core SV0 pins); `resolve_aws_credentials`
      reads the 5 new Settings fields; both bedrock key env vars wired into _UPSTREAM_KEY_ENV_VARS. (No
      composition-root wiring yet — that is bedrock-chat's task; this task delivers the pure seam only.)
- [x] DEAD-CODE (code) — no orphaned symbol; every public/internal symbol is exercised by a test. (The signer
      is consumed by the upstream in the next task; that is the planned, declared consumer.)
- [x] SEMANTIC (prose / non-code) — read the full implementation: canonical-request structure, signing-key
      chain, host:port handling (80/443 elided), payload hash, secret redaction all confirmed correct. KNOWN
      LIMITATION (recorded §7): canonical query-string is raw passthrough (not sorted/encoded) — NOT exercised
      by Bedrock (POST /converse + GET /invoke carry no query); a query-bearing call would need it.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto-resolved on complete evidence; refute-read EARNED — found+fixed the %3A path bug;
  no security finding) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Bedrock 403/SignatureDoesNotMatch rate; signer stays off any hot path.
Spec delta for the next loop: bedrock-chat/streaming/embeddings consume sign_request; if a future call needs
query params, the canonical query-string must be sorted + URI-encoded (today it is raw passthrough — fine
for Bedrock's query-less /converse + /invoke, but a latent gap for any query-bearing AWS call).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [TDD · folded] An external-protocol signer/encoder must be tested against REALISTIC provider-specific inputs, not just the canonical "happy" vector — all original SigV4 fixtures used path "/", which hid that the path was signed RAW; real Bedrock model IDs carry a ':' version suffix that AWS canonicalizes to %3A, so raw-':' signing 403s every versioned-model call. Evidence: the verify-gate refute-read added SV8 (a ':'-path test) which was RED against the green-but-incomplete impl. Lesson: for a signer, add at least one fixture using the ACTUAL target service's path/identifier shape.
- [ADD · folded] Pin a security primitive's core math to an AUTHORITATIVE published vector via a small exposed seam (here _signature() pinned to AWS get-vanilla 5fa00fa3…), so higher-level self-computed expectations (the contract variant) ride on a non-self-referential anchor — this is how the green stays trustworthy when the public API's exact shape has no published known-answer. Evidence: SV0 anchors SV1/SV2/SV8.
