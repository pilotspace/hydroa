"""RED suite — domain-routing-unification migration backfill (TASK.md §3 — FROZEN @ v1,
M7).

Upgrades to the PARENT revision (b7e2c4a9f1d3, the real alembic head BEFORE this task —
confirmed via `alembic heads` at Ground SHA 87fc811) first, seeds pre-existing OIDC+SAML
`email_domains` legacy data directly via raw SQL (simulating tenants that predate this
task's migration), THEN upgrades to head and asserts the exact backfilled
`tenant_domain_claims` rows — never a live application code path.

RED before BUILD: the new migration does not exist yet, so `command.upgrade(cfg,
"head")` stops at b7e2c4a9f1d3 (a no-op — already there) and NEITHER domain ever gets a
backfilled claim row — the honest missing-implementation red (a KeyError looking up a
row that was never created, not a broken test harness).

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import datetime as dt
import uuid

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

# The real alembic head at Ground SHA 87fc811 (confirmed via `alembic heads`), i.e. the
# LAST revision before this task's own new backfill migration is added.
PARENT_REVISION = "b7e2c4a9f1d3"

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_migration_grandfathers_and_dedups() -> None:
    """Scenario: 'backfill grandfathers existing email_domains, first-claimant wins' (M7).

    Legacy OIDC config (tenant A, created EARLIER) lists both "dup-backfill.example" and
    a clean "solo-backfill.example"; legacy SAML config (tenant B, created LATER) also
    lists "dup-backfill.example". After the migration: solo-backfill.example -> A
    (verified), dup-backfill.example -> A (earliest claimant wins), and tenant B's
    colliding entry is logged + skipped (never backfilled, never a second verified row —
    the partial unique index on tenant_domain_claims forbids two anyway).
    """
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    earlier = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    later = dt.datetime(2026, 2, 1, tzinfo=dt.UTC)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'Backfill Tenant A')", tenant_a
        )
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'Backfill Tenant B')", tenant_b
        )
        await conn.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at)"
            " VALUES ($1, $2, 'ownera@backfill-domain-routing.example', 'x', 'owner', $3)",
            user_a,
            tenant_a,
            earlier,
        )
        await conn.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at)"
            " VALUES ($1, $2, 'ownerb@backfill-domain-routing.example', 'x', 'owner', $3)",
            user_b,
            tenant_b,
            later,
        )

        await conn.execute(
            "INSERT INTO oidc_provider_configs "
            "(tenant_id, issuer, client_id, client_secret_enc, token_url, jwks_url, "
            " email_domains, enabled, created_at) "
            "VALUES ($1, 'https://legacy-idp-a.test', 'legacy-client-a', $2, "
            " 'https://legacy-idp-a.test/token', "
            " 'https://legacy-idp-a.test/.well-known/jwks.json', $3, true, $4)",
            tenant_a,
            b"not-real-ciphertext-bytes",
            ["dup-backfill.example", "solo-backfill.example"],
            earlier,
        )
        await conn.execute(
            "INSERT INTO saml_provider_configs "
            "(tenant_id, idp_entity_id, idp_sso_url, idp_x509_cert, email_domains, "
            " enabled, created_at) "
            "VALUES ($1, 'https://legacy-idp-b.test/entity', 'https://legacy-idp-b.test/sso', "
            " 'not-a-real-pem-cert', $2, true, $3)",
            tenant_b,
            ["dup-backfill.example"],
            later,
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT tenant_id, domain, status FROM tenant_domain_claims WHERE domain IN "
            "('dup-backfill.example', 'solo-backfill.example')"
        )
    finally:
        await conn.close()

    by_domain = {r["domain"]: r for r in rows}

    assert "solo-backfill.example" in by_domain, (
        "tenant A's uncontested legacy domain must be backfilled into a verified claim"
    )
    assert by_domain["solo-backfill.example"]["tenant_id"] == tenant_a
    assert by_domain["solo-backfill.example"]["status"] == "verified"

    assert "dup-backfill.example" in by_domain, (
        "the contested domain must still backfill for its earliest claimant"
    )
    assert by_domain["dup-backfill.example"]["tenant_id"] == tenant_a, (
        "tenant A's OIDC config (created earlier) must win the single verified claim over "
        "tenant B's SAML config (created later)"
    )
    assert by_domain["dup-backfill.example"]["status"] == "verified"

    dup_rows = [r for r in rows if r["domain"] == "dup-backfill.example"]
    assert len(dup_rows) == 1, (
        "exactly ONE verified claim may ever exist for a collided domain — tenant B's "
        "losing entry must be logged and skipped, never backfilled as a second row "
        f"(partial unique index would reject it anyway); got {len(dup_rows)} rows"
    )
