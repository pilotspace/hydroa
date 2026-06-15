════════════════════════════════════════════════════════════════════════
 v21 · Enterprise provider: Azure OpenAI
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  A tenant can call Azure OpenAI on the OpenAI-compatible surface
       (chat/stream/tools/response_format/embeddings), routed by
       deployment with api-version, authenticated by api-key and/or
       Azure AD, billed exactly; opt-in and byte-identical when
       disabled.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 azure-auth-routing          done      PASS 11†   ●●●●●●●●●
 azure-chat                  done      PASS 1†    ●●●●●●●●●
 azure-streaming-passthrough done      PASS 0     ●●●●●●●●●
 azure-aad-auth              done      PASS 3†    ●●●●●●●●●
 azure-embeddings            done      PASS 3†    ●●●●●●●●●
 azure-verify                done      PASS 2†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (13 carried)
   • ADD · open · The scope-snapshot manifest includes ANY file present
     at the tests→build snapshot, incl. apps/gateway/.ruff_cache
     (created when ruff formats the test file DURING the tests phase).
     At gate, `touched = changed ∪ added ∪ DELETED` (add.py:2617) — so
     deleting a snapshotted cache COUNTS as an out-of-scope touch. The
     v20 "clean before gate" lesson is INCOMPLETE: cleaning a cache that
     was already in the snapshot is itself the violation. Correct fix =
     ensure transient dirs are ABSENT before the tests→build snapshot
     (point RUFF_CACHE_DIR outside the repo, or clean before
     re-snapshotting), then RE-SNAPSHOT clean (phase
     tests→advance→advance) and gate WITHOUT regenerating. Evidence:
     this task's gate failed twice on apps/gateway/.ruff_cache
     (deleted-since-snapshot) until a clean re-snapshot → PASS.
   • TDD · open · A pure, IO-free config/routing seam landed FIRST
     (before any adapter) is a high-leverage breadth-first pattern: the
     URL builder + secret-class + opt-in resolver get a frozen contract
     + full unit coverage offline, so azure-chat/embeddings/aad inherit
     a proven routing primitive. Evidence: 11/11 offline tests fully
     exercise routing+secrets with zero docker/network.
   • ADD · open · Prophylaxis that kills the scope-snapshot cache race
     (the v21-task1 lesson): run build-phase ruff with
     RUFF_CACHE_DIR=/tmp/ruffcache and pytest with `-p no:cacheprovider
     --no-cov` so NO apps/gateway/.ruff_cache/.pytest_cache/.coverage is
     ever created → the tests→build snapshot AND the gate walk both stay
     clean, no re-snapshot needed. Evidence: azure-chat gated PASS first
     try (vs azure-auth-routing's two scope_violation bounces).
   • SDD · open · An OpenAI-compatible provider (Azure) is a THIN
     passthrough — content-filter/fallback "mapping" is a no-op because
     the existing FROZEN classify_fallback_trigger already covers it
     (the "content management" pattern was added speculatively in v19;
     now exercised end-to-end). Reuse beats re-implement: zero new
     mapping code. Evidence:
     test_content_filter_400_passthrough_and_classifies green with no
     azure-specific classifier change.
   • ADD · open · An adversarial SECURITY subagent (independent, sonnet)
     at verify caught a real latent secret-leak the author's own
     refute-read missed: `raise UpstreamUnavailableError(...) from exc`
     carries the httpx request body (client_secret) on
     exc.__cause__.request.content — invisible today but harvested by
     any future crash-reporter. LESSON: for any auth/secret task, the
     verify gate MUST include an independent adversarial security pass
     (not just the author's self-review); use `from None` whenever
     wrapping an exception whose request/response could hold a secret.
     Evidence: Finding 1, fixed + regression-tested before gate.
   • TDD · open · Wrapping a raised exception in `from None` is a
     TESTABLE security property: assert `exc.value.__cause__ is None` to
     lock that a secret-bearing chain can never re-attach. Evidence:
     test_token_timeout_secret_not_in_exception_chain. <!-- tags: DDD ·
     SDD · UDD · TDD · ADD — see the `add` skill's deltas.md -->>
   • ADD · open · The adversarial security refute-read (independent
     subagent) again caught what self-review missed — here the `from
     exc` api-key-in-chain leak (review #1) AND three WEAK-test gaps
     (breaker state / network path / shared-instance identity never
     asserted). Evidence: 6 findings on a "thin passthrough" task; 4
     closed by new tests, 1 code fix, 1 systemic delta. Reinforces: run
     the adversarial verify even on tasks that look trivial.
   • TDD · open · A breaker SPY (subclass counting
     on_upstream_error/record_success) turns resilience semantics from
     "assumed" into "asserted" — a 5xx test that only checks
     `pytest.raises` would pass an implementation that never trips the
     breaker. Evidence: `_SpyBreaker` closed review #3. Adopt the spy
     pattern for all adapter resilience tests. - [SECURITY · open]
     SYSTEMIC: the shared `execute_with_retry` seam
     (upstream_retry.py:159,183) and every provider adapter's
     transport-error path (azure_upstream.py:160, openai_provider.py:97,
     bedrock, gemini, anthropic) chain the secret-bearing httpx request
     via `raise ... from exc` — reachable by a crash-reporter walking
     `__cause__.request.headers/content`. Only azure_ad.py + (now)
     azure_embeddings.py use `from None`. Evidence: review #1 + grep of
     upstream_retry.py. PROPOSED FOLLOW-UP TASK: cross-cutting
     `provider-secret-chain-hardening` — sweep all `from exc` → `from
     None` at secret-bearing transport sites (+ regression tests), spans
     multiple frozen contracts so it is its own task, NOT folded into
     v21.
   • SDD · open · The "single point AAD plugs in" spec delta from task 4
     held exactly: embeddings reused the shared token_provider instance
     with zero new auth code. Evidence:
     `test_wiring_aad_only_shares_token_provider_instance` asserts
     object identity across chat + embeddings adapters.
   • ADD · open · An auth-VERIFYING stub where the oracle MINTS the
     credential the gateway must echo (the AAD token endpoint → Bearer
     accept) makes the AAD round-trip a genuine end-to-end proof, not a
     header-presence check — the auth analogue of v20's SigV4
     independent re-impl. Evidence: AV3 + live C1 both accept ONLY the
     minted token. Reusable template for any token-exchange provider
     (Azure managed-identity, GCP SA, AWS STS).
   • TDD · open · The two-layer pattern (real-TCP earned-green pytest in
     the CI floor + an operator live double-pass that REUSES the same
     stub module) gives both CI-able wire proof and edge/cache/billing
     proof from ONE artifact. Evidence: live_v21_verify imports
     v21_azure_stub; AV9 greps the live script for idempotency
     invariants. Carry to every provider-verify task.
   • SDD · open · AAD authority is NOT env-configurable
     (resolve_azure_ad_config ignores authority; defaults to
     login.microsoftonline.com), so the LIVE pass exercised AAD only via
     the earned-green pytest layer (api-key at the edge). FOLLOW-UP: add
     GATEWAY_AZURE_AD_AUTHORITY so the live edge can drive AAD too
     (small additive config change). Evidence: v21 overlay comment +
     this task's AAD-via-pytest split.
   • ADD · open · The frozen contract's least-sure flag explicitly
     pre-authorized an operator-step fallback for the live pass; it was
     NOT needed (the e2e stack came up cleanly and both passes were
     GREEN) — surfacing the contingency up front cost nothing and the
     better outcome was still reached. Evidence: §6 LIVE record.

 DECIDE NEXT  consolidate learnings + archive-milestone v21
════════════════════════════════════════════════════════════════════════