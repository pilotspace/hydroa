"""RED evidence-legibility check for audit-coverage-structural-guard (R9) — S3 / M6.

Contract under test (audit-coverage-structural-guard TASK.md §RULES, DRAFT rev 2):
  M6 A row written by the retrofit is ACTOR-LEGIBLE where the evidence is CONSUMED:
     AuditEventItem and the scheduled-report projection carry the actor identity that was
     written (actor_key_id, plus actor_user_id/actor_scim_token_id for symmetry), so the admin
     audit list, GET /admin/audit/export and the compliance report each show WHO — never a row
     whose only actor field is a null actor_email. Additive nullable fields only.
  R:ANONYMOUS_EVIDENCE a retrofit row that reaches an evidence surface with no actor field
     populated at all — the record says what happened and not who, which is the control's
     whole point.
  R:ACTOR_FABRICATION ... including "fixing" M6 by writing a key name into actor_email, which
     ALSO poisons the export's exact-match actor filter (audit_repository.py:144).

WHY THIS IS RED AT THE CURRENT TREE — IT FAILS TWICE OVER, INDEPENDENTLY:

  (1) THE PROJECTION CANNOT CARRY AN ACTOR. `AuditEventItem` (usage/api/router.py:854) and
      `_audit_item` (compliance/application/report_schedule_generator.py:251) both expose
      exactly ONE actor field: `actor_email`. AuthzResult has no email. So every key-actor row
      the retrofit writes projects as `actor_email: null` with nothing else to say who acted —
      into the admin list, into the NDJSON archival feed at GET /admin/audit/export, into the
      compliance bundle (compliance/api/router.py:692) and into the scheduled report body.
      The SOC 2 evidence would record what happened and never who. This leg needs no audit row
      to exist and is red on the shape alone.

  (2) NO ROW EXISTS YET. conversations emits nothing, so the end-to-end leg finds no row to
      project in the first place.

  The two legs are asserted in that order deliberately. Leg 1 isolates the M6 defect, which
  survives even a perfect S2 retrofit: Build could write flawless key-actor rows and this
  evidence surface would still be anonymous. Leg 2 then proves the whole path end to end.

THE FIX IS ADDITIVE, AND THE WRONG FIX IS EXPLICITLY REFUSED. Build adds nullable
actor_key_id / actor_user_id / actor_scim_token_id to the projection envelope and populates
them at all four sites (A18). Build does NOT stuff a key id or key name into actor_email:
that is R:ACTOR_FABRICATION by another door, and because the export's actor filter is an
exact-match on actor_email, it would also silently corrupt every auditor query that filters
by actor. The final assertion here pins that.

DO NOT weaken this test to pass — that is Build's job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from gateway.audit.domain.audit_event import AuditEvent
from gateway.compliance.application import report_schedule_generator
from gateway.usage.api.router import AuditEventItem

from tests.audit_coverage.conftest import audit_ids, bearer, describe, new_audit_rows

pytestmark = pytest.mark.asyncio

# The actor fields that can carry a machine principal. `actor_email` is deliberately NOT in
# this set: it is the field that already exists and the one that is structurally null for
# every key-authenticated caller, which is the whole defect.
MACHINE_ACTOR_FIELDS = frozenset({"actor_key_id", "actor_user_id", "actor_scim_token_id"})

# Reached via getattr because Pyright strict refuses cross-module private access, and this is
# deliberately the PRIVATE projection helper — it is the exact function the scheduled report
# body is built from, so testing a public wrapper instead would test the wrong thing.
_audit_item = getattr(report_schedule_generator, "_audit_item")  # noqa: B009

# A19: a null actor_email on a key row means "key actor — look at actor_key_id", never
# "unknown". The export's actor filter stays exact-match on actor_email (deliberately
# untouched), so filtering by email still excludes key rows; that limitation is disclosed in
# the node, not silently patched here.


def _key_actor_event(tenant_id: uuid.UUID, key_id: uuid.UUID) -> AuditEvent:
    """A row shaped exactly as the S2 retrofit will write one on a key-authed /v1 surface."""
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_user_id=None,
        actor_email=None,
        action="conversation.create",
        target_type="conversation",
        target_id=str(uuid.uuid4()),
        result="success",
        actor_key_id=key_id,
        created_at=datetime.now(UTC),
    )


async def test_retrofit_row_is_actor_legible_in_evidence(
    client: Any, app: Any, tenant: dict[str, str]
) -> None:
    """covers: M6, A18, A19, A20, A21, A22, A23, E5, R:ANONYMOUS_EVIDENCE — drive one retrofit
    endpoint, then read the row back through the evidence projection (GET /admin/audit/export
    and the scheduled-report `_audit_item`) and assert an actor identity is present — not
    merely stored on the row.
    """
    tenant_uuid = uuid.UUID(tenant["tenant_id"])
    key_uuid = uuid.UUID(tenant["key_id"])

    # --- LEG 1 (A18, R:ANONYMOUS_EVIDENCE): the envelope must be able to carry an actor ----
    item_fields = set(AuditEventItem.model_fields)
    assert item_fields & MACHINE_ACTOR_FIELDS, (
        "ANONYMOUS_EVIDENCE: AuditEventItem (usage/api/router.py:854) exposes no machine-actor "
        f"field. It has {sorted(item_fields)} — the only actor field is `actor_email`, and "
        "AuthzResult has no email, so EVERY key-actor row the retrofit writes projects as "
        "`actor_email: null` with nothing else to say who acted. This envelope feeds the admin "
        "audit list, GET /admin/audit/export and the compliance bundle "
        "(compliance/api/router.py:692). Add nullable "
        f"{sorted(MACHINE_ACTOR_FIELDS)} and populate them at all four sites (A18) — do NOT "
        "write a key identifier into actor_email (R:ACTOR_FABRICATION)."
    )

    projected = _audit_item(_key_actor_event(tenant_uuid, key_uuid))
    assert set(projected) & MACHINE_ACTOR_FIELDS, (
        "ANONYMOUS_EVIDENCE: the scheduled-report projection `_audit_item` "
        "(compliance/application/report_schedule_generator.py:251) emits "
        f"{sorted(projected)} — no machine-actor field. A key-actor row reaches the scheduled "
        "compliance report body with `actor_email: null` and no other actor, i.e. anonymous."
    )
    assert any(projected.get(field) is not None for field in MACHINE_ACTOR_FIELDS), (
        "ANONYMOUS_EVIDENCE: `_audit_item` carries machine-actor field(s) but left them ALL "
        f"null for a row that HAS actor_key_id={key_uuid}: {projected}. The projection must "
        "copy the actor that was actually written."
    )
    assert projected.get("actor_email") is None, (
        "ACTOR_FABRICATION: `_audit_item` put a value in actor_email for a key-authenticated "
        f"row that has no user and no email: {projected['actor_email']!r}. The key is the "
        "actor — it belongs in actor_key_id. Writing it into actor_email also poisons the "
        "export's exact-match actor filter (audit_repository.py:144), so an auditor filtering "
        "by a real user's email would start matching machine rows."
    )

    # --- LEG 2 (E5): end-to-end — drive a retrofit endpoint, read it back through export ---
    before = await audit_ids(app)
    created = await client.post(
        "/v1/conversations", json={"title": "evidence legibility"}, headers=bearer(tenant["key"])
    )
    assert created.status_code == 201, f"conversation create failed: {created.text}"

    written = [
        row
        for row in await new_audit_rows(app, before)
        if row.action.startswith(("conversation.", "conversations."))
    ]
    assert len(written) == 1, (
        "no audit row was written for POST /v1/conversations, so there is nothing for the "
        "evidence surface to project. gateway.conversations.api makes no record_audit call "
        f"(S2 is not built yet).\n  rows written:\n{describe(written)}"
    )

    exported = await client.get(
        "/admin/audit/export",
        params={"format": "json", "limit": "100"},
        headers={"Authorization": f"Bearer {tenant['jwt']}"},
    )
    assert exported.status_code == 200, (
        f"GET /admin/audit/export failed: {exported.status_code} {exported.text}"
    )
    items = exported.json()["items"]
    matches = [
        item
        for item in items
        if str(item.get("action", "")).startswith(("conversation.", "conversations."))
    ]
    assert len(matches) == 1, (
        f"the conversation audit row did not reach GET /admin/audit/export — got "
        f"{[i.get('action') for i in items]}"
    )
    exported_row = matches[0]
    assert any(exported_row.get(field) is not None for field in MACHINE_ACTOR_FIELDS), (
        "ANONYMOUS_EVIDENCE (E5): the retrofit row reached the NDJSON/JSON export with NO "
        f"actor populated: {exported_row}. The export is the archival evidence feed an "
        "auditor reads; a row here that cannot say who acted is the control failing at "
        "exactly the point it exists to serve."
    )
    assert exported_row.get("actor_email") is None, (
        "ACTOR_FABRICATION: the exported key-actor row carries actor_email="
        f"{exported_row.get('actor_email')!r}. No user authenticated this call."
    )
