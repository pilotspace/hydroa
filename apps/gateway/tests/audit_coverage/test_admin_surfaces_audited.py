"""S4 / M8 — the /admin surfaces Tin promoted at freeze must leave a legible audit trail.

covers: M8, A2, A24, A25, A26, A27, A28, A29, E7

Drives each promoted route through its REAL success path and asserts the mutation leaves a
DISTINCT-action audit_events row whose actor is the JWT Identity — actor_user_id and
actor_email set, actor_key_id None. That actor rule is the deliberate INVERSE of the /v1
checks in test_five_modules_audited.py: those surfaces authenticate an API key and must NOT
invent a user (R:ACTOR_FABRICATION); these authenticate a human's JWT and must NOT invent a
key. Same invariant, opposite direction, and the difference is the whole point of A24 —
these are the only retrofit rows in this task with a real human actor.

The headline row is `POST /admin/domain-claims/{claim_id}/verify`. "Who verified this domain,
and when" is the CC6 question that earned the promotion: a domain verify decides which tenant
every future email address on that domain silently joins. Today that decision is made with no
evidence at all, which is why this file is RED.

RED TODAY: none of the nine routes calls record_audit, so every one of them is silent and the
first assertion below names all nine.

WHY NINE, NOT TEN — an escalation, not a shortcut. A25 enumerates ten promoted routes; the
tenth is `POST /admin/auth/access-requests`, and it cannot satisfy M8 as written:

    access_requests_router = APIRouter(prefix="/admin/auth", tags=["access-requests-public"])

    async def create_access_request(body, request, use_case) -> AccessRequestCreateResponse:
        ...                                       # no Identity, no authz dependency

It shares the /admin/auth prefix but is the PUBLIC signup-refusal intake: an unauthenticated
stranger asking for access. A24's premise — "the JWT Identity these routes already
authenticate" — is false for it, so M8's "JWT Identity as actor" is unsatisfiable (no user, no
tenant, and an untrusted body-supplied email). Worse, auditing it fights its own security
contract: the handler is deliberately anti-enumeration (signup-refusal-router R3 — no branch
may read tenant or user state), and attributing an event to a tenant would re-open exactly the
enumeration oracle that task closed. It therefore keeps a `no-tenant-state` exemption row in
the walker rather than a `deferred:` one, and this file drives the other nine. Direction owes
Tin that difference explicitly; it is not a silent narrowing.

`POST /internal/catalog/sync` (A27) likewise stays exempt as `no-tenant-state` — Envoy blocks
/internal and it carries no principal to attribute.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.catalog_sync_trigger.conftest import (
    FakeCatalogModel,
    FakeCatalogSource,
    install_fake_source,
)
from tests.conftest import TEST_JWT_SECRET
from tests.domain_capture.conftest import (
    DOMAIN_CLAIMS,
    FakeDnsResolver,
    FakeEmailSender,
    signup_and_login,
)

from .conftest import AuditRow, assert_user_actor, audit_ids, bearer, describe, new_audit_rows

pytestmark = pytest.mark.asyncio

DOMAIN = "promoted-admin-surface.io"
OWNER_EMAIL = f"owner@{DOMAIN}"

# The catalog model the sync seeds and the model-override PUT then flips. Shape copied from
# tests/catalog_sync_trigger/conftest.py's own OPUS constant (a suite-local fixture there;
# duplicated rather than imported so a change to that suite's data cannot silently retarget
# this guard).
CATALOG_MODEL_ID = "anthropic/claude-opus-4"
CATALOG_MODEL = FakeCatalogModel(
    id=CATALOG_MODEL_ID,
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
)

# The route whose absence motivated the whole promotion — named in the failure message.
VERIFY_ROUTE = "POST /admin/domain-claims/{claim_id}/verify"

_HMAC_LABEL = b"member-verify-code"


def _code_hash(code: str, jwt_secret: str) -> str:
    """The FROZEN member-verify hash-at-rest formula, reimplemented as a seeding oracle.

    HMAC-SHA256(code, key=HMAC(jwt_secret, "member-verify-code")), hex — byte-for-byte the
    helper in tests/domain_capture/test_member_verified_recognition.py:66 (private there, so
    copied rather than imported). Used ONLY to put a known code in flight so the member-verify
    route can be driven down its success path; nothing here asserts on hashing.
    """
    inner_key = hmac.new(jwt_secret.encode(), _HMAC_LABEL, hashlib.sha256).digest()
    return hmac.new(inner_key, code.encode(), hashlib.sha256).hexdigest()


async def _seed_code(session: AsyncSession, claim_id: str, *, code: str) -> None:
    """Put a known member-verify code in flight (the issuance use case is not driveable from
    a test without reading the email body). Mirrors the same suite's own `_seed_code`."""
    await session.execute(
        text(
            "UPDATE tenant_domain_claims SET member_verify_code_hash = :h, "
            "member_verify_code_expires_at = :e, member_verify_attempt_count = 0 "
            "WHERE id = :id"
        ),
        {
            "h": _code_hash(code, TEST_JWT_SECRET),
            "e": datetime.now(UTC) + timedelta(minutes=15),
            "id": claim_id,
        },
    )
    await session.commit()


def _record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


async def _settle(app: Any, before: set[uuid.UUID], *, budget_s: float = 0.6) -> list[AuditRow]:
    """Every audit row written since `before`, polled — never a fixed sleep.

    record_audit is scheduled fire-and-forget (audit_writer.py:41), so a row can land after
    the response returns. Poll until the first row appears, then take one short extra look so
    a second row from the SAME route is not mis-attributed to the next one. Bounded: a route
    that writes nothing costs `budget_s` and then reports silence, which is today's red.
    """
    deadline = time.monotonic() + budget_s
    while True:
        rows = await new_audit_rows(app, before)
        if rows:
            await asyncio.sleep(0.05)
            return await new_audit_rows(app, before)
        if time.monotonic() >= deadline:
            return []
        await asyncio.sleep(0.02)


async def test_admin_surfaces_audited(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The nine JWT-authed promoted /admin routes each leave one distinct, human-attributed row."""
    dns = FakeDnsResolver()
    app.state.dns_resolver = dns
    app.state.email_sender = FakeEmailSender()
    install_fake_source(app, FakeCatalogSource([CATALOG_MODEL]))

    tenant_id, token = await signup_and_login(
        client, tenant_name="PromotedAdmin", email=OWNER_EMAIL
    )
    auth = bearer(token)

    observed: list[tuple[str, list[AuditRow]]] = []
    undriveable: list[str] = []
    seen = await audit_ids(app)

    async def drive(
        label: str, request: Callable[[], Awaitable[httpx.Response]], expected: int
    ) -> httpx.Response | None:
        """Drive one promoted route and bank the audit rows it produced.

        A route that does not reach its success status is banked as UNDRIVEABLE rather than
        failing here: its silence would be meaningless (it never mutated anything), and
        aborting mid-sequence would hide the silence of every route after it. Both lists are
        asserted below, so nothing is masked — see the anti-vacuity assert on the totals.

        The request is taken as a FACTORY, not an already-awaited response, because an
        unhandled server exception propagates straight out of httpx's ASGITransport instead of
        becoming a 500 — awaiting at the call site would abort the whole sequence on one broken
        route and lose the M8 evidence for every route after it.
        """
        nonlocal seen
        try:
            resp = await request()
        except Exception as exc:  # noqa: BLE001 — banked, then asserted on below
            undriveable.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return None
        if resp.status_code != expected:
            undriveable.append(
                f"{label}: expected {expected}, got {resp.status_code} — {resp.text[:200]}"
            )
            return resp
        rows = await _settle(app, seen)
        seen |= {row.id for row in rows}
        observed.append((label, rows))
        return resp

    # --- domain_capture ×7 ------------------------------------------------------------
    created = await drive(
        "POST /admin/domain-claims",
        lambda: client.post(DOMAIN_CLAIMS, json={"domain": DOMAIN}, headers=auth),
        201,
    )
    assert created is not None, "the claim-create route raised — see the suite docstring"
    assert created.status_code == 201, (
        "the claim-create route is this sequence's entry point — every other domain-claim "
        f"route needs its claim_id, so a failure here is fatal: {created.text}"
    )
    claim_id = str(created.json()["claim_id"])
    claim_token = str(created.json()["dns_record_value"]).split("=", 1)[1]
    claim_url = f"{DOMAIN_CLAIMS}/{claim_id}"

    await drive(
        "POST /admin/domain-claims/{claim_id}/notify",
        lambda: client.post(f"{claim_url}/notify", json={}, headers=auth),
        200,
    )
    await drive(
        "DELETE /admin/domain-claims/{claim_id}/notify",
        lambda: client.delete(f"{claim_url}/notify", headers=auth),
        204,
    )

    await _seed_code(db_session, claim_id, code="121212")
    await drive(
        "POST /admin/domain-claims/{claim_id}/member-verify/resend",
        lambda: client.post(f"{claim_url}/member-verify/resend", json={}, headers=auth),
        200,
    )

    code = "424242"
    await _seed_code(db_session, claim_id, code=code)
    await drive(
        "POST /admin/domain-claims/{claim_id}/member-verify",
        lambda: client.post(f"{claim_url}/member-verify", json={"code": code}, headers=auth),
        200,
    )

    dns.set_record(_record_name(DOMAIN), claim_token)
    await drive(
        VERIFY_ROUTE,
        lambda: client.post(f"{claim_url}/verify", headers=auth),
        200,
    )

    await drive(
        "DELETE /admin/domain-claims/{claim_id}",
        lambda: client.delete(claim_url, headers=auth),
        204,
    )

    # --- catalog ×2 -------------------------------------------------------------------
    await drive(
        "POST /admin/catalog/sync",
        lambda: client.post("/admin/catalog/sync", headers=auth),
        200,
    )
    # Seed the catalog row the override PUT needs DIRECTLY rather than relying on the sync
    # above having succeeded. Chaining them would make one route's failure silently swallow
    # the other's evidence — the PUT would 404 and be reported as undriveable for a reason
    # that has nothing to do with it.
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length) VALUES (:id, :name, :ctx) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": CATALOG_MODEL_ID, "name": CATALOG_MODEL.name, "ctx": CATALOG_MODEL.context_length},
    )
    await db_session.commit()
    await drive(
        "PUT /admin/models/{model_id:path}",
        lambda: client.put(
            f"/admin/models/{CATALOG_MODEL_ID}", json={"enabled": False}, headers=auth
        ),
        200,
    )

    # ANTI-VACUITY: every promoted route landed in exactly one of the two buckets. Without
    # this, a route silently dropped from the sequence would shrink the guard's population and
    # the remaining asserts would pass over a smaller world.
    assert len(observed) + len(undriveable) == 9, (
        f"all nine promoted routes must be attempted — got {len(observed)} driven + "
        f"{len(undriveable)} undriveable"
    )

    # 1. SILENCE — the whole reason S4 exists. Named route by route.
    silent = [label for label, rows in observed if not rows]
    assert not silent, (
        f"{len(silent)} of the 9 promoted /admin surfaces mutated tenant state and wrote NO "
        "audit event (M8, S4):\n"
        + "\n".join(f"    {label}" for label in silent)
        + f"\n\n  E7 / CC6: {VERIFY_ROUTE} is the row that earned this promotion — a domain "
        "verify decides which tenant every future address on that domain silently joins, so "
        '"who verified this domain, and when" must be answerable from the audit trail alone. '
        "Today it is not answerable at all.\n"
        "  Rows that DID appear, by route:\n"
        + "\n".join(f"  {label}:\n{describe(rows)}" for label, rows in observed)
        + (
            "\n  Routes that never reached their success path (asserted separately below):\n"
            + "\n".join(f"    {line}" for line in undriveable)
            if undriveable
            else ""
        )
    )

    # 2. UNDRIVEABLE — a promoted route that cannot reach its own success path was never
    #    given the chance to audit anything. That is a defect in the ROUTE, not in the audit
    #    retrofit, and it is reported separately so the two are never confused.
    assert not undriveable, (
        f"{len(undriveable)} promoted /admin route(s) could not be driven to success, so M8 is "
        "unproven for them (a 5xx here is a bug in the route itself, not in auditing):\n"
        + "\n".join(f"    {line}" for line in undriveable)
    )

    # 3. DISTINCT actions — M8's anti-gaming clause. One shared `admin.mutate` for all nine
    #    would satisfy "emits an event" while destroying the evidence's whole value.
    owner_of: dict[str, str] = {}
    for label, rows in observed:
        for row in rows:
            clash = owner_of.get(row.action)
            assert clash is None, (
                f"action {row.action!r} is emitted by BOTH {clash!r} and {label!r} — M8 "
                "requires a DISTINCT action per promoted surface. A generic action shared "
                "across routes makes the trail unreadable: an auditor cannot tell a domain "
                "revoke from a catalog sync."
            )
            owner_of[row.action] = label

    # 4. ACTOR — the inverse of the /v1 rule (A24). A human did this, and the row must say so.
    for label, rows in observed:
        for row in rows:
            assert_user_actor(row, tenant_id=str(tenant_id), label=label)

    # 5. E7 by name — the CC6 case, asserted explicitly rather than only as part of the loop
    #    above, so a future edit that drops the verify route from this file fails loudly.
    verify_rows = next(rows for label, rows in observed if label == VERIFY_ROUTE)
    assert verify_rows, f"{VERIFY_ROUTE} must leave an audit row (E7)"
    assert all(row.actor_user_id is not None and row.actor_email for row in verify_rows), (
        f"E7: the {VERIFY_ROUTE} row must name the HUMAN who verified the domain "
        f"(actor_user_id + actor_email), not just the tenant:\n{describe(verify_rows)}"
    )
