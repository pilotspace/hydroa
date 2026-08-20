"""RED fail-open check for audit-coverage-structural-guard (R9).

Contract under test (audit-coverage-structural-guard TASK.md §RULES, DRAFT):
  M4 Audit emission never changes the endpoint's outcome: with the audit writer BROKEN
     (raising), the mutation still succeeds and returns its normal response — record_audit's
     fail-open contract (audit/application/audit_writer.py:43), now pinned by a check.
  M5 Nothing pre-existing weakens — already-audited modules keep their calls.
  A5 The event is written AFTER the mutating commit succeeds, awaited INLINE, so the row is
     readable immediately with no polling ([[fire-and-forget-audit-test-flake]]).
  A6 Fail-open, not fail-closed: a broken audit path is never the caller's problem.
  E2 A mutating endpoint whose audit write raises still returns its normal success response.
  R:AUDIT_BLOCKS_REQUEST audit failure surfacing as a 4xx/5xx is the inverted contract.

WHY THIS TEST IS RED AT THE CURRENT TREE — READ THIS BEFORE "FIXING" IT:
  The BROKEN-WRITER leg is green today for a degenerate reason: gateway/conversations/ makes
  no record_audit call at all, so there is no writer to break and the endpoint trivially
  succeeds. A check that only asserted that leg would be VACUOUSLY green and would stay green
  if Build never wired the audit call — exactly the masked-gate failure mode
  ([[masked-gate-never-reached-a-verdict]]).

  So the red is deliberately anchored on the HEALTHY leg, asserted FIRST: with the writer
  intact, the mutation must leave an audit row. It does not today, and that is this test's
  failure. Only once Build wires the emission does the broken-writer leg start carrying real
  weight — and it then proves the fail-open direction that M4/E2 are actually about.

  This is the upload-bounds M2 disclosure shape: a leg that cannot be made red at authoring
  time is DISCLOSED here rather than quietly counted as coverage.

DO NOT weaken this test to pass — that is Build's job.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository

from tests.audit_coverage.conftest import (
    bearer,
    assert_key_actor,
    audit_ids,
    describe,
    new_audit_rows,
)

pytestmark = pytest.mark.asyncio

# The representative mutating endpoint from the retrofit set. Conversations is chosen because
# it needs no provider stub, no catalog model and no uploaded file — so nothing in this test
# can fail for a reason unrelated to auditing.
# Both spellings the contract offers — M2 says `<module>.<verb>` with the module named
# plurally in `scope:`, A8's examples are singular. Accept either (see _prefixes in
# test_five_modules_audited.py for the same reasoning).
PREFIXES = ("conversation.", "conversations.")


def _is_conversation_event(action: str) -> bool:
    return action.startswith(PREFIXES)


async def _explode(self: AuditRepository, event: AuditEvent) -> None:
    """Stand-in for AuditRepository.record that always raises.

    This is the RIGHT seam to break. Patching app.state.sessionmaker would break the
    ENDPOINT's own transaction too, and the test would then prove nothing about audit
    fail-open — it would prove the endpoint dies when the database dies. record_audit opens
    its own session and swallows every exception (audit_writer.py:48), so making the INNER
    repository call raise exercises exactly the fail-open path and nothing else.
    """
    raise RuntimeError("injected audit writer failure (audit-coverage-structural-guard M4)")


async def test_audit_failure_never_blocks_mutation(
    client: Any,
    app: Any,
    tenant: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """covers: M4, M5, A5, A6, E2, R:AUDIT_BLOCKS_REQUEST — with the audit writer's session
    factory raising, a representative mutating endpoint from the retrofit set still succeeds
    with its normal response and zero audit rows; the same suite run proves already-audited
    modules' tests untouched (M5 rides the full-suite receipt).
    """
    key = tenant["key"]

    # --- M5 anchor: an ALREADY-audited mutation still writes its row -------------------
    # Not the red. It exists so that a change which silently disables auditing everywhere
    # (a broken record_audit, an unwired writer) cannot leave this file green.
    before_anchor = await audit_ids(app)
    anchor = await client.post(
        "/admin/keys",
        json={"name": "audit-cov-anchor"},
        headers={"Authorization": f"Bearer {tenant['jwt']}"},
    )
    assert anchor.status_code == 201, f"anchor key create failed: {anchor.text}"
    anchor_rows = await new_audit_rows(app, before_anchor)
    assert anchor_rows, (
        "M5 anchor: the already-audited keys module wrote NO audit row for POST /admin/keys. "
        "Auditing is broken globally — every other assertion in this suite is meaningless "
        "until that is fixed. This anchor is not the red; do not delete it."
    )

    # --- A5 / the RED: with the writer HEALTHY, the mutation leaves an audit row -------
    before_healthy = await audit_ids(app)
    healthy = await client.post(
        "/v1/conversations", json={"title": "fail-open healthy"}, headers=bearer(key)
    )
    assert healthy.status_code == 201, f"conversation create failed: {healthy.text}"
    healthy_body = healthy.json()
    healthy_id = healthy_body["id"]

    healthy_rows = [
        row
        for row in await new_audit_rows(app, before_healthy)
        if _is_conversation_event(row.action)
    ]
    assert len(healthy_rows) == 1, (
        "HEALTHY case: POST /v1/conversations must leave exactly one "
        f"`conversation(s).*` audit row, found {len(healthy_rows)}. gateway/conversations/ makes no "
        "record_audit call, so the audit trail for this mutation is empty — and the "
        "broken-writer leg below cannot mean anything until it is not.\n"
        f"  rows written:\n{describe(healthy_rows)}"
    )
    assert_key_actor(
        healthy_rows[0],
        tenant_id=tenant["tenant_id"],
        key_id=tenant["key_id"],
        label="fail-open healthy leg",
    )
    # A5: read immediately, no polling — the write is awaited inline after the commit.

    # --- M4 / E2 / A6 / R:AUDIT_BLOCKS_REQUEST: break the writer, outcome unchanged ----
    monkeypatch.setattr(AuditRepository, "record", _explode)
    before_broken = await audit_ids(app)
    broken = await client.post(
        "/v1/conversations", json={"title": "fail-open broken"}, headers=bearer(key)
    )
    assert broken.status_code == 201, (
        "R:AUDIT_BLOCKS_REQUEST: the audit write raised and the mutation returned "
        f"{broken.status_code}. record_audit is FAIL-OPEN (audit_writer.py:48) — a broken "
        f"audit path must never become the caller's error.\n  body: {broken.text}"
    )
    broken_body = broken.json()
    assert set(broken_body) == set(healthy_body), (
        "A13/E2: the success response shape changed when the audit writer broke — the "
        f"response must be byte-identical in shape. healthy={sorted(healthy_body)} "
        f"broken={sorted(broken_body)}"
    )
    assert broken_body["title"] == "fail-open broken"
    assert broken_body["id"] != healthy_id, "a second create must return a distinct id"

    broken_rows = [
        row
        for row in await new_audit_rows(app, before_broken)
        if _is_conversation_event(row.action)
    ]
    assert not broken_rows, (
        "the audit writer was raising, so NO audit row may have been persisted — a row here "
        f"means the failure injection missed its seam:\n{describe(broken_rows)}"
    )

    # The mutation itself must be durable regardless of the audit outcome.
    listed = await client.get("/v1/conversations", headers=bearer(key))
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()["data"]}
    assert broken_body["id"] in ids, (
        "the mutation was rolled back when the audit write failed — audit runs in its OWN "
        "session precisely so it cannot do that (audit_writer.py:36)"
    )

    # --- SECOND SEAM: the failure that record_audit's try block does NOT cover ---------
    # record_audit swallows everything INSIDE itself, but the AuditEvent is constructed at the
    # CALL SITE, before record_audit is entered — so a raising __post_init__ (the
    # audit_missing_actor invariant at audit/domain/audit_event.py:57) escapes into the
    # handler and 500s the mutation. That is the same R:AUDIT_BLOCKS_REQUEST failure the first
    # seam tests, reached by the path a real retrofit is most likely to take: a route whose
    # authenticated principal is not an API key supplies no actor, the invariant fires, and
    # the endpoint dies. Pinning it here means Build must construct the event inside the
    # try — or supply a valid actor — rather than trusting record_audit to catch it.
    def _reject_every_event(self: AuditEvent) -> None:
        raise ValueError("audit_missing_actor (injected — audit-coverage-structural-guard M4)")

    monkeypatch.setattr(AuditEvent, "__post_init__", _reject_every_event)
    invariant_broken = await client.post(
        "/v1/conversations", json={"title": "fail-open invariant"}, headers=bearer(key)
    )
    assert invariant_broken.status_code == 201, (
        "R:AUDIT_BLOCKS_REQUEST: constructing the AuditEvent raised and the mutation returned "
        f"{invariant_broken.status_code}. record_audit's try block starts AFTER the event is "
        "built (audit_writer.py:43), so building it outside the try leaves the caller exposed "
        "to the audit_missing_actor invariant. Build the event inside the guarded region, or "
        "guarantee a valid actor on every route that emits.\n"
        f"  body: {invariant_broken.text}"
    )
