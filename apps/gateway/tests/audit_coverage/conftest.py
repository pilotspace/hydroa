"""Shared fixtures for the audit-coverage-structural-guard behavioral checks.

Mirrors the five modules' own suites rather than inventing fixtures: the `_signup_and_key`
recipe is byte-for-byte the one in tests/conversations/test_conversations.py:40,
tests/memory/test_memory.py:66, tests/vector_store_core/test_vector_store_core.py:50 and
tests/finetune_broker/test_finetune_broker.py:53.

The audit-row readers below are the only new machinery. They snapshot audit_events BEFORE a
mutation and diff AFTER, so a test never has to guess which rows the tenant/key bootstrap
(POST /admin/auth/signup, /admin/auth/login, /admin/keys — all already audited) left behind.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a new tenant, log in, create an API key. Returns {key, key_id, tenant_id, jwt}."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


@dataclass(frozen=True)
class AuditRow:
    """One audit_events row, read back straight from the table."""

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_key_id: uuid.UUID | None
    actor_scim_token_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str

    def __str__(self) -> str:
        return (
            f"action={self.action!r} target_type={self.target_type!r} "
            f"target_id={self.target_id!r} tenant={self.tenant_id} "
            f"user={self.actor_user_id} key={self.actor_key_id} "
            f"email={self.actor_email!r} result={self.result!r}"
        )


_SELECT = (
    "SELECT id, tenant_id, actor_user_id, actor_key_id, actor_scim_token_id, actor_email, "
    "action, target_type, target_id, result FROM audit_events"
)


async def audit_ids(app: Any) -> set[uuid.UUID]:
    """Every audit_events id currently in the table — the pre-mutation snapshot."""
    async with app.state.sessionmaker() as session:
        rows = (await session.execute(text("SELECT id FROM audit_events"))).scalars().all()
    return set(rows)


async def new_audit_rows(app: Any, before: set[uuid.UUID]) -> list[AuditRow]:
    """Every audit_events row written since the `before` snapshot, oldest first."""
    async with app.state.sessionmaker() as session:
        result = (await session.execute(text(_SELECT + " ORDER BY created_at, action"))).all()
    return [AuditRow(*row) for row in result if row[0] not in before]


def describe(rows: list[AuditRow]) -> str:
    """Render rows for a failure message — the whole point is naming what DID appear."""
    if not rows:
        return "    (no audit_events rows were written at all)"
    return "\n".join(f"    {row}" for row in rows)


def assert_key_actor(row: AuditRow, *, tenant_id: str, key_id: str, label: str) -> None:
    """The A1 / R:ACTOR_FABRICATION pin: the KEY is the actor, and no user is invented.

    On key-authenticated /v1 surfaces AuthzResult carries no user identity. The
    audit_missing_actor invariant (audit/domain/audit_event.py:57) accepts a key-only actor,
    and realtime_relay_ws.py:256 is the shipped precedent. Fabricating a user here — e.g.
    attributing the mutation to the tenant's owner — would be a false attribution in the
    evidence SOC 2 CC-series controls lean on.
    """
    assert str(row.tenant_id) == tenant_id, (
        f"{label}: audit row carries tenant_id={row.tenant_id}, expected the authed tenant "
        f"{tenant_id}"
    )
    assert row.actor_key_id is not None and str(row.actor_key_id) == key_id, (
        f"{label}: audit row must carry actor_key_id={key_id} (the authenticated API key IS "
        f"the actor), got {row.actor_key_id}"
    )
    assert row.actor_user_id is None, (
        f"{label}: actor_user_id must stay None on a key-authenticated surface — got "
        f"{row.actor_user_id}. R:ACTOR_FABRICATION: AuthzResult has no user identity, so any "
        "user id here was invented."
    )


def assert_user_actor(row: AuditRow, *, tenant_id: str, label: str) -> None:
    """The M2 (rev 2) pin for the JWT-authed /admin twin: a real USER actor.

    `PUT /admin/evals/sets/{set_id}/baseline` (evals/console/router.py:220) authenticates with
    `identity: Identity` — there is no AuthzResult and no key. Applying the key rule there
    would leave all three actor fields None, raise audit_missing_actor at the CALL SITE
    (outside record_audit's try) and 500 the route: a self-inflicted R:AUDIT_BLOCKS_REQUEST.
    So this surface takes actor_user_id/actor_email from the Identity, mirroring every other
    /admin emitter — and actor_key_id must stay None, because no key authenticated it.
    """
    assert str(row.tenant_id) == tenant_id, (
        f"{label}: audit row carries tenant_id={row.tenant_id}, expected the authed tenant "
        f"{tenant_id}"
    )
    assert row.actor_user_id is not None, (
        f"{label}: the JWT-authed /admin twin must carry actor_user_id from the Identity — "
        "got None. All-None actors raise audit_missing_actor at the call site and 500 the "
        "route (M2, R:AUDIT_BLOCKS_REQUEST)."
    )
    assert row.actor_email, (
        f"{label}: the /admin twin must carry actor_email from the Identity (got "
        f"{row.actor_email!r}) — it is the only actor field the evidence projections read "
        "today, so omitting it makes this row anonymous downstream (M6)."
    )
    assert row.actor_key_id is None, (
        f"{label}: actor_key_id must stay None on a JWT-authenticated surface — got "
        f"{row.actor_key_id}. No API key authenticated this call, so any key id here was "
        "invented (R:ACTOR_FABRICATION)."
    )


def id_forms(resource_id: str) -> set[str]:
    """Every spelling of one resource id that M2's "the resource id" legitimately allows.

    The tree is genuinely split and M2 (node line 30) does not settle it. The /v1 wire ids are
    PREFIXED (`es_<32hex>` eval set, `er_` run, `ec_` case, `vs_` vector store, `file-`,
    `ftjob-`), while every already-audited module writes the RAW uuid — teams/api/router.py:140
    and keys/api/router.py:142 both use `str(row.id)`. A Build that follows the audit-table
    precedent would write the raw uuid; a Build that follows the module's own wire vocabulary
    would write the prefixed form. BOTH satisfy "the resource id", so pinning one would
    false-red a correct Build over a spelling the contract never chose.

    Accepting both spellings still refuses a fabricated, empty, or unrelated id — which is the
    property the check exists for. (Memory and conversations already return raw uuids, so
    there this is a no-op.)
    """
    forms = {resource_id}
    _, sep, tail = resource_id.partition("_") if "_" in resource_id else resource_id.partition("-")
    if sep and len(tail) == 32:
        try:
            forms.add(str(uuid.UUID(hex=tail)))
        except ValueError:
            pass
    return forms


def assert_event_shape(
    row: AuditRow, *, prefixes: set[str], target_ids: set[str], label: str
) -> None:
    """The M2 / A8 pin: `<module>.<verb>` action, bare-noun target_type, real target_id.

    The VERB half is deliberately not pinned to a literal — A8 fixes the family and the module
    prefix, matching the existing flat cache.update / rate_card.upsert vocabulary, and leaves
    the verb to Build. Pinning invented verb strings would fail Build for a naming choice the
    contract never made.

    The PREFIX is likewise accepted in both spellings the contract offers: M2 says
    `<module>.<verb>` and the modules are named plurally in `scope:` (vector_stores,
    conversations), while A8's examples are singular (`vector_store.create`,
    `conversation.create`). The contract contradicts itself, so both pass; what is refused is
    a prefix from neither family.

    `target_ids` is the set of ids that mutation legitimately concerns — normally ONE resource
    (in both id spellings, see id_forms), occasionally two where M2's "the resource id" is
    genuinely ambiguous for a relational mutation (pinning a baseline concerns both the eval
    set and the run; attaching a file concerns both the store and the file).
    """
    matched_prefix = next((p for p in prefixes if row.action.startswith(f"{p}.")), None)
    assert matched_prefix is not None, (
        f"{label}: action {row.action!r} is in none of the accepted families "
        f"{sorted(f'{p}.*' for p in prefixes)} — A8 pins the flat `<module>.<verb>` "
        "vocabulary (cache.update / rate_card.upsert precedent)"
    )
    verb = row.action[len(matched_prefix) + 1 :]
    assert verb and "." not in verb, (
        f"{label}: action {row.action!r} must be exactly `{matched_prefix}.<verb>` — one dot, "
        f"a non-empty verb; got verb {verb!r}"
    )
    assert row.target_id in target_ids, (
        f"{label}: target_id must be the resource id the endpoint returned — one of "
        f"{sorted(target_ids)!r}, got {row.target_id!r}"
    )
    assert row.target_type and row.target_type.strip(), (
        f"{label}: target_type must be the bare resource noun, got {row.target_type!r}"
    )
    assert "/" not in row.target_type and "." not in row.target_type, (
        f"{label}: target_type must be a BARE noun, not a path or a dotted name — got "
        f"{row.target_type!r}"
    )


@pytest.fixture
async def tenant(client: Any) -> dict[str, str]:
    """The authed tenant every behavioral check mutates as."""
    return await _signup_and_key(
        client,
        tenant_name="AuditCov",
        email="audit-cov@example.io",
        password="audit-cov-battery",
    )
