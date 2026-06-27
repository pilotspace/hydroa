════════════════════════════════════════════════════════════════════════
 v53 · Kubernetes deployment + full e2e validation
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     10/10 done         CRITERIA  10/10 met
 GATES     10 PASS            WAIVERS   none

 goal  An operator stands up the entire ai-proxy production stack
       (Next.js dashboard, gateway, Envoy edge, Postgres, Redis, object
       store) on a Kubernetes cluster from one env-parameterized Helm
       chart, proven by an automated end-to-end suite that drives the
       goal flow plus the dashboard UI, realtime-relay, artifacts, and
       admin surfaces against the live cluster.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 helm-chart-scaffold         done      PASS 72†   ●●●●●●●●●
 datastore-statefulsets      done      PASS 14†   ●●●●●●●●●
 envoy-edge-manifests        done      PASS 16†   ●●●●●●●●●
 dashboard-chart             done      PASS 14†   ●●●●●●●●●
 migration-and-secrets       done      PASS 12†   ●●●●●●●●●
 kind-bootstrap              done      PASS 43†   ●●●●●●●●●
 e2e-core-flow               done      PASS 0     ●●●●●●●●●
 e2e-platform-features       done      PASS 0     ●●●●●●●●●
 e2e-ui                      done      PASS 0     ●●●●●●●●●
 ci-e2e-pipeline             done      PASS 7†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   helm-chart-scaffold      PASS Tin Dang <tindang.ht97@gmail.com>
   datastore-statefulsets   PASS Tin Dang <tindang.ht97@gmail.com>
   envoy-edge-manifests     PASS Tin Dang <tindang.ht97@gmail.com>
   dashboard-chart          PASS Tin Dang <tindang.ht97@gmail.com>
   migration-and-secrets    PASS Tin Dang <tindang.ht97@gmail.com>
   kind-bootstrap           PASS Tin Dang <tindang.ht97@gmail.com>
   e2e-core-flow            PASS Tin Dang <tindang.ht97@gmail.com>
   e2e-platform-features    PASS Tin Dang <tindang.ht97@gmail.com>
   e2e-ui                   PASS Tin Dang <tindang.ht97@gmail.com>
   ci-e2e-pipeline          PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 10/10 met

 LEARNINGS (25 carried)
   • TDD · open · chart TDD works by shelling out to real `helm
     template`/`helm lint` and asserting on PARSED rendered YAML (not
     template text) — the only way the green proves rendering; pyyaml +
     subprocess, no new dep (evidence: 16 tests, red-for-right-reason
     when chart absent).
   • SDD · open · a Helm chart guard that claims to mirror an app-side
     validator MUST mirror its exact predicate — exact-string
     `=="production"` silently under-guarded vs the app's `not in
     {dev,test}` (evidence: refute-read F2; fixed +
     test_secret_guard_fires_for_any_non_dev_env).
   • ADD · open · post-freeze deviation records belong in §7 OBSERVE,
     NOT appended into the frozen §3 region — editing §3 after the
     tests→build snapshot trips `contract_tampered` (evidence: this
     loop's tripwire on attempt 1; reverted §3, recorded here).
   • TDD · open · A passing render-test that only asserts a key's
     PRESENCE (not its non-empty VALUE) waved through a
     passwordless-datastore defect (evidence: refute-read F1 —
     create=true+empty-creds; closed by a fail-closed guard +
     value-non-empty assertions).
   • SDD · open · A frozen default can collide with a SIBLING task's
     frozen invariant — task-2's create=true default broke task-1's "no
     populated Secret by default"; caught at tests phase, fixed via CR-1
     (evidence: the secure-by-default flip mirroring gateway jwtSecret).
   • ADD · open · An adversarial refute-read at verify earned its keep:
     1 HIGH security defect + 1 invalid-YAML correctness bug + a
     design-for-failure timeout gap, none caught by green tests
     (evidence: F1/F2/F6 → heal cycle tests→build→verify).
   • TDD · open · A render-only helm test cannot catch a runtime shell
     defect; the base64 newline-wrap (busybox wraps at 76 cols → broke
     the inline JWKS for >57-byte secrets) slipped past 13 green tests
     and was caught only by an adversarial refute-read. Added a
     render-level guard asserting the pipeline strips newlines (`tr -d
     '\n'`), but the real proof is a live e2e exercising the
     initContainer (evidence: task-3 HIGH finding; covered live by
     e2e-core-flow).
   • ADD · open · Faithfully porting a proven config
     (`infra/envoy/envoy-prod.yaml`) carries its latent bugs forward —
     the compose entrypoint had the SAME missing `tr -d '\n'`. "Faithful
     to the proven artifact" must mean faithful to intent, hardened
     where the runtime differs (busybox vs the compose shell) (evidence:
     task-3 refute-read).
   • TDD · open · Helm renders large YAML ints in scientific notation
     (`33554432` → `3.3554432e+07`); a test caught it
     (test_deployment_wires_gateway) → fix is `| int` in the template
     before `| quote`. A render assertion on the exact string value is
     the guard (evidence: build batch-2 failure).
   • ADD · open · An explanatory CODE COMMENT can fail a substring-based
     source test (the Dockerfile comment "never `next start`" tripped
     `"next start" not in df`; the health-route comment "no cookie"
     tripped `"cookie" not in route`). Source-scan tests must target
     real constructs, or the code must avoid the forbidden token even in
     prose (evidence: build batch-1 false-positives).
   • TDD · open · A "no unauthorized edit" guard that diffs `git status
     --porcelain` is VACUOUS once the change is committed (working tree
     clean → assert trivially passes), so it gives false CI confidence
     (evidence: refute-read on
     test_kind_overlay_only_authorized_template_edit). Prefer guarding
     the INVARIANT via the rendered output (e.g. assert prod renders
     exactly the expected NetworkPolicies / probe shape) rather than VCS
     working-tree state.
   • TDD · open · Render-only (helm template) tests prove SHAPE, not
     RUNTIME: 72→85 green tests passed while THREE distinct live-only
     edge defects (no startupProbe→crashloop, dns_lookup_family AUTO→0
     hosts, kindnet-enforced NP→blocked edge) sat undetected until a
     cold `make kind-up` (evidence: all three only surfaced live). A
     live bring-up is a REQUIRED gate evidence tier for infra, not
     optional.
   • DDD · open · Environment assumptions decay: "kindnet ignores
     NetworkPolicy" was true once, false in kind v0.32/k8s v1.36 —
     assumptions about external tooling behavior must be RE-VALIDATED
     live each milestone, not carried forward (evidence: NP enforcement
     broke the edge despite the documented assumption).
   • ADD · open · A live e2e is the FIRST gate that asserts a real 200
     on the money path — it caught a prod-relevant chart defect (missing
     enc-key wiring) that task-6 smoke (health + /v1/models 401) and the
     compose e2e ("not 401/403") both passed straight over. Lesson: an
     exit-criterion e2e must assert the SUCCESS body, not just "not
     rejected". (evidence: the v2 change-request existed only because
     this task asserted 200.)
   • TDD · open · The red surfaced one assertion EARLIER than §4
     predicted (tokens, not cost) because of a hidden upstream coupling
     (recorder token↔pricing). Red-for-the-right-reason held, but the
     PREDICTED failing assertion was wrong — pre-declared red mechanisms
     should be verified against the real recording path, not assumed.
     (evidence: §4 said "fails on cost_usd>0"; actual `assert 0 == 9`.)
   • ADD · open · Mixing an imperative `kubectl set env` spike with
     declarative helm caused a server-side-apply conflict (`valueFrom` +
     `value` on one env) — the spike MUST be removed before `helm
     upgrade` reconciles. Lesson: prove fixes via the declarative path,
     not an imperative patch that later collides. (evidence: the first
     helm upgrade UPGRADE FAILED until the imperative env was deleted.)
   • ADD · open · a live e2e that drives the REAL edge catches
     edge-vs-app auth-seam defects unit/render tests can't — task-8's
     header-less-WS-blocked-by-ext_authz is the second such catch after
     task-7's enc-key (evidence: v52 live-verify was SKIPPED, so the
     relay-unreachable-through-edge defect shipped undetected until this
     task drove it live).
   • ADD · open · WS endpoints behind an ext_authz edge need an explicit
     auth-model decision (header-at-edge vs in-band-at-gateway) at
     CONTRACT time — browsers can't set WS handshake headers, so any
     header-based edge auth makes a relay unreachable (evidence: the §3
     v2 change-request was forced by exactly this, mid-build).
   • TDD · open · cross-validated close-code asserts (== 4404 for valid,
     == 4401 for bad, never-1006) make a WS honest-degrade test
     un-gameable — a wrong code or a dropped connection fails loudly
     instead of passing vacuously (evidence: refute-read Q4 PASS).
   • TDD · open · a server-accepted fixture value can still fail a
     CLIENT validator — `@kind.e2e` passed the gateway signup but the
     dashboard's zod `.email()` rejected the digit-bearing TLD, so the
     form never submitted; pick e2e fixtures that satisfy EVERY layer
     they traverse (browser zod + BFF + gateway), not just the backend
     (evidence: the v2 red caught it live; M1/M2 timed out + R2
     false-passed on the client alert).
   • TDD · open · a reject-case assertion (`alert visible` + `stay on
     URL`) can PASS for the WRONG reason when two different failures
     produce the same surface — R2 was strengthened to assert the client
     validation alert is ABSENT, pinning it to the SERVER rejection
     (evidence: R2 green even while M1 failed → the alert was
     client-side, not the gateway 401).
   • ADD · open · driving the REAL UI through the edge catches
     browser-layer contract gaps (client validation, cookie flags, guard
     redirects) that an API-only e2e (task 8) and a mocked a11y harness
     both miss — the third such live-catch in v53 after t7 (enc-key) and
     t8 (relay edge-auth) (evidence: the zod-email gap was invisible to
     the gateway-side curl that returned 201).
   • ADD · open · when the real proof surface is blocked by an external
     constraint (Actions billing), name the SUBSTITUTE proof surface IN
     the frozen contract (here: a locally-green `make ci-e2e` +
     structural validation) so "done" is honest and un-gameable, not a
     green that was never run (evidence: the freeze flag Tin approved;
     `make ci-e2e` ran green live while the workflow stayed
     un-exercised).
   • TDD · open · a CI workflow + a runbook ARE testable without
     executing them — parse the YAML/Makefile/markdown structurally
     (pinning, bounded timeout, always-teardown, required runbook
     sections, no-secret-scan) for a real red→green, with one
     always-true anchor (existing ci.yml) proving the suite isn't
     vacuous (evidence: 6-red/1-anchor before build → 7-green after).
   • ADD · open · a structural test asserts PRESENCE, not byte-IDENTITY
     — pair an "additive-guard" test with an out-of-band `git diff HEAD`
     check at verify, because "the two jobs still exist" is weaker than
     "the file is unchanged" (evidence: refute-read MED on
     `test_existing_ci_unchanged`).

 SPEC DELTAS    185 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v53
════════════════════════════════════════════════════════════════════════