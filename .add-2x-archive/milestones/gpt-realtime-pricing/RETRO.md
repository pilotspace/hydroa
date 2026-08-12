════════════════════════════════════════════════════════════════════════
 gpt-realtime-pricing · GPT-Realtime cache-discount pricing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  2/2 met
 GATES     3 PASS             WAIVERS   none

 goal  A GPT-Realtime call through the proxy produces an accurate,
       billed usage_records row reflecting OpenAI's real dual-stream
       pricing (text $4/$16/$0.40-cached, audio $32/$64/$0.40-cached per
       1M), and both GET /v1/models and GET /admin/catalog/models list
       GPT-Realtime with all 6 real prices
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 gpt-realtime-pricing-fields done      PASS 0     ●●●●●●●●●
 gpt-realtime-relay-billing  done      PASS 3†    ●●●●●●●●●
 gpt-realtime-schema-migrat… done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   gpt-realtime-pricing-fi… PASS Tin Dang <tindang.ht97@gmail.com>
   gpt-realtime-relay-bill… PASS Tin Dang <tindang.ht97@gmail.com>
   gpt-realtime-schema-mig… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS (4 carried)
   • ADD · open · The GROUND phase's initial research missed that the
     relay's actual default model id (`gpt-4o-realtime-preview`)
     differed from the milestone's assumed pricing target
     (`gpt-realtime`) — caught only by live WebFetch pricing research,
     not by reading code alone (evidence: required a user decision via
     AskUserQuestion mid-GROUND).
   • ADD · open · A single additive field access in `_insert_snapshot`
     (`repository.py`) broke 3 sibling suites' independently-duck-typed
     `FakeCatalogModel` fixtures plus one exact-id-list wiring assertion
     — none of these were in this task's originally declared scope, only
     surfaced by running the FULL regression suite, not the targeted one
     (evidence: GRPF7 caught what the targeted 7-test run could not).
     Reinforces: always run the full suite before VERIFY on any change
     touching a shared domain entity, even when the task's own tests are
     green.
   • ADD · open · GROUND-phase research (both this task's own §0 and an
     earlier GROUND-phase subagent for the parent milestone) wrongly
     concluded "no Alembic/formal migration tooling exists in this repo"
     — a repo-wide search missed `apps/gateway/alembic.ini` +
     `apps/gateway/migrations/versions/` (35 prior migrations) entirely,
     and this false premise was baked into the FROZEN, human-approved §3
     CONTRACT text before being caught by the very GSM4 regression run
     the contract itself required. Future GROUND-phase research on
     schema-touching tasks MUST explicitly check for `alembic.ini` (via
     `find <app-root> -iname alembic.ini`) before asserting "no
     migration tool exists" — a directory-listing/grep miss is not
     equivalent to a confirmed absence (evidence: 2 full-suite failures
     — test_upgrade_from_empty_parity, test_autogenerate_empty_diff —
     caught the gap; fixed via a4c6e8b0d2f3, no contract/behavior change
     needed).
   • ADD · open · the shared test Postgres
     (`localhost:5433/gateway_test`) has no isolation between concurrent
     worktree pytest sessions — a sibling worktree's own full-suite run
     can orphan a table (`tenant_model_presets`) mid-run and cascade a
     single `DROP TABLE` FK failure into hundreds of unrelated test
     failures for the REST of that pytest session. This is the third
     time this exact signature has been hit this session alone
     (previously: catalog-pricing-fields's build_tampered remediation;
     now twice more here). Worth a real fix (e.g. per-worktree test DB
     names, like tests/migrations/conftest.py already does with its
     dedicated `gateway_migrations_test` DB) rather than continuing to
     work around it ad hoc — evidence: 2 of 4 full-suite attempts this
     task alone were disrupted by it (78 failed + 833 errors each time,
     100% traced to the identical DependentObjectsStillExistError root
     cause, 0% overlap with any file this task touched).

 SPEC DELTAS    222 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              gpt-realtime-pricing
════════════════════════════════════════════════════════════════════════