"""Red->green suite for domain-verify-notify (domain-verify-notify TASK.md §3/§4 — FROZEN
@ v1, SECURITY task). One test per §2 SCENARIOS row + the migration-manifest reconciliation
check (§4 test_plan, 14 tests total).

MUST run RED (missing implementation) before Build: `DomainVerifyNotifyScheduler` /
`ERR_DOMAIN_CLAIM_NOT_PENDING` / the two `/notify` routes do not exist yet, so every test
that reaches them fails on import (ModuleNotFoundError/ImportError) or a 404/405 from the
router not knowing the path — the established true-red convention this codebase already
uses (see tests/domain_capture/test_domain_capture.py's own docstring).

Asserts observable behavior only: HTTP status, `code` field, DB row effects, captured email
messages — never internal implementation details. The DNS-TXT proof itself is NEVER
re-implemented here — every scheduler test drives the SAME FakeDnsResolver test double the
sibling domain_capture suite uses, reusing VerifyDomainClaimUseCase's own frozen contract by
construction (a scheduler that duplicated the check would fail these tests exactly the same
way; what pins the SECURITY invariant is `test_scheduler_leaves_unmatched_pending` +
`test_scheduler_failopen_on_dns_error` proving no path other than a live TXT match flips a
claim, and `test_recipient_is_owner_never_request` + `test_exactly_once_email_on_double_claim`
pinning R-sec-1/R-sec-3).

Coverage target: 95% (security task — the scheduler + opt-in + proof-reuse paths fully
covered, per TASK.md §4).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    DOMAIN_CLAIMS,
    FakeDnsResolver,
    FakeEmailSender,
    bearer,
    issue_token,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    return body


def _record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


async def _claim_status(db: AsyncSession, claim_id: str) -> str:
    row = (
        await db.execute(
            text("SELECT status FROM tenant_domain_claims WHERE id = :id"), {"id": claim_id}
        )
    ).scalar_one()
    return str(row)


async def _notify_row(db: AsyncSession, claim_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT notify_requested_at, notified_at FROM tenant_domain_claims WHERE id = :id"
            ),
            {"id": claim_id},
        )
    ).one()


def _make_scheduler(
    app: Any,
    *,
    dns_resolver: FakeDnsResolver,
    email_sender: FakeEmailSender,
    origin: str = "https://app.notifytest.example",
) -> Any:
    from gateway.domain_capture.application.notify_scheduler import DomainVerifyNotifyScheduler

    return DomainVerifyNotifyScheduler(
        session_factory=app.state.sessionmaker,
        dns_resolver=dns_resolver,
        email_sender=email_sender,
        dns_timeout_seconds=5.0,
        origin=origin,
    )


async def _create_and_optin(
    client: httpx.AsyncClient, token: str, domain: str
) -> tuple[str, str]:
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    assert create.status_code == 201, create.text
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]

    optin = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token))
    assert optin.status_code == 200, optin.text
    return claim_id, claim_token


# ===========================================================================
# 1. Owner opts in to be emailed when live   (M1)
# ===========================================================================


async def test_optin_sets_flag(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-optin.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "notify-optin.io"}, headers=bearer(token)
    )
    claim_id = create.json()["claim_id"]

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_id"] == claim_id
    assert body["notify_requested_at"] is not None
    assert body["notified_at"] is None

    row = await _notify_row(db_session, claim_id)
    assert row.notify_requested_at is not None


# ===========================================================================
# 2. Opting in twice is an idempotent no-op success   (M1)
# ===========================================================================


async def test_optin_idempotent(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-optin-idem.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "notify-optin-idem.io"}, headers=bearer(token)
    )
    claim_id = create.json()["claim_id"]

    first = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token))
    assert first.status_code == 200, first.text
    first_ts = first.json()["notify_requested_at"]

    second = await client.post(
        f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token)
    )
    assert second.status_code == 200, second.text
    assert second.json()["notify_requested_at"] == first_ts, (
        "a repeat opt-in must be a true no-op — the original opt-in timestamp is preserved"
    )


# ===========================================================================
# 3. Owner opts out to stop the watch   (M2)
# ===========================================================================


async def test_optout_clears_flag(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-optout.io"
    )
    claim_id, _claim_token = await _create_and_optin(client, token, "notify-optout.io")

    resp = await client.delete(f"{DOMAIN_CLAIMS}/{claim_id}/notify", headers=bearer(token))

    assert resp.status_code == 204, resp.text
    row = await _notify_row(db_session, claim_id)
    assert row.notify_requested_at is None


# ===========================================================================
# 4. Scheduler auto-verifies and emails the OWNER when the record goes live
#    (M4, M5, M3)
# ===========================================================================


async def test_scheduler_verifies_and_emails_owner(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
    fake_email_sender: FakeEmailSender,
) -> None:
    domain = "notify-live.io"
    owner_email = "owner@notify-live.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=owner_email)
    claim_id, claim_token = await _create_and_optin(client, token, domain)
    fake_dns.set_record(_record_name(domain), claim_token)

    scheduler = _make_scheduler(app, dns_resolver=fake_dns, email_sender=fake_email_sender)
    count = await scheduler.refresh_once()

    assert count == 1
    assert await _claim_status(db_session, claim_id) == "verified"
    row = await _notify_row(db_session, claim_id)
    assert row.notified_at is not None
    assert len(fake_email_sender.sent) == 1
    assert fake_email_sender.sent[0].to == owner_email
    assert domain in fake_email_sender.sent[0].subject or domain in (
        fake_email_sender.sent[0].text_body
    )


# ===========================================================================
# 5. Recipient is server-derived, never from any request   (R-sec-1, M3)
# ===========================================================================


async def test_recipient_is_owner_never_request(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
    fake_email_sender: FakeEmailSender,
) -> None:
    domain = "notify-recipient.io"
    owner_email = "realowner@notify-recipient.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=owner_email)
    _claim_id, claim_token = await _create_and_optin(client, token, domain)
    fake_dns.set_record(_record_name(domain), claim_token)

    scheduler = _make_scheduler(app, dns_resolver=fake_dns, email_sender=fake_email_sender)
    await scheduler.refresh_once()

    assert len(fake_email_sender.sent) == 1
    assert fake_email_sender.sent[0].to == owner_email
    assert fake_email_sender.sent[0].to != f"admin@{domain}"
    assert fake_email_sender.sent[0].to != f"webmaster@{domain}"


# ===========================================================================
# 6. Not-yet-live record leaves the claim untouched (proof unchanged)   (M7)
# ===========================================================================


async def test_scheduler_leaves_unmatched_pending(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
    fake_email_sender: FakeEmailSender,
) -> None:
    domain = "notify-not-live.io"
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-not-live.io"
    )
    claim_id, _claim_token = await _create_and_optin(client, token, domain)
    # Deliberately leave fake_dns unconfigured for this name -> lookup_txt returns [].

    scheduler = _make_scheduler(app, dns_resolver=fake_dns, email_sender=fake_email_sender)
    count = await scheduler.refresh_once()

    assert count == 0
    assert await _claim_status(db_session, claim_id) == "pending"
    row = await _notify_row(db_session, claim_id)
    assert row.notified_at is None
    assert len(fake_email_sender.sent) == 0


# ===========================================================================
# 7. A DNS lookup failure never crashes the loop or flips the claim   (M6, M7)
# ===========================================================================


async def test_scheduler_failopen_on_dns_error(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
    fake_email_sender: FakeEmailSender,
) -> None:
    domain = "notify-dns-fail.io"
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-dns-fail.io"
    )
    claim_id, _claim_token = await _create_and_optin(client, token, domain)
    fake_dns.fail_timeout(_record_name(domain))

    scheduler = _make_scheduler(app, dns_resolver=fake_dns, email_sender=fake_email_sender)
    count = await scheduler.refresh_once()  # must NOT raise

    assert count == 0
    assert await _claim_status(db_session, claim_id) == "pending"
    row = await _notify_row(db_session, claim_id)
    assert row.notified_at is None
    assert len(fake_email_sender.sent) == 0

    # The loop continues to the next tick — a later, clean tick succeeds.
    claim_token_value = (
        await db_session.execute(
            text("SELECT verification_token FROM tenant_domain_claims WHERE id = :id"),
            {"id": claim_id},
        )
    ).scalar_one()
    fake_dns.clear_timeout(_record_name(domain))
    fake_dns.set_record(_record_name(domain), claim_token_value)
    second_count = await scheduler.refresh_once()
    assert second_count == 1
    assert await _claim_status(db_session, claim_id) == "verified"


# ===========================================================================
# 8. Exactly one email even across overlapping ticks / replicas   (M5)
# ===========================================================================


async def test_exactly_once_email_on_double_claim(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
) -> None:
    from gateway.domain_capture.infrastructure.repository import SqlAlchemyDomainClaimRepository

    domain = "notify-exactly-once.io"
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-exactly-once.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))
    assert verify.status_code == 200, verify.text

    repo = SqlAlchemyDomainClaimRepository(db_session)
    first_won = await repo.mark_notified(claim_id=uuid.UUID(claim_id))
    second_won = await repo.mark_notified(claim_id=uuid.UUID(claim_id))

    assert first_won is True, "the first atomic claim must win"
    assert second_won is False, "a second concurrent claim must lose — exactly-once"


# ===========================================================================
# 9. Notify on another tenant's claim is not found   (R1)
# ===========================================================================


async def test_notify_other_tenant_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@notify-cross-globex.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "notify-cross.io"}, headers=bearer(globex_token)
    )
    globex_claim_id = create.json()["claim_id"]

    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-cross-acme.io"
    )

    resp = await client.post(
        f"{DOMAIN_CLAIMS}/{globex_claim_id}/notify", json={}, headers=bearer(acme_token)
    )

    _assert_problem(resp, 404, "ERR_DOMAIN_CLAIM_NOT_FOUND")
    row = await _notify_row(db_session, globex_claim_id)
    assert row.notify_requested_at is None, "globex's claim is completely unchanged"


# ===========================================================================
# 10. Notify on an already-verified claim is rejected   (R2)
# ===========================================================================


async def test_notify_already_verified_409(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    domain = "notify-already-verified.io"
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-already-verified.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))
    assert verify.status_code == 200, verify.text

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token))

    _assert_problem(resp, 409, "ERR_DOMAIN_CLAIM_NOT_PENDING")
    row = await _notify_row(db_session, claim_id)
    assert row.notify_requested_at is None


# ===========================================================================
# 11. Notify on an expired claim is rejected   (R3)
# ===========================================================================


async def test_notify_expired_410(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    domain = "notify-expired.io"
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-expired.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]

    await db_session.execute(
        text(
            "UPDATE tenant_domain_claims SET expires_at = now() - interval '1 hour' WHERE id = :id"
        ),
        {"id": claim_id},
    )
    await db_session.commit()

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token))

    _assert_problem(resp, 410, "ERR_DOMAIN_CLAIM_EXPIRED")
    row = await _notify_row(db_session, claim_id)
    assert row.notify_requested_at is None


# ===========================================================================
# 12. A non-owner cannot opt a claim in   (R4)
# ===========================================================================


async def test_notify_non_owner_403(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "notify-non-owner.io"
    tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-non-owner.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    claim_id = create.json()["claim_id"]

    admin_token = issue_token(app, role=Role.ADMIN, tenant_id=tenant_id)

    resp = await client.post(
        f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(admin_token)
    )

    _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    row = await _notify_row(db_session, claim_id)
    assert row.notify_requested_at is None


# ===========================================================================
# 13. Opt-in beyond the rate limit is rejected   (R5)
# ===========================================================================


async def test_notify_rate_limited_429(
    client: httpx.AsyncClient,
    low_limit_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@notify-ratelimit.io"
    )
    _low_app, low_client = low_limit_client

    create = await low_client.post(
        DOMAIN_CLAIMS, json={"domain": "notify-ratelimit.io"}, headers=bearer(token)
    )
    assert create.status_code == 201, create.text
    claim_id = create.json()["claim_id"]

    first = await low_client.post(
        f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token)
    )
    assert first.status_code == 200, first.text

    second = await low_client.post(
        f"{DOMAIN_CLAIMS}/{claim_id}/notify", json={}, headers=bearer(token)
    )

    _assert_problem(second, 429, "ERR_RATE_LIMITED")
    assert second.headers.get("Retry-After") is not None


# ===========================================================================
# 14. Migration manifest reconciled — the 2 new columns exist   (R-drift)
# ===========================================================================


async def test_migration_manifest_reconciled(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tenant_domain_claims'"
        )
    )
    columns = {r[0] for r in rows}
    assert {"notify_requested_at", "notified_at"} <= columns, (
        f"expected the 2 additive notify columns on tenant_domain_claims; got {columns}"
    )
