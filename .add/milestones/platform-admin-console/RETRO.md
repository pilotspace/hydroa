════════════════════════════════════════════════════════════════════════
 platform-admin-console · Platform Admin Console
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  5/5 met
 GATES     5 PASS             WAIVERS   none

 goal  A superadmin can view and fully manage any tenant (config,
       budget, keys, members) through a dedicated, fully audited
       cross-tenant admin surface
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 platform-tenant-directory   done      PASS 0     ●●●●●●●●●
 cross-tenant-config-budget  done      PASS 0     ●●●●●●●●●
 cross-tenant-keys-members   done      PASS 0     ●●●●●●●●●
 admin-console-audit         done      PASS 0     ●●●●●●●●●
 admin-console-ui            done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   platform-tenant-directo… PASS Tin Dang <tindang.ht97@gmail.com>
   cross-tenant-config-bud… PASS Tin Dang <tindang.ht97@gmail.com>
   cross-tenant-keys-membe… PASS Tin Dang <tindang.ht97@gmail.com>
   admin-console-audit      PASS Tin Dang <tindang.ht97@gmail.com>
   admin-console-ui         PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (15 carried)
   • ADD · folded · a test file reformatted by `ruff format` AFTER its
     tests->build tamper-tripwire [folded foundation-version 45]
     snapshot (taken at RED-confirmation) diverges from that snapshot's
     md5 and trips `build_tampered` at gate time, even though the change
     is whitespace-only and the test was never weakened. Fix is cheap
     (re-cross tests->build via `add.py phase build` to force an
     unconditional re-snapshot, per `_build_entry`'s own documented
     "legit change-request... re-snapshots cleanly" design) but costs a
     heal-attempt-shaped scare if not recognized immediately (evidence:
     hit verbatim this task, burned 0 of the 3-attempt cap since it was
     diagnosed before retrying blind). Worth a lint-format pass BEFORE
     the tests->build crossing, not after, to avoid this entirely on
     future tasks.
   • SDD · folded · `mcp__serena__find_referencing_symbols` (and direct
     `find_symbol` lookups) gave [folded foundation-version 45]
     false-negative/stale results for symbols in files written earlier
     in the SAME session (`list_tenants`, `get_tenant_by_id`,
     `platform_tenants_router` — all real, all live, all found correctly
     via `search_for_pattern`/grep instead). Serena's symbol index
     appears to lag behind very recently written/edited files within a
     session (evidence: this task's WIRING deep-check in §6 required the
     grep fallback for every new symbol). Don't trust a symbol-graph
     zero-result at face value for fresh code — cross-check with a raw
     pattern search first.
   • TDD · folded · a full-suite failure should never be accepted as
     "pre-existing/unrelated" from [folded foundation-version 45]
     reading a stack trace alone when the changed diff is small enough
     to stash — `git stash` the task's changed paths, re-run the failing
     test in isolation 2-3x on the CLEAN base, and only trust the
     "unrelated" conclusion if it reproduces the SAME way without any of
     the task's code present (evidence: this task's response_caching
     failure looked plausibly related at first glance — NOGROUP on a
     Redis stream, and this task added a new router+dependency to the
     same `app` fixture every test shares — but the A/B stash comparison
     proved it flakes identically pass/fail/fail with zero lines of this
     task's code in the tree, a pre-existing startup race in the
     flusher's lazy `xgroup_create` already partially documented in
     tests/conftest.py's own comments).
   • ADD · folded · the tests→build §5 scope-snapshot is taken ONCE,
     unconditionally, at the crossing [folded foundation-version 45]
     (`_build_entry`, shared verbatim by `cmd_advance` and the `add.py
     phase build <slug>` admin override) — and `add.py`'s scope-walk
     exclusion list (`_SCOPE_EXCLUDE_DIRS`) prunes JS/TS build artifacts
     (`.next`/`coverage`/`test-results`) but NOT Python ones
     (`.pytest_cache`/ `.ruff_cache`/`.coverage`). Any manual
     pytest/ruff verification run AFTER the snapshot but BEFORE `add.py
     gate` regenerates those Python artifact dirs, which then shows up
     as `scope_violation` at gate time — even though `cmd_gate` itself
     is no-exec and never ran them. The fix is mechanical once
     diagnosed: declare the 3 artifact paths in §5 Scope AND re-run
     `add.py phase build <slug>` immediately before gating (forces a
     fresh snapshot reflecting the now-clean tree), with zero test/lint
     commands in between the re-snapshot and the gate call (evidence:
     this task hit `scope_violation` twice — attempt 1 of 3 citing 9
     files, attempt 2 of 3 citing 34 freshly-regenerated `.pytest_cache`
     files — before the engine source read located the root cause; a
     `.gitignore`d path is NOT automatically scope-walk-exempt for
     Python projects today). Worth adding
     `.pytest_cache`/`.ruff_cache`/`.coverage` to `_SCOPE_EXCLUDE_DIRS`/
     `_SCOPE_EXCLUDE_FILES` directly in the engine, the same way
     `.next`/`coverage` already are for JS/TS, rather than requiring
     every Python task to widen its own §5 Scope by hand.
   • TDD · folded · the "never accept pre-existing/unrelated from a
     stack trace alone" regression- [folded foundation-version 45]
     triage discipline (first recorded by platform-tenant-directory this
     same session) reproduced its value on a SECOND, independent,
     differently-shaped failure set: 9 failures + 1 error across 6
     self-service test directories, none reproducible via git-stash
     (this task's new files were untracked, not a diff on tracked files)
     — so the isolation method itself had to adapt to a physical
     file-move A/B (new router + new test dir moved to a scratchpad dir,
     identical command re-run on the now-clean tree) rather than `git
     stash`. Confirmed 2 independently-verifiable pre-existing defects
     (a deterministic `test_guardrails_core_migration_column_exists`
     failure matching a defect already documented in the precedent
     task's own §6 VERIFY; a non-deterministic Redis ledger-timing flake
     matching `tests/conftest.py`'s own documented `NOGROUP` caveat) —
     neither count nor identity of failures changed across 3 repeated
     runs regardless of this task's code being present or physically
     absent (evidence: 3 full A/B cycles, each re-confirming the same 2
     pre-existing categories and re-confirming this task's own 20/20
     tests green throughout). Worth generalizing the isolation recipe in
     the `add` skill's docs beyond `git stash` to also name the
     untracked-files file-move variant explicitly.
   • SDD · folded ·
     `mcp__serena__find_referencing_symbols`/`find_symbol` missed a
     genuinely live [folded foundation-version 45] cross-file usage this
     task depended on — a directory-scoped `conftest.py` overriding the
     parent `tests/conftest.py`'s `app` fixture by name, then importing
     and registering `platform_tenant_config_router` onto it —
     reproducing the exact "stale for same-session-written files"
     symptom platform-tenant-directory's own SDD delta already named,
     but for a NEW code shape (pytest fixture-override-by-name, not a
     plain function/class reference). Cross-checked via
     `mcp__serena__search_for_pattern` + a live route-table inspection
     (8 distinct, non-colliding routes confirmed) before trusting the
     build was correctly wired (evidence: this task's §6 WIRING
     deep-check documents the same-zero-result-then-grep-fallback
     sequence explicitly).
   • TDD · folded · A genuinely adversarial test — an INDEPENDENT second
     DB read via a separate [folded foundation-version 45] session, not
     a re-read of the same request's own response body — caught a real,
     previously-invisible production defect that a mature,
     already-shipped feature's own test suite missed entirely (evidence:
     `test_superadmin_reassigns_target_tenant_member_role`'s
     `db_session` read exposed `UserRoleRepository.update_role`'s
     missing commit; `test_users_role.py`'s
     `test_owner_assigns_any_tier` only ever reads the SAME session's
     response body across 3 sequential PUTs, or a DIFFERENT,
     separately-committed table (`audit_events`) — never an independent
     re-read of the mutated `users` row itself, and its 3-sequential-PUT
     design happens to pass regardless of cross-request commit behavior
     since each UPDATE is keyed on id+tenant_id, not on the row's
     current role). Lesson: "assert on the API response" and "assert on
     a persisted read via a different session" are not equivalent claims
     — the second is strictly stronger and belongs in any test whose
     Reject/Must line is phrased in terms of a stored outcome, not just
     a returned one.
   • ADD · folded · Re-crossing tests->build via `add.py phase build
     <slug>` after a legitimate [folded foundation-version 45]
     post-crossing test-file edit (here: adding a test-module-local
     `app` fixture override, needed to register new routers for genuine
     end-to-end verification without touching `main.py`) cleanly
     re-snapshots the tamper tripwire with zero friction when the
     contract is already frozen and the flag already verified —
     confirmed a SECOND time this session (first by a sibling task, now
     by this one) (evidence: `add.py phase build
     cross-tenant-keys-members` ran clean immediately after the fixture
     addition + the `session.commit()` fix, no `_die` triggered,
     `state["tasks"][slug]["tripwire"]` unconditionally overwritten per
     `_build_entry`). This is a safe, sanctioned, repeatable recovery
     path for "I need to touch a test file again after crossing into
     build" — not a one-off fluke specific to the first task that hit
     it.
   • DDD · folded · A "reuse existing use-case/repository verbatim" task
     can still surface a genuine, [folded foundation-version 45]
     previously-latent defect IN the reused code, discovered purely by
     writing a MORE rigorous test than the original feature ever had —
     "reuse-over-invent" bounds this task's OWN new logic to zero, it
     does not imply the reused code was already fully correct (evidence:
     `UserRoleRepository.update_role`'s missing `commit()`, see Spec
     delta above). Standing habit worth carrying forward: when reusing a
     mutation-performing repository method verbatim, explicitly check
     whether it (or something in its caller chain) actually commits —
     not just whether it returns the right in-memory value or whether
     the existing tests for it are green.
   • ADD · folded · A legitimate mid-build Scope expansion (to fix a
     SIBLING task's own now- [folded foundation-version 45] invalidated
     test assertion) is distinguishable from "weakening a test to force
     my own build to pass" by asking whose contract the change serves
     (evidence: the 2 sibling-test edits here served THIS task's OWN
     frozen §3 contract's mandated new behavior; THIS task's own §4
     frozen tests and §3 contract were never touched).
   • TDD · folded · Line-coverage percentage is an unreliable
     correctness signal for async [folded foundation-version 45]
     SQLAlchemy-session handler code in this repo (greenlet-related
     trace under-attribution); a per-call-site behavioral assertion
     (real request -> independent DB re-query -> exact-field assertion)
     is a strictly stronger green-ness signal than a coverage percentage
     for this code shape (evidence: reproducible `coverage json` dump on
     an isolated, passing test showing its own necessarily-executed
     lines marked "missing").
   • ADD · folded · An independent adversarial subagent review
     (separately primed, no access to the [folded foundation-version 45]
     builder's own narrative) is worth spawning even when self-review
     already feels thorough, for a 15-call-site,
     cross-tenant-data-handling change — the highest-consequence bug
     class here (tenant_id attribution swap) is exactly the kind of
     subtle, easy-to-miss-in-self-review defect that benefits from a
     second, differently-primed reader (evidence: the subagent
     independently re-derived the same "zero `identity.tenant_id` in
     executable code" finding via its own separate codebase-wide search,
     rather than trusting the builder's narrative — a converging, not
     merely repeated, confirmation).
   • TDD · folded · A `waitFor` predicate can resolve on a transient
     intermediate state rather than [folded foundation-version 48] the
     intended final state when the assertion (e.g. "X is absent") is
     ALSO true during a loading/ transition frame, not just at the
     desired end state — the very next synchronous assertion then fails
     in a way that looks like the earlier `waitFor` "hung," when it
     actually resolved too early. Fix pattern: fold both the negative
     and positive condition into the SAME `waitFor` callback so it only
     resolves once the true end state holds (evidence: the
     `test_directory_search_filters_and_row_links_to_detail` debounce
     investigation this session — cost roughly half a session of
     bisection before the actual mechanism was found via a DOM- rendered
     debug log, not console.log, since this environment's test runner
     does not surface it).
   • TDD · folded · A fully-green suite only proves the paths it
     actually exercises — an independent [folded foundation-version 48]
     adversarial review (subagent refute-read) found a real, uncovered
     gap (Rotate/Revoke/assignRole mutations had no `onError` handler,
     silently swallowing a failure — a direct R4 violation) that none of
     the 34 new tests caught, because no test exercised any mutation's
     FAILURE path on this surface (only Create's failure path was
     covered, inherited for free from the reused `CreateKeyDialog.tsx`).
     Candidate standing checklist item for future test-plans: one
     failure-path test per mutation, not just per screen (evidence:
     `a885e980fd730c83e`'s review, fixed same session, re-verified 17/17
     + clean lint/typecheck + clean 944-test regression).
   • ADD · folded · Live mutation-probing (temporarily inverting one
     meaningful line of production [folded foundation-version 48] logic,
     confirming the relevant test fails, then reverting
     byte-identically) is a materially stronger refute-read technique
     than a read-only review — it converted "the assertions look
     reasonable" into a demonstrated fact for R6, the budget merge
     logic, and the members self-guard. Worth naming explicitly as a
     preferred refute-read technique in `advisor.md`/`confidence.md` for
     safety-critical tasks, not just an ad hoc choice this one reviewer
     happened to make (evidence: this task's Refute-read verdict — all 3
     probes correctly flipped their target test from green to red, then
     back).

 SPEC DELTAS    276 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              platform-admin-console
════════════════════════════════════════════════════════════════════════