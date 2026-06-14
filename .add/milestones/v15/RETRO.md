════════════════════════════════════════════════════════════════════════
 v15 · Dashboard feature-coverage — a surface for every backend capability
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     11/11 done         CRITERIA  10/10 met
 GATES     11 PASS            WAIVERS   none

 goal  a tenant owner/admin can manage every backend capability (model
       availability, teams & members, per-key rate/cache governance,
       spend breakdowns, routing health, response cache, guardrails,
       SSO/OIDC) through a consistent, accessible, responsive dashboard,
       with NO change to the gateway/BFF contracts

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 design-system-extension     done      PASS 0     ●●●●●●●●●
 model-management-ui         done      PASS 0     ●●●●●●●●●
 bff-patch-passthrough       done      PASS 0     ●●●●●●●●●
 teams-add-by-email          done      PASS 0     ●●●●●●●●●
 teams-governance-ui         done      PASS 0     ●●●●●●●●●
 tenant-settings-ui          done      PASS 0     ●●●●●●●●●
 routing-health-ui           done      PASS 0     ●●●●●●●●●
 governance-completion-ui    done      PASS 0     ●●●●●●●●●
 key-cache-enabled-fidelity  done      PASS 0     ●●●●●●●●●
 oidc-login-relay            done      PASS 0     ●●●●●●●●●
 feature-coverage-verify     done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 10/10 met

 LEARNINGS (23 carried)
   • SDD · open · a spec rule should name the OBSERVABLE, not a
     mechanism — §1 said "consumes useCurrentUser" yet ALSO required the
     member to see "the ErrorState carrying the BFF error title", which
     only the server 403 produces; naming the hook created a phantom
     requirement that, if honored literally, would be untestable dead
     code (evidence: refute-read DEFECT 2; resolved by the server-403
     gate). Future surface specs: state "member → role=alert error", let
     the mechanism follow.
   • TDD · open · the refute-read caught a frozen-contract clause the
     build silently skipped — §3's "PUT 404 → ErrorState (mutation
     error)" had no test AND no impl; a passing suite looked complete
     because the missing path had no red anchor (evidence:
     `test_toggle_put_404_shows_error` was added after the fact).
     Lesson: every contracted error branch needs its own §4 test BEFORE
     build, not just the happy path + the read-side rejection.
   • UDD · open · role-based NAV visibility is now a cross-surface need
     (admin-only `/models`, owner-only SSO) — the static `NAV_ITEMS`
     shows links a member cannot use; carry into a milestone-level
     nav-RBAC concern (evidence: §7 spec delta above).
   • DDD · open · additive identity-resolution (email→user_id) belongs
     in the repository inside the existing txn, tenant-scoped, with
     defense-in-depth (resolve filter + team-membership check both
     enforce isolation) — evidence: cross-tenant email returns 404 even
     if either guard alone were removed
   • SDD · open · an "exactly-one-of" optional-identifier contract is
     cleanly expressed as a Pydantic `@model_validator(mode="after")` +
     `str_strip_whitespace`, so whitespace-only collapses to "absent" —
     evidence: test_add_member_whitespace_email_422,
     test_add_member_{both,neither}_422
   • ADD · open · a build-time port (Protocol) signature change is a
     legitimate scope correction (not a contract change) when pyright
     forces it to reflect a new capability — evidence:
     TeamRepository.add_member gained `email` to clear reportCallIssue;
     §3 unchanged
   • TDD · open · the silent-mutation-failure DEFECT recurred (budget
     PATCH had no onError) — the model-mgmt lesson is now a STANDING
     rule: every useMutation needs an onError that surfaces the BffError
     title, and every contracted error branch (404/409/422) needs its
     own red→green test, not just the happy path — evidence: adversarial
     NOT-EARNED → test_budget_patch_server_error red→green
   • UDD · open · getByLabelText substring-matches aria-label across
     elements (the budget input + "Save budget for X" button collided) —
     role-scoped queries (`getByRole("textbox", {name})`) are the
     disambiguator; design labels so no control's accessible name is a
     superstring of another's — evidence: 3 budget tests failed on
     "multiple elements" until role-scoped
   • ADD · open · the bare-list-vs-{data}-envelope difference between
     `/admin/teams` (bare) and `/admin/models` ({object,data}) is a real
     footgun — GROUND must record the response envelope per-endpoint,
     not assume uniformity — evidence: §0 explicitly flagged "BARE
     array, no {data} unwrap"
   • SDD · open · a master-detail UI satisfies one "view + manage
     members" exit criterion with one route + one suite (vs a
     `/teams/[id]` route split) — the in-page selection model is the
     lighter contract when deep-linking isn't required — evidence: the
     §3 least-sure flag chose it, all 22 tests in one file
   • TDD · open · A write-only-secret surface needs an EXPLICIT negative
     DOM assertion (input == "" + no "<stored>" anywhere + no secret
     field on the role-denied path) — the first-pass tests proved the
     secret reached the PUT but not that the sentinel stayed out of the
     DOM; the adversarial refute-read caught the gap (evidence:
     test_save_sso/test_admin_forbidden_sso strengthened this re-cross).
   • UDD · open · Deterministic read errors (403/404) must set
     retry:false on the query — the OIDC tab had it (404=unconfigured)
     but Cache/Guardrails didn't, so a settled 403 would retry-storm
     before the inline alert (evidence: retry:false added to all three
     settings queries for parity).
   • ADD · open · The re-cross ritual (phase tests → advance → advance)
     is the correct mechanism to STRENGTHEN already-green tests
     mid-verify without tripping build_tampered — used here to add 4
     security assertions whose behavior already held (evidence: tripwire
     held, 16/16 still green).
   • TDD · open · A complete enum (4 circuit states) needs a fixture per
     VALUE, not per error-class — the first-pass suite covered
     closed/open/unknown but silently skipped half_open (the map handled
     it, so coverage stayed high while a value went untested); the
     adversarial refute-read caught it (evidence:
     test_half_open_state_rendered added this re-cross).
   • TDD · open · A "no config leaked on 403" assertion must enumerate
     EVERY block heading, not just one — asserting only `/retry
     policy/i` absent would pass a buggy impl that rendered empty
     Cooldown/Model-groups/Candidate card shells (evidence:
     test_member_forbidden_403 strengthened to query all four card
     headings absent).
   • UDD · open · Decorative icons paired with a visible text label must
     carry aria-hidden — the NAV icons lacked it (inconsistent with
     states.tsx); one attribute removes a redundant SR announcement
     across all 7 nav items (evidence: app-shell.tsx Icon aria-hidden
     added, axe still clean).
   • SDD · open · Hand-written per-endpoint serializers drift from their
     schema — list_keys dropped cache_enabled while patch_key forwarded
     it, silently defaulting the list to False (evidence:
     test_list_keys_reports_true_cache_enabled was RED). A shared
     KeyInfoResponse.from_domain(item) builder would make every endpoint
     forward every field by construction.
   • TDD · open · A fidelity test must distinguish "true value
     forwarded" from "constant returned" — asserting only A=true would
     pass an always-true cheat; pairing A=true with B=false pins the
     per-key semantics (evidence: the test asserts both).
   • TDD · open · a "loading shows role=status" assertion is vacuous
     unless it also proves the spinner RESOLVES (a permanent role=status
     node would pass it); assert the loading→data transition (evidence:
     refute-read D2 — fixed by adding `findByText` +
     `queryByRole("status").not...` after the T=0 assert).
   • TDD · open · a "skip-link is first focusable" assertion via
     `querySelector("a")` only proves first ANCHOR; a preceding
     focusable button/input/[tabindex] would slip through — query ALL
     focusable types to match the WCAG Must (evidence: refute-read D1 —
     fixed).
   • TDD · open · a permissive shared msw wildcard (`/api/gw/:path*`)
     silently defeats `onUnhandledRequest:"error"`; a forgotten per-test
     handler returns wrong data, not a loud failure — scope mock
     fallbacks to the paths that truly need them (evidence: refute-read
     D5 — deferred to `bff-test-harness-strict-handlers`).
   • UDD · open · jsdom axe is a PROXY: it proves
     roles/labels/landmarks/focusability but never color-contrast or
     true viewport layout; the real-browser a11y pass must be a standing
     milestone residue, not re-litigated per task (evidence: this gate +
     the v13 ui-ux-verify gate share the identical carried follow-up).
   • ADD · open · a milestone-EXIT verification suite legitimately lands
     GREEN, not RED (the behavior already shipped + gate-PASSed
     per-surface); "RED for the right reason" maps to "the consolidated
     bar is newly codified and provably held," with the earned-green
     proven by an adversarial refute-read rather than a first-run
     failure (evidence: 8/8 green on first run, then hardened after
     audit).

 DECIDE NEXT  consolidate learnings + archive-milestone v15
════════════════════════════════════════════════════════════════════════