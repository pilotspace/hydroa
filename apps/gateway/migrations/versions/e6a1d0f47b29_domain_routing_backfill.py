"""domain_routing_backfill — grandfather OIDC+SAML email_domains into verified claims.

Revision ID: e6a1d0f47b29
Revises: b7e2c4a9f1d3
Create Date: 2026-07-18

domain-routing-unification TASK.md §3 M7 (FROZEN @ v1). One-time DATA backfill,
no schema change:

  - Every existing `oidc_provider_configs.email_domains` and
    `saml_provider_configs.email_domains` entry is backfilled into a VERIFIED
    `tenant_domain_claims` row for its tenant, so legacy SSO tenants keep
    routing after login-init moves to the verified-claim source of truth.
  - On a cross-tenant domain collision (two tenants' configs share a domain),
    the EARLIEST config wins the single verified claim — ordered by
    (created_at, then tenant_id); losing entries are LOGGED and skipped (their
    SSO login then fails closed per M2/M3 until the tenant DNS-TXT re-verifies).
  - The partial unique index `uq_domain_claims_domain_verified` structurally
    forbids two verified claims for one domain — every insert is additionally
    wrapped in a SAVEPOINT with IntegrityError caught, logged, and skipped
    (mirrors mark_verified's IntegrityError race-defense shape).
  - A domain string that fails minimal hostname normalization, or a tenant with
    no users row (created_by_user_id is NOT NULL), is logged and skipped —
    never a migration abort.

downgrade(): IRREVERSIBLE DATA — backfilled verified claims are
indistinguishable from organically DNS-verified ones; deleting them could
destroy real verifications. Deliberate no-op.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import IntegrityError

# revision identifiers, used by Alembic.
revision: str = "e6a1d0f47b29"
down_revision: str | None = "b7e2c4a9f1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Synthetic token marker for backfilled rows — verification_token is NOT NULL;
# these rows were grandfathered from admin-typed provider configs, not DNS-TXT.
_BACKFILL_TOKEN = "backfilled-from-provider-config-email-domains"  # noqa: S105 — provenance marker, not a credential


def _normalize(raw: str) -> str | None:
    """Minimal, dependency-free mirror of domain_capture's normalize_domain
    outcome for the backfill path: lowercase + trim; reject empty/single-label
    values. (Migrations never import application code — repo convention.)"""
    candidate = (raw or "").strip().lower()
    if not candidate or len(candidate) > 253 or "." not in candidate:
        return None
    return candidate


def _as_utc(value: datetime | None) -> datetime:
    """Order key: oidc_provider_configs.created_at is a NAIVE timestamp while
    saml_provider_configs.created_at is timezone-aware — naive values are
    treated as UTC so the (created_at, tenant_id) sort never raises."""
    if value is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Collect every (tenant_id, domain) entry from both provider-config
    #    tables, tagged with its config's created_at for first-claimant-wins
    #    ordering (M7: earliest created_at, then tenant_id).
    entries: list[tuple[datetime, uuid.UUID, str, str]] = []
    for table in ("oidc_provider_configs", "saml_provider_configs"):
        rows = bind.execute(
            sa.text(f"SELECT tenant_id, email_domains, created_at FROM {table}")  # noqa: S608 — fixed table names, no user input
        ).fetchall()
        for row in rows:
            for raw_domain in row.email_domains or []:
                normalized = _normalize(raw_domain)
                if normalized is None:
                    logger.warning(
                        "domain_routing_backfill: skipping unnormalizable %s email_domains "
                        "entry %r (tenant %s)",
                        table,
                        raw_domain,
                        row.tenant_id,
                    )
                    continue
                entries.append((_as_utc(row.created_at), row.tenant_id, normalized, table))

    entries.sort(key=lambda e: (e[0], str(e[1])))

    processed: set[tuple[uuid.UUID, str]] = set()
    for created_at, tenant_id, domain, table in entries:
        if (tenant_id, domain) in processed:
            continue  # same tenant lists the domain in both OIDC and SAML configs
        processed.add((tenant_id, domain))

        # Existing verified claim for this domain (any tenant) wins outright.
        existing = bind.execute(
            sa.text(
                "SELECT tenant_id FROM tenant_domain_claims "
                "WHERE domain = :domain AND status = 'verified'"
            ),
            {"domain": domain},
        ).first()
        if existing is not None:
            if existing.tenant_id != tenant_id:
                logger.warning(
                    "domain_routing_backfill: SKIPPED cross-tenant collision — domain %r "
                    "(from %s, tenant %s, created_at %s) is already verified for tenant %s; "
                    "the losing tenant's SSO for this domain fails closed until re-verified",
                    domain,
                    table,
                    tenant_id,
                    created_at.isoformat(),
                    existing.tenant_id,
                )
            continue

        # created_by_user_id is NOT NULL — attribute the backfilled claim to the
        # tenant's earliest user; a userless tenant cannot hold a claim row.
        user_row = bind.execute(
            sa.text(
                "SELECT id FROM users WHERE tenant_id = :tenant_id "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        ).first()
        if user_row is None:
            logger.warning(
                "domain_routing_backfill: SKIPPED domain %r — tenant %s has no users row "
                "to attribute the claim to",
                domain,
                tenant_id,
            )
            continue

        # A pending claim row for (tenant_id, domain) may already exist — flip
        # it to verified instead of inserting (UNIQUE (tenant_id, domain)).
        try:
            with bind.begin_nested():
                pending = bind.execute(
                    sa.text(
                        "SELECT id FROM tenant_domain_claims "
                        "WHERE tenant_id = :tenant_id AND domain = :domain"
                    ),
                    {"tenant_id": tenant_id, "domain": domain},
                ).first()
                if pending is not None:
                    bind.execute(
                        sa.text(
                            "UPDATE tenant_domain_claims "
                            "SET status = 'verified', verified_at = now() "
                            "WHERE id = :id AND status = 'pending'"
                        ),
                        {"id": pending.id},
                    )
                else:
                    bind.execute(
                        sa.text(
                            "INSERT INTO tenant_domain_claims "
                            "(id, tenant_id, domain, verification_token, status, "
                            " created_at, verified_at, expires_at, created_by_user_id) "
                            "VALUES (:id, :tenant_id, :domain, :token, 'verified', "
                            " now(), now(), now(), :user_id)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "tenant_id": tenant_id,
                            "domain": domain,
                            "token": _BACKFILL_TOKEN,
                            "user_id": user_row.id,
                        },
                    )
        except IntegrityError:
            # uq_domain_claims_domain_verified race/duplicate backstop — the
            # partial unique index forbids a second verified claim; the loser
            # is logged and skipped, never a migration abort (M7).
            logger.warning(
                "domain_routing_backfill: SKIPPED domain %r for tenant %s — a verified "
                "claim already exists (partial unique index uq_domain_claims_domain_verified)",
                domain,
                tenant_id,
            )
            continue

        logger.info(
            "domain_routing_backfill: backfilled verified claim domain=%r tenant=%s (from %s)",
            domain,
            tenant_id,
            table,
        )


def downgrade() -> None:
    # IRREVERSIBLE DATA: backfilled verified claims are indistinguishable from
    # organically DNS-verified tenant_domain_claims rows — deleting them could
    # destroy genuine verifications, so this is a deliberate no-op.
    pass
