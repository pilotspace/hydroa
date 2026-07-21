════════════════════════════════════════════════════════════════════════
 frontdoor-persona-routing · Front-door persona routing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     9/9 done           CRITERIA  7/7 met
 GATES     9 PASS             WAIVERS   none

 goal  Every visitor who arrives at Hydroa's front door reaches a live
       next step: self-serve signup works, and a member of an existing
       tenant is routed to SSO, their invite link, or a request-access
       path instead of a dead end.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 scoped-self-serve-signup    done      PASS 0     ●●●●●●●●●
 signup-refusal-router       done      PASS 0     ●●●●●●●●●
 homepage-integration-proof  done      PASS 0     ●●●●●●●●●
 homepage-price-anchor       done      PASS 0     ●●●●●●●●●
 homepage-cta-intent-split   done      PASS 0     ●●●●●●●●●
 domain-aware-auth-routing   done      PASS 0     ●●●●●●●●●
 unified-signin-entry        done      PASS 0     ●●●●●●●●●
 pricing-tier-ladder         done      PASS 0     ●●●●●●●●●
 sso-login-oracle-closure    done      PASS 2†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   scoped-self-serve-signup PASS Tin Dang <tindang.ht97@gmail.com>
   signup-refusal-router    PASS Tin Dang <tindang.ht97@gmail.com>
   homepage-integration-pr… PASS Tin Dang <tindang.ht97@gmail.com>
   homepage-price-anchor    PASS Tin Dang <tindang.ht97@gmail.com>
   homepage-cta-intent-spl… PASS Tin Dang <tindang.ht97@gmail.com>
   domain-aware-auth-routi… PASS Tin Dang <tindang.ht97@gmail.com>
   unified-signin-entry     PASS Tin Dang <tindang.ht97@gmail.com>
   pricing-tier-ladder      PASS Tin Dang <tindang.ht97@gmail.com>
   sso-login-oracle-closure PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS (12 carried)
   • TDD · open · **"Assert against a fixture, not against the code
     under test."** A test that recomputes the expected value using the
     production call it is verifying can only ever prove
     self-consistency, never correctness. It looks rigorous and is
     vacuous in exactly one direction — the direction that matters.
     (evidence: 28 green tests that a hardcoded literal would also have
     passed)
   • ADD · open · no `flow: design` persona covers identity/auth
     architecture — the design-flow set is accessibility-auditor /
     ui-designer / ux-researcher, all UI lenses. A SECURITY design span
     on an auth surface had to run generic with `appsec-engineer` (flow:
     build, advisor) as an overlay (evidence: this task's §5 Persona
     note). Seed an identity-architect persona with `flow: design`, or
     add `design` to appsec-engineer's flow.
   • SDD · open · the anti-enumeration reasoning that protects the
     BACKEND (uniform status/body/cost, scoped-self-serve-signup §3)
     does NOT transfer to a routing UI, where the visible adaptation IS
     the observable — the only safe posture is to make the decision a
     pure client-side function of input + a static constant (evidence:
     §1 framings 2 and 4, both rejected for this reason).
   • SDD · open · a task can inherit a live oracle from a NEIGHBOURING
     frozen contract without its own diff being at fault; grounding must
     sweep the surfaces the task ROUTES TO, not just the ones it edits
     (evidence: §0 R-a — LoginForm.handleSso's 302-vs-404 preflight,
     found only because ground read the SSO destination this task
     emphasizes).
   • UDD · open · The shipped test suite is a DESIGN CONSTRAINT, not
     just a safety net — reading four auth test files decided the shape
     of this task (emphasis-not-disclosure, /login-not-/start) before
     any code existed, and both alternatives would have looked
     reasonable on a whiteboard (evidence: §0 R-a and R-b, each grounded
     in a cited line of a green test).
   • UDD · open · A frozen a11y placement can flip from liability to
     asset once an upstream surface seeds the input — domain-aware's
     panel-above-the-field is a problem when the visitor types below it,
     and exactly right when the email arrives pre-classified from
     another door (evidence: the `?email=` seed, M13).
   • TDD · open · **"When a behavior change retargets ZERO assertions,
     run the new suite against the pre-fix code before believing it."**
     The Ground-SHA worktree revert-proof turned an *argued* non-vacuity
     claim into *evidence* in ~10 minutes, and it is the only check that
     could have caught vacuous claim-seeding.
   • ADD · open · **"Treat a prose security claim in shipped code as an
     assertion requiring a test."** The false "no oracle" docstring is
     plausibly the reason nobody re-checked this route for months.
     Highest-value competency delta from this task. - [SPEC · open]
     `error_catalog.OIDC_TENANT_NOT_CONFIGURED` and
     `OIDC_NOT_CONFIGURED` share code `ERR_OIDC_NOT_CONFIGURED` with
     DIFFERENT titles, while `assert_problem` asserts status+code only.
     Any future same-code/different-title pair is an oracle no existing
     test would catch. Consider a catalog invariant test: one code ⇒ one
     title. (evidence: §0 Issues #4)
   • SDD · open · A frozen contract that permits an ALTERNATION of error
     codes (`403 | 404`) on an unauthenticated route silently licenses
     an enumeration oracle. The alternation reads as flexibility at
     freeze and as a leak in production. Prefer a single contracted
     terminal code on any unauthenticated discovery surface. (evidence:
     domain-routing-unification §3 M2's `403 | 404` line produced this
     task)
   • ADD · open · A docstring authored by a frozen contract asserted a
     security property ("no oracle between unclaimed and
     claimed-but-unconfigured") that the code beneath it never
     delivered, and it survived a security-sensitive review. The
     overclaim was load-bearing: it is plausibly *why* nobody
     re-checked. Treat a prose security claim in shipped code as an
     assertion requiring a test, not as documentation. (evidence: §0
     Issues #5; the leg was entirely untested — §3 Retarget register)
   • TDD · open · The retarget set for this task is EMPTY because the
     403 login leg had no test at all. "No test needs changing" was,
     here, evidence of a coverage hole rather than of a safe change.
     When a behavior change touches zero assertions, ask why the old
     behavior was untested before concluding the change is low-risk.
     (evidence: exhaustive grep of both codes across
     `apps/gateway/tests/`)
   • ADD · open · No `flow: design` persona in `.add/personas/` covers
     backend/security design — the three that exist
     (accessibility-auditor, ui-designer, ux-researcher) are all
     UI-facing, while `appsec-engineer` and `backend-architect` are
     `flow: build, advisor`. Security design spans currently fall back
     to generic. Consider adding `flow: design` to `appsec-engineer`, or
     seeding a design-flow security-architect persona. (evidence: `grep
     -l "flow: design" .add/personas/*.md` at Ground SHA `9421827`
     returns only accessibility-auditor, ui-designer, ux-researcher;
     appsec-engineer frontmatter reads `flow: build, advisor`)

 SPEC DELTAS    293 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              frontdoor-persona-routing
════════════════════════════════════════════════════════════════════════