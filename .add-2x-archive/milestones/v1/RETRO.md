════════════════════════════════════════════════════════════════════════
 v1 · MVP: metered multi-tenant AI proxy
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     9/9 done           CRITERIA  8/8 met
 GATES     9 PASS             WAIVERS   none

 goal  a tenant owner can sign up, issue an API key, call any OpenRouter
       model through the OpenAI-compatible proxy, and see every
       request's billable cost — with budget enforcement

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 tenant-identity             done      PASS 0     ●●●●●●●●
 model-catalog               done      PASS 0     ●●●●●●●●
 api-keys                    done      PASS 0     ●●●●●●●●
 proxy-completions           done      PASS 0     ●●●●●●●●
 usage-metering              done      PASS 0     ●●●●●●●●
 budgets                     done      PASS 0     ●●●●●●●●
 edge-envoy                  done      PASS 0     ●●●●●●●●
 dashboard-shell             done      PASS 0     ●●●●●●●●
 dashboard-usage             done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 8/8 met

 LEARNINGS (12 carried)
   • DDD · open · CatalogSource as a typing.Protocol port with
     FakeCatalogSource injected via app.state decouples all tests from
     real HTTP — evidence: 15 tests ran without any network call,
     CatalogSourceUnavailableError path exercised via raise_unavailable
     flag
   • SDD · open · Single-transaction sync (upsert + snapshot +
     deactivate) is the correct safety boundary for an append-only
     ledger — evidence: test_sync_upstream_unavailable confirms zero
     rows written on source failure;
     test_sync_idempotent_when_prices_unchanged confirms no duplicate
     snapshot on re-sync
   • TDD · open · Red suite (15 failures on 404) confirmed before any
     implementation line was written — evidence: gate check output
     captured; green achieved in first implementation pass without test
     edits
   • ADD · open · Pre-existing ruff S107/RUF002 errors in frozen test
     files require pyproject.toml per-file-ignore extension rather than
     test edits — architectural decision: suppress at config level to
     maintain test immutability contract
   • DDD · open · GLOSSARY "argon2 for all keys" conflicts with hot-path
     latency requirements for high-entropy API key secrets — evidence:
     §1 assumption ⚠ flag surfaced before freeze; GLOSSARY amended in §3
     contract: "stored as SHA-256 hash" for API keys, argon2 retained
     for passwords.
   • ADD · open · lowest-confidence flag surfaced a spec/GLOSSARY
     inconsistency before any code was written — confirms freeze as the
     right gate for cross-artifact consistency.
   • TDD · open · byte-identical response contract for all authz failure
     paths (malformed/unknown/ revoked/wrong-secret) was enforced purely
     by tests — test_authz_malformed_keys_byte_identical and
     test_authz_wrong_secret_rejected_constant_time drove the
     AuthzUseCase design to always run hash comparison even for unknown
     rows, preventing content-length oracle.
   • SDD · open · explicit key_id generation at the router call site
     (uuid7() called in router, passed to use case, then to repository)
     prevents the "child row with unset parent id" bug class noted in
     the task prompt — pattern confirmed by
     test_owner_creates_key_plaintext_shown_once which validates the hex
     in the returned key matches the stored row id.
   • ADD · open · node dependencies are not governed by
     dependencies.allowlist (Python gate only) — delta: document node
     dep governance separately or extend the allowlist format; evidence:
     §3 contract note "Python dependencies.allowlist does NOT govern
     node deps".
   • UDD · open · localStorage JWT XSS risk must be surfaced in the spec
     (not hidden in code) — evidence: §1 ⚠ assumption drives the freeze
     flag; production path (httpOnly-cookie BFF) documented in §3
     contract.
   • TDD · open · frozen RTL suites with bare getByText string/regex
     matchers over-constrain the build — the builder had to gate the
     models query on usage data (sequential fetch) and initially hid the
     model ID to dodge duplicate-match errors; future UI red suites must
     scope assertions with within(<section>) so parallel queries and
     repeated strings are legal (evidence: tests 20/24/27/29 collisions,
     verify-phase fix commit)
   • UDD · open · the catalog-row scenario was satisfiable by a
     different element's text — scenario observables should name WHERE
     the text appears, not just that it appears (evidence:
     hidden-model-ID divergence caught only by manual semantic review)

 DECIDE NEXT  consolidate learnings + archive-milestone v1
════════════════════════════════════════════════════════════════════════