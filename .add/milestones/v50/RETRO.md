════════════════════════════════════════════════════════════════════════
 v50 · Production hardening — landing & admin UI
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     9/9 done           CRITERIA  7/7 met
 GATES     9 PASS             WAIVERS   none

 goal  every landing and admin/dashboard page stays usable, secure, and
       accessible when the backend is slow or failing — a
       production-grade frontend

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 resilient-bff-fetch         done      PASS 0     ●●●●●●●●●
 security-headers-csp        done      PASS 0     ●●●●●●●●●
 bff-input-validation        done      PASS 0     ●●●●●●●●●
 failure-state-segments      done      PASS 0     ●●●●●●●●●
 motion-primitives           done      PASS 0     ●●●●●●●●●
 a11y-ci-coverage            done      PASS 0     ●●●●●●●●●
 harden-marketing            done      PASS 0     ●●●●●●●●●
 harden-admin                done      PASS 0     ●●●●●●●●●
 harden-auth                 done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS (23 carried)
   • ADD · open · `_scope_walk` descended into `.claude/worktrees/*`
     (nested git worktrees from a PARALLEL program) and counted their
     uncommitted files as this task's touch → false `scope_violation` at
     the gate. FIXED by adding `.claude` to `_SCOPE_EXCLUDE_DIRS` (same
     regenerated/foreign-artifact class as `.next`/`node_modules`);
     required a re-cross (`phase tests`→advance→advance) to re-snapshot
     the baseline. Engine fix is LOCAL to this repo's
     `.add/tooling/add.py` — should be upstreamed (evidence: gate
     attempt 1 listed 14 `.claude/worktrees/dashboard/apps/gateway/...`
     files).
   • TDD · open · A module-global circuit-breaker poisons cross-test
     state — error-path tests trip it for later tests (saw "Service
     temporarily unavailable" in keys.test). FIX = reset per-test in the
     SHARED setup (mirrors msw/localStorage reset), and force instant
     retry backoff via env so error-path tests don't incur real latency
     that flakes `waitFor` under coverage (evidence: 2 keys tests failed
     pre-reset; intermittent coverage flake pre-backoff-env).
   • SDD · open · A single error class must live in the LOWEST layer and
     be re-exported upward (BffError defined in resilient-fetch.ts,
     re-exported by bff-client.ts) — defining it in the consumer would
     force a circular import and break `instanceof` across the app
     (evidence: refute-read confirmed instanceof holds).
   • ADD · open · An adversarial refute-read caught a real half-open
     concurrency gap (>1 parallel trial) that 12 green tests missed;
     closed by STRENGTHENING (single-probe `probing` sentinel + a 13th
     test), never by weakening — the refute-read is worth its cost on
     concurrency primitives (evidence: VERDICT EARNED after fix).
   • SDD · open · A static `next.config.ts` `headers()` is fully
     UNIT-testable by importing `nextConfig` and calling `headers()` (no
     running server needed for the red/green), while the RUNTIME
     emission is confirmed separately via a live `next start` + curl —
     the two together prove both the config shape AND that Next actually
     serves it (evidence: 4 unit tests + live curl on / and /login).
   • ADD · open · For a pure-static-config task with no
     logic/concurrency/secret, a full security-expert refute-read is
     overkill — the earned-green is proven by the running server
     emitting the exact contracted values; reserve the heavy refute-read
     for tasks with real logic (evidence: this gate auto-resolved on
     live evidence).
   • SDD · open · CSP relaxations must be recorded at the freeze as an
     auditable decision with a named upgrade path (here:
     `'unsafe-inline'`→nonce SPEC delta) — never a silent permanent
     allowance (evidence: §3 Least-sure flag + the SPEC delta above).
   • TDD · open · Special/control characters in a test STRING literal
     get normalized away by the editor (U+0085/NBSP silently became
     plain ASCII → a green-looking but vacuous assert); build them with
     `String.fromCharCode(0x85)` so the bytes survive (evidence:
     test_sanitize_domain_c1_and_non_ascii failed-then-fixed).
   • ADD · open · A security refute-read pays off on input-validation
     tasks even when tests are green — it found a contract-fidelity gap
     (C0-only vs the "no control chars" contract includes C1) that the
     happy tests missed; reserve it for logic/security tasks, skip it
     for pure static config (evidence: v50 task-2 skipped it, task-3
     caught a real gap).
   • SDD · open · When a task's drafted status code (422) collides with
     a shipped test (400), PRESERVE the shipped contract and reconcile
     the spec wording — never weaken the test for a cosmetic code
     (evidence: 400-preservation freeze flag honored).
   • UDD · open · Reusing the v13 `states.tsx` primitives made the
     failure segments a thin composition (one RouteError + thin
     wrappers) with no new visual language — the state-pattern
     investment pays off again (evidence: 7 files, ~all delegate to
     ErrorState/Loading).
   • ADD · open · A security-flagged task whose invariant is "render X,
     never render Y" is best verified by a sentinel-absent test PLUS a
     grep of the code paths — together they prove the negative more
     cheaply than a full subagent refute-read on a tiny surface
     (evidence: no-leak verified by 2 tests + grep showing only
     error.digest).
   • SDD · open · Next 16 special-file signature:
     error.tsx/global-error.tsx MUST be "use client" with `{error,
     reset}`; global-error renders its OWN html/body and can't use
     providers — keep it inline-styled/dependency-light (evidence: built
     + compiled into the route tree).
   • UDD · open · The a11y guarantee (reduced-motion) belongs in a
     GLOBAL css net (covers everything unconditionally), while the
     per-component primitive (Reveal) is the opt-in polish — separating
     "guarantee" from "enhancement" keeps the invariant robust even if a
     component forgets the motion-safe gate (evidence: M1 net
     independent of M2 Reveal).
   • TDD · open · `import.meta.url` is NOT a file:// URL under the
     jsdom/vitest transform — read repo files in tests via
     `resolve(process.cwd(), …)` instead (evidence:
     test_globals_has_reduced_motion_net threw "URL must be of scheme
     file" → fixed).
   • TDD · open · An a11y assertion helper must itself be proven to FAIL
     (render a known-bad node and assert it throws) — otherwise the
     surface "passes" could be vacuous; pair every "passes clean" with a
     "fails on real violation" test (evidence:
     test_helper_throws_on_serious anchors the 4 surface checks).
   • UDD · open · The never-axe'd auth forms + new failure segments
     passed serious/critical on the first check — the shared primitives
     (labeled Input, ErrorState role=alert) carry a11y by construction
     (evidence: 0 violations surfaced).
   • SDD · open · A shared `buildMetadata` helper + root-layout defaults
     gives consistent SEO with title-template inheritance — far better
     than per-page literal objects (no OG, drift); the title template
     (`%s · Hydroa`) means pages store just `"Pricing"` (evidence: 8
     pages unified).
   • TDD · open · Importing the ROOT layout in a test pulls
     `next/font/google` (`Inter`) which throws in jsdom —
     `vi.mock("next/font/google", ...)` per test that needs layout
     metadata (evidence: "Inter is not a function" → fixed).
   • UDD · open · Owning the route entrance ONCE in the shared shell
     (keyed by activePath) beats wrapping N pages — uniform motion, zero
     per-page churn, re-triggers on nav via React key remount (evidence:
     13 routes covered by one wrap).
   • TDD · open · A `data-slot` marker on a presentational primitive
     gives a clean, non-brittle test hook (vs matching Tailwind class
     strings) and doubles as a DS adoption marker (evidence: admin test
     asserts `[data-slot="reveal"]`, red before the wrap).
   • UDD · open · Sharing the entrance via AuthShell (one wrap) covers
     both auth pages with zero per-page churn — same shell-owns-motion
     pattern as the admin AppShell (evidence: 2 pages, 1 swap).
   • TDD · open · For "already-shipped" criteria (EC8), the verify net
     asserts the live surface (invalid-email inline error, in-flight
     disabled submit) rather than re-implementing — green-by-design
     tests still earn their keep as regression guards (evidence: 3 EC8
     tests green pre-change, lock the behavior).

 SPEC DELTAS    141 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v50
════════════════════════════════════════════════════════════════════════