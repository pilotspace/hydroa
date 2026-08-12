════════════════════════════════════════════════════════════════════════
 v31 · Operator-wide reconciliation + UI↔BE coverage
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  a platform operator reads cross-tenant reconciliation drift
       through an authorized ops-auth endpoint, and every implemented
       backend control-plane capability has a dashboard surface

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 operator-wide-reconciliati… done      PASS 1†    ●●●●●●●●●
 sso-login-button            done      PASS 0     ●●●●●●●●●
 alerts-events-viewer        done      PASS 0     ●●●●●●●●●
 catalog-sync-trigger        done      PASS 0     ●●●●●●●●●
 upstream-health-view        done      PASS 0     ●●●●●●●●●
 ratelimit-counter-view      done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (13 carried)
   • SDD · open · mTLS behind a reverse proxy = an XFCC EDGE-TRUST
     model: the app can only be the verify-half (match the forwarded
     fingerprint); the cryptographic check + the anti-spoof strip live
     in Envoy. Capture this as the standard shape for any future
     operator/edge-authed surface — freeze the strip+path-restriction as
     a release requirement, keep the app fail-closed (evidence: this
     task's §3 trust boundary + Least-sure flag).
   • TDD · open · for a security surface, the refute-read's EARNED-GAPs
     were COVERAGE not bugs; each security invariant needs its OWN
     explicit guard test — byte-identical 401 (incl. the
     invalid-Bearer→401 oracle case), the 403/401 denial split, and
     fail-closed default-OFF (evidence: refute UPHELD 0.87 → 4
     strengthening asserts added → re-cross).
   • ADD · open · a frozen contract's ILLUSTRATIVE literal
     (ERR_USAGE_INVALID_WINDOW/400) was corrected PRE-TEST to the
     binding "reuse existing" reality (422 ERR_PAYLOAD_INVALID) and
     annotated in §3 — a clarification caught at test-writing is
     legitimate (not a contract-weakening), as long as it moves toward
     the contract's own stated intent (evidence: §3 correction note
     2026-06-22).
   • TDD · open · jsdom's `window.location.assign` is NON-configurable →
     `vi.spyOn` throws "Cannot redefine property"; redefine
     `window.location` WHOLESALE (save original,
     `Object.defineProperty(window,"location",{configurable,writable,value:{...orig,assign:vi.fn()}})`,
     restore in afterEach) — reusable harness pattern for any
     full-page-nav component test (evidence: this task's
     sso-login.test.tsx).
   • UDD · open · for a SMALL UI change, an AskUserQuestion `preview`
     (ASCII layout) served as the design-confirm — no full render-loop
     needed; the human picked the layout before build (evidence: Tin
     approved the /login layout preview 2026-06-22).
   • SDD · open · a "add X" task where X already EXISTS → ground
     re-scopes to the real adjacent gap (here: the SSO button existed;
     the gap was the domain field) BEFORE building the wrong thing —
     surface the re-scope to the human at ground/specify (evidence: this
     task's §0 RE-SCOPE FINDING).
   • TDD · open · adding a UI control with an overlapping accessible
     name/label silently makes SIBLING tests' loose selectors
     (`/email/i`, `/log in|sign in/i`) ambiguous → sweep ALL suites for
     the loose pattern, tighten to anchored regex (`/^email$/`), and
     update superseded design assertions to the new frozen contract,
     then re-cross (evidence: 4 sibling test files updated).
   • TDD · open · a human-approved invariant RELAXATION needs a single
     combined test that exercises ALL branches at once (own visible +
     NULL visible + other hidden + correct total), not just
     one-branch-each — the refute (EG-5) showed isolated tests let a
     WHERE mutation survive (evidence: test_combined_visibility added
     post-refute).
   • SDD · open · when a contract grants a second exception to a core
     invariant, record the decision verbatim at the §3 freeze AND mirror
     it as an inline comment at the enforcing WHERE clause — so the
     relaxation is auditable from the code, not just the TASK (evidence:
     get_alerts docstring + §3 "Decided at freeze").
   • ADD · open · adding an admin-only nav item supersedes a prior
     frozen nav-count test (7→8) — update it in the TESTS phase as a
     declared change, then re-cross; carrying it into build trips
     build_tampered (evidence: nav-role-filter.test.tsx updated before
     the snapshot crossing).
   • TDD · open · a "denied/failed → state unchanged" assertion is
     VACUOUS against a fresh-DB fixture (count==0 is trivially true) —
     seed a prior SUCCESS first, then assert the count is unchanged at N
     (not 0/N+1) so the guard actually proves the denied path never
     wrote. Evidence: refute EG-2 →
     test_member_denied_leaves_existing_catalog_intact.
   • SDD · open · exposing a previously-internal (Envoy-guarded,
     no-auth) operation as an authed external endpoint is a thin, safe
     move when the op is idempotent + delegates to the same use case —
     give it a SEPARATE response model so the internal contract stays
     byte-identical (don't extend the shared DTO). Evidence:
     CatalogSyncResponse vs SyncResponse.
   • ADD · open · "reuse the existing mechanism" tasks inherit the
     upstream's design-for-failure (timeout/retry) for free — ground
     should explicitly confirm WHERE that handling lives and note what's
     still missing (circuit breaker) as a delta rather than
     re-implementing. Evidence: §0 + spec deltas.

 SPEC DELTAS    38 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v31
════════════════════════════════════════════════════════════════════════