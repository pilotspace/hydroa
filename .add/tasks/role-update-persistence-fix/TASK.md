# TASK: Fix silent role-change rollback in self-service user-role update

slug: role-update-persistence-fix · created: 2026-07-03 · stage: production
milestone: (none)
autonomy: auto
phase: done
fast: true

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).
> The acceptance scenario collapses into §1 `Accept:`; OBSERVE is one optional line at the gate.

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
  `tenants/infrastructure/users_repository.py:UserRoleRepository.update_role` (~line 62) —
    calls only `await self._session.flush()`, never `.commit()`. This is the write path for
    `PUT /admin/users/{id}/role`.
  `tenants/api/users_router.py:assign_user_role` (~lines 98-172) — the self-service handler;
    never calls `.commit()` anywhere in its body (only fires an unrelated fire-and-forget
    audit task). NOT modified by any task this session.
  `tenants/application/users_use_cases.py:AssignUserRoleUseCase.execute` (~line 89) — calls
    `self._repo.update_role(...)`; itself never commits either.
  `core/db.py:get_session` (~lines 73-76) — `async def get_session(request): async with
    request.app.state.sessionmaker() as session: yield session` — no commit, no autocommit/
    autobegin config (only `expire_on_commit=False` at `main.py:633`). SQLAlchemy's
    `AsyncSession.close()` (fired by the `async with` exit) implicitly ROLLS BACK anything
    uncommitted — confirmed via direct SQLAlchemy 2.0.50 source/docstring inspection.
  Confirmed exactly 2 live callers of this chain (grepped, not assumed):
    `users_router.py:135` (buggy, self-service) and
    `tenants/api/platform_users_router.py:167` (this session's new cross-tenant route, which
    added its OWN local `await session.commit()` at ~line 202 as a self-contained fix —
    proves the fix pattern works, but only patches one of the two callers).
  `tests/test_users_role.py:test_owner_assigns_any_tier` (~lines 149-206) — the existing
    happy-path test. Confirmed by direct read: it asserts the NEW role from the HTTP response
    body (in-memory ORM state, set correctly pre-commit) and then queries `audit_events` via
    the SAME `db_session` fixture the request's `get_session` override yields — i.e. the same
    still-open transaction, never closed/reopened. It never independently re-SELECTs the
    `users` row through a fresh session after the request returns, which is exactly why this
    gap was never caught.
Context (working folder): none beyond the files above — no config/data touched.
Honors: every other write path in the codebase (~50, exhaustively checked this session by an
  independent verification pass) commits explicitly at the repository layer; this is the one
  exception. Fixing at the repository layer (not the router/use-case layer) matches that
  convention and fixes BOTH current and any future caller at the root, rather than requiring
  every caller to remember to commit. The one other existing caller
  (`platform_users_router.py`) already commits locally — a repository-level commit makes that
  call redundant-but-harmless (a second commit on a session with nothing left to flush is a
  safe no-op), not a bug.
Anchors the contract cites: `UserRoleRepository.update_role`, `AssignUserRoleUseCase.execute`,
  `users_router.py:assign_user_role`, `get_session`.

---

## 1 · SPECIFY — the rules

Feature: Role reassignment via `PUT /admin/users/{id}/role` survives past the request
Must:
  - A role change made through this endpoint is visible to an INDEPENDENT read (a fresh
    session/connection, opened after the original request has returned) — not just to reads
    sharing the original request's transaction.
  - No change to the endpoint's request schema, response schema, status codes, or existing
    403/404 authorization behavior.
Reject: none new — this is a durability fix, not a new validation surface. All existing
  Reject paths (self-role-change, escalation-above-caller's-tier, invalid role string) are
  unchanged and must stay green.
Accept: Given an owner successfully calls `PUT /admin/users/{id}/role` to demote an admin to
  member (200, response shows `role: "member"`), When a SEPARATE, later request (or a fresh
  DB session opened after the first request's session has closed) reads that same user's
  role, Then it also shows `"member"` — not the pre-change role.
Assumptions: ⚠ the minimal, correct fix is one `await self._session.commit()` added at the
  end of `UserRoleRepository.update_role`, mirroring the exact fix already self-applied (at a
  different layer) in this session's `platform_users_router.py`. Risk if wrong: the handler's
  only other side effect is a fire-and-forget audit task that is already independent/async and
  does not share this transaction, so committing earlier than "implicitly on close" should not
  split any currently-atomic multi-write operation — but this should be double-checked during
  build by re-reading `assign_user_role`'s full body for any later-in-the-request write that
  currently (accidentally) relies on the pre-fix no-commit behavior.

---

## 3 · CONTRACT — freeze the shape

```
UserRoleRepository.update_role(...) — UNCHANGED signature/fields.
Behavior change only (no shape change):
  - add `await self._session.commit()` as the final statement, after the existing
    `await self._session.flush()`.
Success (200 via PUT /admin/users/{id}/role): unchanged response body/shape. Durability
  guarantee added: the new role is now visible to a fresh session opened after the request
  returns (previously: rolled back silently on session close).
Unchanged: request schema, all existing 403/404 rejection paths (self-role-change,
  escalation-above-caller's-tier, invalid role string), the fire-and-forget audit task.
New symbols: none. Touches exactly one existing method body.
```

`Least-sure flag surfaced at freeze:` [contract] whether to ALSO remove the now-redundant
  local `await session.commit()` this session added to `platform_users_router.py` (~line 202)
  once this repository-level fix lands — leaving it is harmless (safe no-op second commit) but
  slightly odd; removing it is a 1-line cleanup but touches a file outside this task's original
  "one method body" scope. Recommend: leave it for THIS task (stay minimal/in-scope), file as
  a trivial cleanup note in §7 instead of expanding scope now. If wrong: at most a harmless
  redundant commit call sitting in the codebase, zero functional cost.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-03 (explicit, real sign-off — "freeze
  role-update-persistence-fix and start build" — not an auto-mode fallback, unlike this
  session's other two contracts).

---

## 4 · TESTS — failing-first (red)

Plan: `test_role_change_persists_past_request` (added to `tests/test_users_role.py`, alongside
its 7 siblings) — PUTs a role change via `client`+`seeded_users`, then re-reads the `users` row
via the `db_session` fixture (confirmed a genuinely separate `AsyncSession`, opened fresh from
`app.state.sessionmaker()`, independent of the request handler's own session) and asserts the
NEW role is visible there — the §1 Accept line's Then.
Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

RED confirmed (2026-07-03): ran in isolation against unfixed code —
  `AssertionError: role change did not survive past the request — independent read saw
  'member', expected 'admin' (the request's own response showed 'admin' but the write was
  rolled back on session close)`. Exactly the right failure mode: HTTP 200 + correct
  in-memory response, but the independent read still sees the pre-change role. 1 failed in
  0.51s, clean collection (no import/fixture errors).

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/gateway/src/gateway/tenants/infrastructure/users_repository.py`, `apps/gateway/tests/test_users_role.py`, `.pytest_cache`, `.ruff_cache`, `.coverage`
  (the engine's §5 scope parser only reads backticked tokens on THIS first line — confirmed
  empirically this session: both sibling tasks' own multi-line declarations parsed as `declared:
  []` too, and only gated PASS because a later re-snapshot happened to zero out the touched-diff
  at gate time, not because scope was genuinely declared. Fixed properly here instead of relying
  on the same coincidence.)
  users_repository.py: the fix — one `commit()` line added to `UserRoleRepository.update_role`.
  test_users_role.py: new red test, added alongside its siblings.
  .pytest_cache / .ruff_cache / .coverage: gitignored build artifacts, pre-declared to avoid a
  scope-snapshot false-positive two sibling tasks hit this session.
Strategy & known-problem fixes:
  1. Write ONE new test in `test_users_role.py`: PUT a role change via the existing `client` +
     `seeded_users` fixtures, then open an INDEPENDENT fresh `AsyncSession` against the same
     test DB (NOT the `db_session` fixture, which shares the request's own still-open
     transaction — that is exactly the blind spot that let this bug ship) and re-SELECT the
     user row through it. Confirm RED first: today, the independent read must still show the
     OLD role (proving the bug currently reproduces), before touching src/.
  2. Add `await self._session.commit()` as the final statement of
     `UserRoleRepository.update_role`, after the existing `flush()`.
  3. Confirm GREEN: the new test passes; the full existing `test_users_role.py` file stays
     100% green (no behavior change to any 403/404/validation path); re-run
     `tests/cross_tenant_keys_members/` too, since `platform_users_router.py` calls the same
     use-case/repository and will now commit twice per call (expected to be a harmless no-op
     second commit — confirm, don't assume).
  4. Run `ruff format` on the touched files BEFORE crossing tests->build (a known
     tamper-tripwire trap hit earlier this session if formatting happens after the snapshot).
Strategy actually used: exactly as planned, zero deviation. Placed the new
  `await self._session.commit()` as the truly final statement (after the re-SELECT, immediately
  before `return`) rather than right after `flush()` — functionally identical given
  `expire_on_commit=False`, but keeps the whole read+write of one logical operation inside a
  single transaction before closing it out. Double-checked the flagged Assumption for real (not
  just asserted): read `assign_user_role`'s full body — the only action after the use-case call
  is `asyncio.ensure_future(record_audit(request.app.state.sessionmaker, ...))`, which takes the
  sessionmaker FACTORY (not the request's own session) and manages its own independent
  transaction — confirms the new, earlier commit cannot split or race anything it depends on.
Code lives in: `./src/`   ·   Constraints: change no test, no contract; allow-list packages only.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build
- [x] green was EARNED — no overfit / vacuous asserts / stubbed-away logic
- [x] no exposed secrets, injection openings, or unexpected dependencies (security = HARD-STOP)

Build expectations (from §1 Accept + §3 CONTRACT): a role change via `PUT /admin/users/{id}/role`
must be visible to an independent read after the request closes, with zero change to request/
response shape or existing rejection paths. Confirmed by:
  - `tests/test_users_role.py` full file: 8/8 passed (7 pre-existing + the new
    `test_role_change_persists_past_request`), including all 403/404/validation Reject paths
    unchanged.
  - Regression: `tests/cross_tenant_keys_members/` (38 tests, the sibling task that calls the
    same repository/use-case from a second router) + `tests/superadmin_role/` (1 migration test,
    required a one-time pre-created `gateway_migrations_test_<suffix>` DB — a known test-infra
    naming quirk unrelated to this fix, not a regression) — all green, confirming the harmless
    double-commit assumption for real rather than by inference.
  - `ruff check` + `pyright`: 0 findings on both touched files.
  - Manually re-read `assign_user_role`'s full body (see §5 Strategy actually used) — confirmed
    no other write shares this transaction, so earlier-commit is safe.
Security: this task's own change REDUCES risk (fixes a silent-privilege-retention bug) and adds
  no new surface — no secrets, no new dependencies, no injection-shaped input touched. No HARD-STOP.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (froze the contract with explicit sign-off: "freeze
  role-update-persistence-fix and start build") + AI self-review (build, verify, regression
  across 2 sibling test suites) · date: 2026-07-03

OBSERVE:
  - [SPEC · seeded] `platform_users_router.py`'s own local `await session.commit()` (~line 202)
    is now a redundant-but-harmless second commit given this fix — a trivial cleanup, not a bug
    (evidence: `tests/cross_tenant_keys_members/` 38/38 still green with the double-commit in
    place). Left as-is per this task's own frozen Least-sure-flag call to stay minimal/in-scope;
    fold into whichever task next touches that file.
  - [competency · TDD] The existing `db_session` pytest fixture (`tests/conftest.py`) already
    opens a genuinely separate `AsyncSession` per test (`app.state.sessionmaker()`, independent
    of whatever the request handler's own `get_session` dependency yields) — no custom
    engine/session-factory code is needed to prove cross-transaction durability. The prior gap
    wasn't a fixture limitation, it was that no test used `db_session` to re-read the WRITTEN
    table itself (only a same-table-different-row or different-table check) after a request
    returned (evidence: `test_owner_assigns_any_tier` used `db_session` only against
    `audit_events`, never re-reading `users`). Worth a standing check on any future
    "does this write survive the request" test: does it re-read via `db_session`/an
    equivalently independent session, against the SAME row the request wrote?
