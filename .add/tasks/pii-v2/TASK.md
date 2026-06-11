# TASK: PII detection v2 — expanded built-ins + per-tenant custom patterns

slug: pii-v2 · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: PII detection v2 — four new built-in PII types (IP, IBAN, API secret, passport)
         and per-tenant validated custom patterns with ReDoS heuristics + runtime budget guard

Framings weighed:

- **Additive extension of _PII_PATTERNS + JSONB custom_patterns** (chosen): the v4
  evaluator already loads `_PII_PATTERNS` at module import; adding new built-in pairs to
  that list extends masking transparently. Per-tenant custom patterns are stored as a new
  `pii_custom_patterns` key inside the existing `guardrail_configs` JSONB column (zero new
  DB migration needed). At evaluate_pre/post time the evaluator compiles custom patterns
  from the config and appends them after built-ins. This matches the v4 additive principle:
  no schema widening except inside JSONB.

- **Separate `pii_patterns` table** (rejected): would enable per-pattern metadata/audit rows,
  but adds a new table to EXPECTED_TABLES (a test-invasive change), requires an additional
  DB query on every tenant auth (or a second JOIN), and adds cross-tenant isolation risk
  (row-level security needed). No benefit over JSONB for this use case.

- **Presidio/NER-based detection** (out of scope — v5 milestone decision): package weight
  (spacy models), determinism requirement; regex-v2 first.

- **Literal field tenant-supplied** (rejected for custom patterns): a tenant-controlled
  literal creates an injection surface — a tenant could supply a literal that matches
  another built-in pattern, causing infinite re-substitution loops if the replacement
  literal is itself matched by the same or another pattern. Literals are server-derived
  from the pattern name: `[{NAME}_REDACTED]`. This is the ONLY safe design.

- **No runtime timeout on `re.search`** (Python `re` limitation — no native per-call
  timeout): mitigated by three independent defenses: (1) static ReDoS heuristic at PUT time
  rejects known-dangerous constructs, (2) input length cap (first 64 KB per message scan),
  (3) monotonic deadline between patterns (time budget per evaluate_pre call; patterns
  exceeding the budget are skipped with fail-OPEN + metric + structlog WARNING). All three
  must be present; none alone is sufficient.

**New built-in types — choice and justification:**

1. **IPv4 address** → `[IP_REDACTED]`
   Regex: `r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\b"`
   Justification: IP addresses appear in logs, API calls, and debug output; leaking a
   client's source IP identifies geographic location. Regex is linear-time: no nested
   quantifiers; each octet branch is a bounded alternation (fixed-length alternatives,
   leftmost-first ordering, no overlap). The `\b` boundaries prevent matching sub-segments
   in larger numeric strings (e.g. version numbers). False-positive risk: version strings
   like "1.2.3.4" may be masked — acceptable (security-conservative tradeoff; documented
   assumption ⚠).

2. **IBAN** → `[IBAN_REDACTED]`
   Regex: `r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"`
   Justification: IBAN numbers appear in financial integrations and are high-value PII
   (bank account identifiers). Regex is linear-time: character-class quantifiers with fixed
   upper bound {4,30} — no nested quantifiers, no alternation. False-positive risk: some
   ISO 3166-1 alpha-2 country codes followed by digits could match; rare in practice for
   LLM prompts. `\b` boundaries required.

3. **API secret / high-entropy token** → `[SECRET_REDACTED]`
   Regex: `r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{36}|xoxb-[A-Za-z0-9\-]{24,})\b"`
   Justification: LLM prompts commonly contain leaked API keys (OpenAI sk-, AWS AKIA,
   GitHub ghp_, Slack xoxb-). These are catastrophic if forwarded to the upstream LLM.
   Each alternative is anchored by a fixed prefix; quantifiers are simple {n,} or {n}
   on a character class — no nested quantifiers. Alternation does NOT overlap (prefixes
   are disjoint). Linear-time provable.

4. **Passport number (simplified)** → `[PASSPORT_REDACTED]`
   Regex: `r"\b[A-Z]{1,2}[0-9]{6,9}\b"`
   Justification: many passport formats follow a letter prefix + digit sequence; passports
   are Tier-1 PII. The regex is a conservative superset (some non-passport strings may
   match); character-class quantifiers, no alternation, no nested quantifiers — linear.
   False-positive risk: model numbers, license plate fragments — moderate. Documented.
   Rationale for including over DOB: date-of-birth strings are extremely common in natural
   language (dates appear in conversations constantly); a DOB regex at this scope would
   have a prohibitively high false-positive rate in LLM prompts.

**ReDoS static heuristic design:**

The static check at PUT time is conservative and admits false positives (rejecting safe
patterns) to minimize false negatives (accepting dangerous ones). Rules applied:

  R1. Pattern length > 256 bytes → REJECT
  R2. Pattern count in list > 8 per tenant → REJECT
  R3. `re.compile(pattern)` raises → REJECT (syntax invalid)
  R4. `re.search(pattern, "")` matches (empty string) → REJECT (masks everything,
      including replacement literals → infinite-loop risk in `re.sub`)
  R5. Backreferences (`\1` … `\9` or `(?P=name)`) → REJECT (not linear-time)
  R6. Recursive patterns (`(?R)` or `(?0)`) → REJECT (not supported in `re` but
      present in PCRE-copied patterns → compilation error or confused usage)
  R7. Nested quantifier heuristic — REJECT if the pattern string matches:
      `r"\([^)]*[+*?][^)]*\)[+*?{]"` (a group containing a quantifier, followed by
      a quantifier on the group). This catches `(a+)+`, `(a*)*`, `(a|a)*` shapes.
      NOTE: this is HEURISTIC, not complete. A pattern like `(?:[a-z]+)+` is
      correctly rejected. A pattern like `(a{1,3})+` is also caught (the inner
      `{` satisfies the quantifier-in-group check via the `{` in the outer regex).
      It will not catch every catastrophic backtracking pattern (e.g. deeply nested
      alternations without explicit quantifiers). This is documented and paired with
      the runtime budget guard.
  R8. Pattern name must match `^[A-Z][A-Z0-9_]{0,31}$` → REJECT with reason "invalid name"

**Runtime budget guard design:**

  - Per-`evaluate_pre` / `evaluate_post` call: `deadline = time.monotonic() + 0.1` (100 ms
    total budget for ALL custom pattern scans in one call — built-ins are excluded from the
    budget, they are statically verified at module load).
  - Input length cap: each message content string is capped to the first 65536 bytes
    (64 KB) before custom pattern scanning. Built-in patterns are NOT capped (they are
    linear-time and short).
  - Between each custom pattern application, check `time.monotonic() > deadline`:
      - True: increment `gateway_guardrail_events_total{guardrail="pii_mask", mode=...,
        action="budget_exceeded"}`, emit structlog WARNING, skip remaining custom patterns,
        return whatever masking has been applied so far (fail-OPEN).
  - Seam for testing: the deadline is read from a helper `_custom_pattern_deadline()` that
    defaults to `time.monotonic() + _CUSTOM_BUDGET_SECONDS` where `_CUSTOM_BUDGET_SECONDS`
    is a module-level constant (default 0.1). Tests inject a pathological pattern plus a
    seam override: the evaluator exposes `_custom_budget_seconds` as an instance attribute
    (default None → uses module constant); test sets it to 0.0 to force immediate budget
    exhaustion on the first pattern, verifying the skip-and-log path without needing an
    actual ReDoS pattern.

Must:
<must>
  - Four new built-in (pattern, literal) pairs are added to `_PII_PATTERNS` in
    `guardrail_evaluator.py`, in order after the existing four, with VERBATIM regexes
    as specified in §3 CONTRACT. These are compiled at module load. Frozen v4 literals
    and their patterns are NEVER modified.

  - `guardrail_configs` JSONB gains an optional `pii_custom_patterns` key under the
    `pii_mask` guardrail config object (NOT as a top-level key). Full shape:
      {
        "pii_mask": {
          "enabled": true,
          "mode": "mask" | "audit",
          "pii_custom_patterns": [
            {
              "name": "<NAME>",      // ^[A-Z][A-Z0-9_]{0,31}$
              "pattern": "<regex>"   // tenant-supplied regex string
            }
          ]
        }
      }
    The `literal` field is NOT stored — it is derived server-side as `[{name}_REDACTED]`.
    The `pii_custom_patterns` key is optional (absent = no custom patterns).

  - PUT /admin/guardrails validation for pii_custom_patterns (applied to the list when
    present under pii_mask):
      V1. Count: len(pii_custom_patterns) <= 8; else 422 ERR_PAYLOAD_INVALID with
          detail naming the violation.
      V2. Per-pattern: `name` must match `^[A-Z][A-Z0-9_]{0,31}$`; else 422.
      V3. Per-pattern: `pattern` length <= 256 bytes; else 422.
      V4. Per-pattern: `re.compile(pattern)` must succeed; else 422 with detail
          "invalid regex syntax: <name>".
      V5. Per-pattern: `re.search(pattern, "")` must NOT match (pattern must not
          match the empty string); else 422 with detail "pattern matches empty string: <name>".
      V6. Per-pattern: the nested-quantifier heuristic check must pass; else 422 with
          detail "pattern contains nested quantifiers (ReDoS risk): <name>".
      V7. Per-pattern: no backreferences (`\1`…`\9`, `(?P=name)`); else 422 with
          detail "pattern contains backreferences: <name>".
    On any validation failure: 422 ERR_PAYLOAD_INVALID; guardrail_configs is NOT updated.

  - The evaluator applies custom patterns in `_mask_pii` (and `_mask_pii_in_body`) AFTER
    all built-in patterns. Custom patterns respect the runtime budget guard (see §1).
    Built-in patterns are always applied first, with no budget guard.

  - Custom patterns participate in the same `pii_mask` guardrail (same mode: mask/audit,
    same fail-OPEN semantics, same Prometheus counter `gateway_guardrail_events_total
    {guardrail="pii_mask", mode=..., action=...}`). No new metric added; the
    `budget_exceeded` action value is a new valid action label on the existing counter.

  - GET /admin/guardrails returns the stored `pii_custom_patterns` list (names and
    patterns, not literals). Literals are NEVER returned (they are derived, not stored).

  - PUT /admin/guardrails partial-merge: `pii_mask` with `pii_custom_patterns` fully
    replaces the previous custom pattern list (not element-wise merge). Omitting
    `pii_custom_patterns` in a PUT that includes `pii_mask` removes the custom list.
    Omitting `pii_mask` entirely preserves the existing pii_mask config unchanged
    (standard v4 partial-merge semantics).

  - All v4 frozen built-in literals remain exactly: `[EMAIL_REDACTED]`, `[PHONE_REDACTED]`,
    `[CREDIT_CARD_REDACTED]`, `[SSN_REDACTED]`. Their patterns are never modified.
</must>

Reject:
<reject>
  - pii_custom_patterns list with more than 8 patterns → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with name not matching ^[A-Z][A-Z0-9_]{0,31}$ → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with pattern longer than 256 bytes → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with an invalid regex (re.compile fails) → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry whose pattern matches the empty string → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with nested quantifiers (heuristic) → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with a backreference → "ERR_PAYLOAD_INVALID" (422)
  - pii_custom_patterns entry with a literal field supplied by tenant (field is ignored / stripped)
  - PUT /admin/guardrails by member role → "ERR_AUTH_FORBIDDEN" (403) [v4 frozen, unchanged]
</reject>

After:
<after>
  - Four new built-in PII types (IP, IBAN, API secret, passport) are masked by the evaluator
    using their VERBATIM contracted regexes and literals.
  - A tenant's `guardrail_configs.pii_mask.pii_custom_patterns` stores a validated list of
    custom patterns; each custom pattern masks content in both pre-call and post-call paths.
  - Any PUT supplying a dangerous custom regex is rejected 422 with a detail naming the
    offending pattern.
  - GET /admin/guardrails returns stored custom patterns (name + pattern only).
  - Custom patterns in audit mode do NOT mask but increment the Prometheus counter.
  - The runtime budget guard fires `budget_exceeded` events when custom scanning exceeds
    the time budget, skipping remaining custom patterns (fail-OPEN).
  - All v4 frozen behavior (four built-ins, modes, fail-OPEN/CLOSED, literals) unchanged.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ NESTED-QUANTIFIER HEURISTIC COMPLETENESS [spec]: the static ReDoS check using
    `r"\([^)]*[+*?][^)]*\)[+*?{]"` catches the classic `(a+)+` family but misses
    alternation-based catastrophic backtracking (e.g. `(a|aa)+`). This is documented and
    paired with the runtime budget guard as the second defense. The heuristic is intentionally
    conservative (false-positive rejections are OK; false-negative admits are dangerous).
    Lowest confidence because: (1) static ReDoS detection is an open research problem;
    (2) the runtime guard makes the design safe even if a dangerous pattern slips through;
    but (3) the combination may still allow a slow pattern to burn 100ms of latency per
    request before the budget kicks in. Cost if wrong: a POST-FREEZE disposition adds
    additional heuristic rules or lowers the budget constant. Confidence: 0.72.

  ⚠ IPV4 FALSE-POSITIVE RATE [spec]: the IPv4 regex may mask version strings (e.g.
    "API v1.2.3.4", semantic version "4.0.0.1"). In LLM proxy contexts this is acceptable
    security-conservatism, but the false-positive rate in real tenant prompts is unknown.
    Cost if wrong: a per-tenant opt-out config flag for specific built-in types (v6 scope).
    Confidence: 0.78. Flagged ⚠ because once the type ships it becomes a frozen literal —
    changing it is a breaking change for existing masked data.

  - IBAN regex is a simplified syntactic check (country code + check digits + BBAN) — it
    does NOT validate the checksum (mod-97). False-positive risk is low for LLM prompts.
    Confidence: 0.90. If wrong (checksum validation needed): a note in §3; implementation
    adds a checksum validation step without changing the regex.

  - API-secret prefix list (`sk-`, `AKIA`, `ghp_`, `xoxb-`) covers the four most common
    key formats in LLM proxy use cases (OpenAI, AWS IAM, GitHub, Slack). Other providers
    (Anthropic `sk-ant-`, Hugging Face `hf_`) are omitted from v2 to keep the regex
    finite and reviewable. Cost if wrong: additive extension in a later built-in update.
    Confidence: 0.88.

  - `pii_custom_patterns` lives INSIDE `pii_mask` config (not as a top-level guardrail key)
    because custom patterns are logically part of the pii_mask guardrail's behavior and
    inherit its mode (mask/audit). This avoids introducing a new top-level key that would
    require schema changes in `GuardrailConfigRequest`. Confidence: 0.93. Cost if wrong:
    a top-level `pii_custom_patterns` key requires a Pydantic model change at the API layer.

  - Runtime budget of 100 ms is sufficient to distinguish a normal fast pattern (< 1 ms
    per field) from a pathologically slow one, without adding perceptible latency to benign
    requests. 64 KB input cap per field limits worst-case linear-time scanning to a bounded
    operation even without the budget. Confidence: 0.85. Cost if wrong: adjust the constants
    — no contract change needed (they are implementation details in the evaluator).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — new built-in IPV4 masks live through the proxy (pre-call)
  Given a tenant with pii_mask.enabled=true, mode=mask (no custom patterns)
  And a request message containing "my server IP is 192.168.1.100 for config"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[IP_REDACTED]" in place of "192.168.1.100"
  And the response is 200
  And what must remain unchanged: other text in the message is preserved verbatim

Scenario: S2 — new built-in IBAN masks live through the proxy (pre-call)
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a request message containing "my IBAN is GB82WEST12345698765432"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[IBAN_REDACTED]" in place of "GB82WEST12345698765432"
  And the response is 200
  And what must remain unchanged: surrounding text is preserved

Scenario: S3 — new built-in API secret masks live through the proxy (pre-call)
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a request message containing "my key is sk-abcdefghijklmnopqrstu1234"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[SECRET_REDACTED]" in place of the key
  And the response is 200
  And what must remain unchanged: the "my key is " prefix is preserved

Scenario: S4 — new built-in PASSPORT masks live through the proxy (pre-call)
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a request message containing "passport number A12345678 issued in"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[PASSPORT_REDACTED]" in place of "A12345678"
  And the response is 200

Scenario: S5 — custom pattern masks request message content (pre-call)
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a custom pattern {"name": "EMPLOYEE_ID", "pattern": "EMP\\d{6}"} stored via PUT /admin/guardrails
  And a request message containing "employee EMP123456 submitted the request"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[EMPLOYEE_ID_REDACTED]" in place of "EMP123456"
  And the response is 200
  And what must remain unchanged: the surrounding text "employee  submitted the request" is intact

Scenario: S6 — custom pattern masks response content (post-call)
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a custom pattern {"name": "ORDER_ID", "pattern": "ORD-\\d{8}"} stored
  And the upstream response choices[0].message.content contains "Your order ORD-12345678 is confirmed"
  When POST /v1/chat/completions (non-streaming) is made
  Then the client response has "[ORDER_ID_REDACTED]" in place of "ORD-12345678"
  And what must remain unchanged: other parts of the response body are unchanged

Scenario: S7 — PUT with invalid regex syntax → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with pii_custom_patterns containing pattern "["
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And the detail names the offending pattern name
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S8 — PUT with empty-string-matching pattern → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with pattern ".*" (matches empty string)
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And detail indicates "pattern matches empty string"
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S9 — PUT with nested-quantifier pattern → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with pattern "(a+)+" (nested quantifier)
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And detail indicates "nested quantifiers"
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S10 — PUT with over-length pattern → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with a pattern whose byte length is 257
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S11 — PUT with over-count list → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with a list of 9 custom patterns (over the 8-pattern limit)
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S12 — PUT with invalid pattern name → 422 ERR_PAYLOAD_INVALID
  Given an owner JWT
  When PUT /admin/guardrails is sent with pattern name "invalid-name!" (invalid chars)
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S13 — GET round-trips stored custom patterns
  Given a tenant with pii_mask.enabled=true, mode=mask
  And custom patterns stored via PUT /admin/guardrails
  When GET /admin/guardrails is called
  Then the response contains the same name and pattern values as stored
  And no "literal" field is present in the response (literal is derived, not stored)
  And what must remain unchanged: other guardrail config fields are unchanged

Scenario: S14 — custom pattern in audit mode does NOT mask but increments metric
  Given a tenant with pii_mask.enabled=true, mode=audit
  And a custom pattern {"name": "EMPLOYEE_ID", "pattern": "EMP\\d{6}"} stored
  And a request message containing "employee EMP123456 submitted"
  When POST /v1/chat/completions is made
  Then the upstream receives the ORIGINAL message (no masking in audit mode)
  And gateway_guardrail_events_total{guardrail="pii_mask", mode="audit", action="audited"} increments
  And the response is 200
  And what must remain unchanged: "EMP123456" is present verbatim in the upstream message

Scenario: S15 — time-budget exceeded: custom scanning skipped, fail-OPEN, metric incremented
  Given a tenant with pii_mask.enabled=true, mode=mask
  And a custom pattern stored
  And the evaluator's custom budget is forced to 0 seconds (seam override)
  When POST /v1/chat/completions is made with any message
  Then the upstream call still succeeds (fail-OPEN: budget exceeded does not block)
  And gateway_guardrail_events_total{guardrail="pii_mask", mode=..., action="budget_exceeded"}
      is incremented
  And the response is 200
  And what must remain unchanged: built-in patterns still apply (they are not budget-guarded)

Scenario: S16 — frozen v4 EMAIL literal still works after v2 extension (regression)
  Given a tenant with pii_mask.enabled=true, mode=mask (no custom patterns)
  And a request message containing "email me at user@example.com for details"
  When POST /v1/chat/completions is made
  Then the upstream receives the message with "[EMAIL_REDACTED]" (exact v4 literal)
  And the response is 200
  And what must remain unchanged: the v4 literal is byte-identical to the frozen contract value
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/guardrails
  200 -> {
    "prompt_injection": { "enabled": bool, "mode": "block"|"audit" } | null,
    "pii_mask": {
      "enabled": bool,
      "mode": "mask"|"audit",
      "pii_custom_patterns": [             // optional — absent if none stored
        { "name": str, "pattern": str }    // literal is NOT returned
      ]
    } | null
  }
  401 -> { "code": "ERR_AUTH_INVALID_KEY" }

PUT /admin/guardrails   body: {
    "prompt_injection"?: { "enabled": bool, "mode": "block"|"audit" },
    "pii_mask"?: {
      "enabled": bool,
      "mode": "mask"|"audit",
      "pii_custom_patterns"?: [
        { "name": str, "pattern": str }
      ]
    }
  }
  Semantics: partial-merge on top-level keys (v4 unchanged).
             pii_custom_patterns replaces the entire prior list (not element-wise merge).
             Absent pii_custom_patterns key WITHIN a present pii_mask block removes custom patterns.
             Absent pii_mask key entirely preserves existing pii_mask config.
  200 -> { full merged config (same shape as GET 200) }
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }
  422 -> { "code": "ERR_PAYLOAD_INVALID" }   with body containing detail naming offending pattern

Schema:
  tenants.guardrail_configs JSONB — NO new migration needed. Column already exists (d4e7f1a2b3c5).
  pii_custom_patterns is stored INSIDE the pii_mask object:
    guardrail_configs = {
      "pii_mask": {
        "enabled": true,
        "mode": "mask",
        "pii_custom_patterns": [
          { "name": "EMPLOYEE_ID", "pattern": "EMP\\d{6}" }
        ]
      }
    }
  Literal is NOT stored: derived at evaluation time as "[{name}_REDACTED]".

Validation rules (applied in order at PUT time; first failure → 422):
  V1. count(pii_custom_patterns) <= 8
      422 detail: "too many custom patterns (max 8)"
  V2. name matches ^[A-Z][A-Z0-9_]{0,31}$
      422 detail: "invalid pattern name: {name}"
  V3. len(pattern.encode()) <= 256
      422 detail: "pattern too long (max 256 bytes): {name}"
  V4. re.compile(pattern) succeeds
      422 detail: "invalid regex syntax: {name}"
  V5. re.search(pattern, "") is None  (pattern does NOT match empty string)
      422 detail: "pattern matches empty string: {name}"
  V6. backreference check: not re.search(r'\\[1-9]|\\(?P=', pattern)
      422 detail: "pattern contains backreferences: {name}"
  V7. nested-quantifier heuristic: not re.search(r'\([^)]*[+*?][^)]*\)[+*?{]', pattern)
      422 detail: "pattern contains nested quantifiers (ReDoS risk): {name}"

New built-in (pattern, literal) pairs — VERBATIM (appended to _PII_PATTERNS after SSN):
  5. IPv4 address:
     pattern: r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\b"
     literal: "[IP_REDACTED]"
  6. IBAN:
     pattern: r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"
     literal: "[IBAN_REDACTED]"
  7. API secret / high-entropy token:
     pattern: r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{36}|xoxb-[A-Za-z0-9\-]{24,})\b"
     literal: "[SECRET_REDACTED]"
  8. Passport number:
     pattern: r"\b[A-Z]{1,2}[0-9]{6,9}\b"
     literal: "[PASSPORT_REDACTED]"

Evaluator behavior (additive to v4 _evaluate_pre_inner and _mask_pii_in_body):
  _mask_pii_with_custom(messages, custom_compiled, mode, deadline, budget_exceeded_cb):
    1. Apply all 8 built-in patterns first (no budget guard — linear-time guaranteed).
    2. For each custom (compiled_pattern, literal) pair:
       a. If time.monotonic() > deadline:
            invoke budget_exceeded_cb(); break (skip remaining custom patterns)
       b. Cap content to first 65536 bytes before scanning.
       c. Apply re.sub(compiled_pattern, literal, capped_content) on each message.
    3. Return (masked_messages, any_replaced, budget_exceeded_flag).

  evaluate_pre changes:
    After step 2 (pii_mask), apply custom patterns when pii_cfg contains
    pii_custom_patterns and pii_mask is enabled. Custom patterns use the same
    mode (mask/audit) as the pii_mask guardrail.
    deadline = time.monotonic() + (self._custom_budget_seconds or _CUSTOM_BUDGET_SECONDS)
    On budget exceeded: emit budget_exceeded event (action="budget_exceeded");
    structlog WARNING; fail-OPEN (do not block).

  evaluate_post changes:
    Same custom pattern application in _mask_pii_in_body, with same deadline semantics.

  _custom_budget_seconds: instance attribute on RegexGuardrailEvaluator, default None
    (reads _CUSTOM_BUDGET_SECONDS module constant = 0.1). Tests set instance attribute
    to 0.0 to force immediate budget exhaustion.

  _CUSTOM_BUDGET_SECONDS: module-level float constant = 0.1

Runtime budget guard:
  Metric: gateway_guardrail_events_total{guardrail="pii_mask", mode=<mode>, action="budget_exceeded"}
  Behavior on exceeded: log WARNING via structlog, skip remaining custom patterns, return
  current masking state (fail-OPEN). Request is NOT blocked.
  Built-in patterns: never budget-guarded (they are linear-time and pre-verified).

Custom pattern literal derivation:
  literal = f"[{name}_REDACTED]"
  Example: name="EMPLOYEE_ID" → literal="[EMPLOYEE_ID_REDACTED]"
  The tenant CANNOT supply a literal field; if present in the PUT body it is silently stripped
  (Pydantic model does not declare it → ignored by strict parsing).

API schema changes (Pydantic models in guardrail_router.py):
  CustomPatternItem:   name: str (validated ^[A-Z][A-Z0-9_]{0,31}$), pattern: str
  PiiMaskConfig gains: pii_custom_patterns: list[CustomPatternItem] | None = None
  GuardrailConfigResponse: pii_mask becomes PiiMaskConfigResponse (includes optional
    pii_custom_patterns: list[dict] | None = None)

Modules touched (hard boundary — BUILD must not add new modules outside this list):
  - apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py
      (extend _PII_PATTERNS with 4 new pairs; add custom pattern evaluation with budget guard)
  - apps/gateway/src/gateway/tenants/api/guardrail_router.py
      (extend Pydantic models: CustomPatternItem, update PiiMaskConfig + GuardrailConfigResponse)
  - apps/gateway/pyproject.toml
      (add pii-v2 test file to ruff format exclude list)
  - apps/gateway/tests/pii_v2/__init__.py  (new, empty)
  - apps/gateway/tests/pii_v2/test_pii_v2.py  (new red suite)

No new Python packages. No new DB migration. No new metrics (budget_exceeded reuses
gateway_guardrail_events_total with a new action label value — no cardinality explosion
since action is bounded and mode is already a label dimension).

EXPECTED_TABLES: UNCHANGED.

Enforcement order: unchanged from v4 §3 CONTRACT (8-step order). Custom patterns apply
within step 4 (pre-call) and step 5.5 (post-call), after built-ins, within the same
pii_mask guardrail evaluation.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-11).
  Orchestrator review: VERBATIM built-in patterns checked for linear-time safety
  (no nested quantifiers; alternations disjoint); passport/IPv4 false-positive risk
  ACCEPTED for an opt-in guardrail (documented, per-type opt-out pinned as the v6
  path); three-layer ReDoS defense (static V1–V7 + 64KB input cap + 100ms monotonic
  budget with fail-OPEN skip) approved; server-derived literals only (tenant-supplied
  literal silently stripped) approved. Red re-run by orchestrator: 15 failed (right
  reasons) + 1 passed (S16 v4-EMAIL regression anchor, green-by-design); frozen
  guardrails suite 17/17 — authoritative.

Least-sure flag surfaced at freeze:
  ⚠ [spec] Nested-quantifier heuristic completeness: the heuristic at V7 catches
    `(a+)+` but not `(a|aa)+` or deeply nested alternations. The runtime budget guard
    is the safety net, but a slow pattern admitted by the heuristic burns 100ms of
    hot-path latency before expiry. Why least sure: static ReDoS detection is an
    open problem; no conservative regex fully classifies all dangerous patterns.
    Cost if wrong: lower the budget constant, add additional heuristic rules, or
    cap input more aggressively — all are implementation changes with no contract impact.

  ⚠ [contract] IPv4 false-positive on version strings: once `[IP_REDACTED]` ships
    as a frozen literal it becomes a contract (frozen tests pin its exact value).
    If the false-positive rate on real tenant traffic is too high, removing or
    restricting the type requires a change request. Why least sure: empirical
    false-positive rate in LLM prompt traffic is unknown pre-deployment.
    Cost if wrong: per-tenant opt-out flag for specific built-in types (v6 scope).

  ⚠ [test] Budget-exceeded seam relies on `_custom_budget_seconds` instance attribute
    injection, which is a white-box seam. If the evaluator is refactored to accept the
    budget via constructor only, the test breaks. Tagged [test] because the seam is pinned
    in the contract — BUILD must honor it.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_builtin_ipv4_masks_pre_call (S1): arrange pii_mask mask mode; message with
    "192.168.1.100"; assert upstream received "[IP_REDACTED]"; response 200

  - test_builtin_iban_masks_pre_call (S2): arrange pii_mask mask mode; message with
    "GB82WEST12345698765432"; assert upstream received "[IBAN_REDACTED]"; response 200

  - test_builtin_api_secret_masks_pre_call (S3): arrange pii_mask mask mode; message with
    "sk-abcdefghijklmnopqrstu1234"; assert upstream received "[SECRET_REDACTED]"; response 200

  - test_builtin_passport_masks_pre_call (S4): arrange pii_mask mask mode; message with
    "A12345678"; assert upstream received "[PASSPORT_REDACTED]"; response 200

  - test_custom_pattern_masks_request_content (S5): PUT custom EMPLOYEE_ID pattern;
    arrange message "employee EMP123456 submitted"; assert upstream received
    "[EMPLOYEE_ID_REDACTED]"; response 200

  - test_custom_pattern_masks_response_content (S6): PUT custom ORDER_ID pattern;
    fake upstream returns content "Your order ORD-12345678 is confirmed";
    assert client response has "[ORDER_ID_REDACTED]"; response 200

  - test_put_custom_invalid_regex_syntax (S7): PUT with pattern "["; assert 422
    ERR_PAYLOAD_INVALID; guardrail_configs unchanged

  - test_put_custom_empty_string_matching (S8): PUT with pattern ".*"; assert 422
    ERR_PAYLOAD_INVALID; detail mentions empty string

  - test_put_custom_nested_quantifier (S9): PUT with pattern "(a+)+"; assert 422
    ERR_PAYLOAD_INVALID; detail mentions nested quantifiers

  - test_put_custom_over_length_pattern (S10): PUT with pattern of 257 bytes; assert 422
    ERR_PAYLOAD_INVALID

  - test_put_custom_over_count_list (S11): PUT with 9 patterns; assert 422
    ERR_PAYLOAD_INVALID

  - test_put_custom_invalid_name (S12): PUT with name "invalid-name!"; assert 422
    ERR_PAYLOAD_INVALID

  - test_get_round_trips_custom_patterns (S13): PUT custom patterns; GET; assert name+pattern
    present; assert no "literal" key in response items

  - test_custom_pattern_audit_mode_no_mask (S14): arrange pii_mask audit mode + custom pattern;
    assert upstream receives original content; assert audited counter increments; response 200

  - test_budget_exceeded_skips_custom_fail_open (S15): set evaluator._custom_budget_seconds=0.0;
    PUT custom pattern; POST request; assert 200; assert budget_exceeded event counter increments

  - test_v4_email_literal_regression (S16): pii_mask mask mode no custom patterns;
    "user@example.com" in message; assert upstream gets "[EMAIL_REDACTED]" (exact v4 literal)
</test_plan>

Tests live in: `apps/gateway/tests/pii_v2/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `apps/gateway/tests/pii_v2/` -->

Red run evidence (captured 2026-06-11):
  15 failed, 1 passed in 13.79s

  S1 test_builtin_ipv4_masks_pre_call — RIGHT REASON:
    AssertionError: IPv4 address should be masked to [IP_REDACTED] in upstream message,
    got content: 'my server IP is 192.168.1.100 for config'
    (no IPv4 entry in _PII_PATTERNS)

  S2 test_builtin_iban_masks_pre_call — RIGHT REASON:
    AssertionError: IBAN should be masked to [IBAN_REDACTED], got content:
    'please transfer to my IBAN GB82WEST[PHONE_REDACTED]432 today'
    (no IBAN pattern; phone pattern partially matched the digit run — unrelated false positive,
    confirms IBAN is absent from the built-in list)

  S3 test_builtin_api_secret_masks_pre_call — RIGHT REASON:
    AssertionError: API secret should be masked to [SECRET_REDACTED], got content:
    'my key is sk-abcdefghijklmnopqrstu123 please help'
    (no API-secret pattern in _PII_PATTERNS)

  S4 test_builtin_passport_masks_pre_call — RIGHT REASON:
    AssertionError: Passport number should be masked to [PASSPORT_REDACTED], got content:
    'passport number A12345678 issued in 2020'
    (no passport pattern in _PII_PATTERNS)

  S5 test_custom_pattern_masks_request_content — RIGHT REASON:
    AssertionError: custom pattern should mask EMP123456 to [EMPLOYEE_ID_REDACTED],
    got: 'employee EMP123456 submitted the request'
    (PiiMaskConfig silently drops pii_custom_patterns; evaluator never applies custom patterns)

  S6 test_custom_pattern_masks_response_content — RIGHT REASON:
    AssertionError: custom pattern should mask ORD-12345678 to [ORDER_ID_REDACTED]
    in response, got content: 'Your order ORD-12345678 is confirmed and will ship soon.'
    (evaluate_post has no custom pattern application)

  S7 test_put_custom_invalid_regex_syntax — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (PiiMaskConfig drops unknown fields, no regex-syntax validation)

  S8 test_put_custom_empty_string_matching — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (no empty-string-match validation at PUT time)

  S9 test_put_custom_nested_quantifier — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (no nested-quantifier heuristic check)

  S10 test_put_custom_over_length_pattern — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (no pattern length check)

  S11 test_put_custom_over_count_list — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (no count check on pii_custom_patterns list)

  S12 test_put_custom_invalid_name — RIGHT REASON:
    AssertionError: expected HTTP 422, got 200: {"prompt_injection":null,"pii_mask":{"enabled":true,"mode":"mask"}}
    (no name-format validation)

  S13 test_get_round_trips_custom_patterns — RIGHT REASON:
    AssertionError: GET response pii_mask should contain pii_custom_patterns,
    got: {'mode': 'mask', 'enabled': True}
    (GuardrailConfigResponse / _build_response does not propagate pii_custom_patterns)

  S14 test_custom_pattern_audit_mode_no_mask — RIGHT REASON:
    AssertionError: guardrail_events_total{guardrail='pii_mask', mode='audit', action='audited'}
    should increment when custom pattern matches in audit mode; before=0.0, after=0.0
    (evaluator never processes custom patterns, no audit event emitted)

  S15 test_budget_exceeded_skips_custom_fail_open — RIGHT REASON:
    AssertionError: gateway_guardrail_events_total{guardrail='pii_mask', mode='mask',
    action='budget_exceeded'} should increment on budget exceeded; before=0.0, after=0.0
    (budget guard and _custom_budget_seconds seam do not exist yet)

  S16 test_v4_email_literal_regression — PASSES (correct: regression guard, v4 EMAIL already works)

  Frozen suite tests/guardrails/: 17/17 PASS (untouched).

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): Tenant-supplied regexes are UNTRUSTED INPUT on the
hot path — every validation rule in §3 (V1–V7) must be enforced atomically at PUT time
(reject the entire update, never partial-save). The runtime budget guard MUST be present
and MUST fail-OPEN (never block on budget exceeded). Built-in patterns are NEVER
budget-guarded. Frozen v4 literals ([EMAIL_REDACTED] etc.) are IMMUTABLE.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): custom pattern match rate per tenant; budget_exceeded
rate (spike = tenant has a slow custom regex — investigate or lower budget); false-positive
rate signal via tenant support tickets (IP/passport types); new built-in mask rate by type
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
