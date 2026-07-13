"""SQLAlchemy repository for API keys."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.keys.domain.entities import ApiKey, ApiKeyInfo
from gateway.keys.infrastructure.orm import ApiKeyRow


def _resolve_effective_guardrails(
    key_policy: dict[str, Any] | None,
    tenant_configs: dict[str, Any],
) -> dict[str, Any]:
    """Key > tenant > default-off. Wholesale override -- no field merge.

    per-key-guardrail-policies §3 CONTRACT. A non-NULL key_policy (including an
    explicit {}) is used AS-IS; tenant_configs is never consulted when key_policy
    is not None -- the NULL-vs-{} distinction is the caller's to preserve.
    """
    if key_policy is not None:
        return key_policy
    return tenant_configs


def _row_to_api_key(row: ApiKeyRow) -> ApiKey:
    """Convert an ORM row to the ApiKey domain entity."""
    return ApiKey(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        key_hash=row.key_hash,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        monthly_budget_usd=row.monthly_budget_usd,
        soft_budget_usd=row.soft_budget_usd,
        expires_at=row.expires_at,
        model_allowlist=row.model_allowlist,
        rotated_from_key_id=row.rotated_from_key_id,
        rpm_limit=row.rpm_limit,
        tpm_limit=row.tpm_limit,
        team_id=row.team_id,
        cache_enabled=bool(getattr(row, "cache_enabled", False)),
        capture_enabled=bool(getattr(row, "capture_enabled", False)),
        tier=getattr(row, "tier", None),
    )


class SqlAlchemyApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
        key_hash: str,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
        cache_enabled: bool = False,
        tier: str | None = None,
    ) -> ApiKey:
        """Insert a new api_keys row.

        key_id is passed explicitly — never rely on column default for uuid7
        because the plaintext key embeds the id hex and must be consistent.
        Safety (task §5): key_hash stored; plaintext secret never touches this layer.
        """
        row = ApiKeyRow(
            id=key_id,
            tenant_id=tenant_id,
            name=name,
            key_hash=key_hash,
            monthly_budget_usd=monthly_budget_usd,
            soft_budget_usd=soft_budget_usd,
            expires_at=expires_at,
            model_allowlist=model_allowlist,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            team_id=team_id,
            cache_enabled=cache_enabled,
            tier=tier,
        )
        # Use begin_nested() (savepoint) so this works whether or not a transaction
        # is already active on the session (e.g. when team_repo shares the same session).
        async with self._session.begin_nested():
            self._session.add(row)
        await self._session.commit()
        # Refresh to get server-side created_at
        await self._session.refresh(row)
        return _row_to_api_key(row)

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[ApiKeyInfo]:
        result = await self._session.execute(
            select(ApiKeyRow)
            .where(ApiKeyRow.tenant_id == tenant_id)
            .order_by(ApiKeyRow.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            ApiKeyInfo(
                key_id=row.id,
                name=row.name,
                prefix=f"sk-{row.id.hex[:8]}",
                created_at=row.created_at,
                revoked_at=row.revoked_at,
                monthly_budget_usd=row.monthly_budget_usd,
                soft_budget_usd=row.soft_budget_usd,
                expires_at=row.expires_at,
                model_allowlist=row.model_allowlist,
                rpm_limit=row.rpm_limit,
                tpm_limit=row.tpm_limit,
                team_id=row.team_id,
                cache_enabled=bool(getattr(row, "cache_enabled", False)),
            )
            for row in rows
        ]
        # NOTE: ApiKeyInfo (list projection) does not carry `tier` today — the
        # frozen §3 CONTRACT extends KeyInfoResponse (single-key GET/PATCH/POST
        # shapes), not the list projection; adding it here would need a matching
        # ApiKeyInfo.tier field, which §3 does not name. Left as a scope note
        # rather than a silent, unrequested surface widening.

    async def revoke(self, *, key_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Set revoked_at = now() WHERE id = key_id AND tenant_id = tenant_id.

        Returns True if exactly one row was updated, False if zero rows matched
        (key_id unknown OR belongs to another tenant — identical result, no leak).
        """
        result = await self._session.execute(
            update(ApiKeyRow)
            .where(ApiKeyRow.id == key_id, ApiKeyRow.tenant_id == tenant_id)
            .values(revoked_at=func.now())
            .returning(ApiKeyRow.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        from gateway.teams.infrastructure.orm import TeamRow
        from gateway.tenants.infrastructure.orm import PlanRow, TenantRow

        # 4-table LEFT JOIN: api_keys → teams (team_budget_usd)
        #   → tenants (cache_enabled, guardrail_configs, ...) → plans (plan-enforcement,
        #   4th outerjoin: TenantRow.plan_id == PlanRow.id).
        # All LEFT JOINs so un-teamed / un-tenanted / un-planned keys still authenticate.
        # Zero extra DB reads per contract §3 (response-caching, guardrails-core,
        # plan-enforcement M4) — mirrors this same JOIN's own existing precedent exactly.
        # api_keys.guardrail_policy (per-key-guardrail-policies) rides along for free:
        # ApiKeyRow is already selected wholesale below, so the key-level override
        # column costs zero extra columns/JOINs/queries (M3).
        stmt = (
            select(
                ApiKeyRow,
                TeamRow.team_budget_usd,
                TenantRow.cache_enabled,
                TenantRow.guardrail_configs,
                TenantRow.semantic_cache_enabled,
                TenantRow.batch_grouping_enabled,
                TenantRow.zdr_enabled,
                TenantRow.payload_capture_enabled,
                TenantRow.plan_id,
                PlanRow.model_allowlist,
                PlanRow.name,
                TenantRow.default_tier,
            )
            .outerjoin(TeamRow, ApiKeyRow.team_id == TeamRow.id)
            .outerjoin(TenantRow, ApiKeyRow.tenant_id == TenantRow.id)
            .outerjoin(PlanRow, TenantRow.plan_id == PlanRow.id)
            .where(ApiKeyRow.id == key_id)
        )
        result = (await self._session.execute(stmt)).first()
        if result is None:
            return None
        (
            row,
            team_budget_usd,
            tenant_cache_enabled,
            tenant_guardrail_configs,
            tenant_semantic_cache_enabled,
            tenant_batch_grouping_enabled,
            tenant_zdr_enabled,
            tenant_payload_capture_enabled,
            plan_id,
            plan_model_allowlist_raw,
            plan_name,
            tenant_default_tier,
        ) = result
        # Effective cache = key-level OR tenant-level (both default false)
        effective_cache = bool(getattr(row, "cache_enabled", False)) or bool(
            tenant_cache_enabled or False
        )
        # Effective capture = key-level OR tenant-level (mirrors cache_enabled exactly —
        # key can only turn capture ON, never override tenant OFF; payload-capture-store §3
        # Freeze question #1, OR-semantics, Tin-approved 2026-07-10).
        effective_capture = bool(getattr(row, "capture_enabled", False)) or bool(
            tenant_payload_capture_enabled or False
        )
        # Deserialize guardrail_configs: asyncpg may return dict or str depending on driver version
        import json as _json

        raw_gc = tenant_guardrail_configs
        if raw_gc is None:
            tenant_guardrails: dict[str, Any] = {}
        elif isinstance(raw_gc, dict):
            tenant_guardrails = raw_gc
        elif isinstance(raw_gc, str):
            try:
                tenant_guardrails = _json.loads(raw_gc)
            except Exception:
                tenant_guardrails = {}
        else:
            tenant_guardrails = {}

        # per-key-guardrail-policies (M1-M3): resolve key > tenant, wholesale, zero
        # extra IO -- row.guardrail_policy is already part of the ApiKeyRow projection
        # selected above (no new column/JOIN/query). Same dict-or-str driver quirk as
        # tenant_guardrail_configs above, so the same defensive parse applies.
        raw_kp = getattr(row, "guardrail_policy", None)
        key_guardrail_policy: dict[str, Any] | None
        if raw_kp is None:
            key_guardrail_policy = None
        elif isinstance(raw_kp, dict):
            key_guardrail_policy = raw_kp
        elif isinstance(raw_kp, str):
            try:
                parsed_kp = _json.loads(raw_kp)
            except Exception:
                parsed_kp = None
            key_guardrail_policy = parsed_kp if isinstance(parsed_kp, dict) else None
        else:
            key_guardrail_policy = None

        guardrail_configs = _resolve_effective_guardrails(key_guardrail_policy, tenant_guardrails)
        # NOTE for the sibling guardrail-analytics task (depends-on this task):
        # this discriminator is DELIBERATELY finer-grained than
        # key_guardrail_router.get_key_guardrails()'s GET `source` field. GET's
        # response Literal is frozen to exactly "key" | "tenant" (§3 CONTRACT) — it
        # reports "tenant" whenever the key itself has no override, even if the
        # tenant's own guardrail_configs is empty ({}), because that Literal has no
        # third state to express "nothing configured anywhere". This internal
        # AuthzResult/ApiKey discriminator instead distinguishes an ACTUALLY-populated
        # tenant policy ("tenant") from a genuinely empty one ("none"), which is the
        # more useful attribution for hit-count analytics: "none" means no guardrail
        # config exists at either layer, so there is nothing to attribute a hit to.
        # Not a bug in either place — two different consumers, two different
        # granularities; recorded here rather than silently left to be rediscovered.
        guardrail_policy_source: Literal["key", "tenant", "none"]
        if key_guardrail_policy is not None:
            guardrail_policy_source = "key"
        elif tenant_guardrails:
            guardrail_policy_source = "tenant"
        else:
            guardrail_policy_source = "none"

        effective_semantic_cache = bool(tenant_semantic_cache_enabled or False)
        effective_batch_grouping = bool(tenant_batch_grouping_enabled or False)
        effective_zdr = bool(tenant_zdr_enabled or False)

        # service-tiers (TASK.md §3 M1): key-level override wins; ELSE the tenant
        # default — resolved via the EXISTING LEFT JOIN, zero extra DB reads.
        # tenant_default_tier is None only when the tenant row itself is absent
        # (outerjoin miss) — falls back to the column's own server_default value.
        raw_key_tier = getattr(row, "tier", None)
        resolved_tier: Literal["priority", "standard"]
        tier_source: Literal["key", "tenant"]
        if raw_key_tier is not None:
            resolved_tier = raw_key_tier
            tier_source = "key"
        else:
            resolved_tier = tenant_default_tier or "standard"
            tier_source = "tenant"

        # plan-enforcement (M4): defensive JSONB parse — same dict/str driver quirk this
        # method already guards for on guardrail_configs above.
        raw_pal = plan_model_allowlist_raw
        plan_model_allowlist: list[str] | None
        if raw_pal is None:
            plan_model_allowlist = None
        elif isinstance(raw_pal, list):
            plan_model_allowlist = raw_pal
        elif isinstance(raw_pal, str):
            try:
                parsed_pal = _json.loads(raw_pal)
            except Exception:
                parsed_pal = None
            plan_model_allowlist = parsed_pal if isinstance(parsed_pal, list) else None
        else:
            plan_model_allowlist = None

        return ApiKey(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            key_hash=row.key_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
            monthly_budget_usd=row.monthly_budget_usd,
            soft_budget_usd=row.soft_budget_usd,
            expires_at=row.expires_at,
            model_allowlist=row.model_allowlist,
            rotated_from_key_id=row.rotated_from_key_id,
            rpm_limit=row.rpm_limit,
            tpm_limit=row.tpm_limit,
            team_id=row.team_id,
            team_budget_usd=team_budget_usd,
            cache_enabled=effective_cache,
            guardrail_configs=guardrail_configs,
            semantic_cache_enabled=effective_semantic_cache,
            batch_grouping_enabled=effective_batch_grouping,
            guardrail_policy_source=guardrail_policy_source,
            zdr_enabled=effective_zdr,
            capture_enabled=effective_capture,
            plan_id=plan_id,
            plan_model_allowlist=plan_model_allowlist,
            plan_name=plan_name,
            tier=resolved_tier,
            tier_source=tier_source,
        )

    async def update(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
        cache_enabled: bool | None = None,
        capture_enabled: bool | None = None,
        tier: str | None = None,
        _fields_to_clear: set[str] | None = None,
    ) -> ApiKey | None:
        """Update governance fields on an active (non-revoked) key owned by tenant_id.

        Returns updated ApiKey on success, None when not found/revoked/cross-tenant.
        _fields_to_clear: set of field names to explicitly set to NULL.
        """
        # Fetch the active key for this tenant
        row = (
            await self._session.execute(
                select(ApiKeyRow).where(
                    ApiKeyRow.id == key_id,
                    ApiKeyRow.tenant_id == tenant_id,
                    ApiKeyRow.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        # Apply updates — only fields that were explicitly provided
        clear = _fields_to_clear or set()
        if monthly_budget_usd is not None or "monthly_budget_usd" in clear:
            row.monthly_budget_usd = None if "monthly_budget_usd" in clear else monthly_budget_usd
        if soft_budget_usd is not None or "soft_budget_usd" in clear:
            row.soft_budget_usd = None if "soft_budget_usd" in clear else soft_budget_usd
        if expires_at is not None or "expires_at" in clear:
            row.expires_at = None if "expires_at" in clear else expires_at
        if model_allowlist is not None or "model_allowlist" in clear:
            row.model_allowlist = None if "model_allowlist" in clear else model_allowlist
        if rpm_limit is not None or "rpm_limit" in clear:
            row.rpm_limit = None if "rpm_limit" in clear else rpm_limit
        if tpm_limit is not None or "tpm_limit" in clear:
            row.tpm_limit = None if "tpm_limit" in clear else tpm_limit
        # team_id: absent (not in clear, team_id arg is None) = no change
        #           "team_id" in clear = set to NULL
        #           team_id is not None = set to UUID value
        if team_id is not None or "team_id" in clear:
            row.team_id = None if "team_id" in clear else team_id

        # cache_enabled: None = no change; True/False = set to value
        if cache_enabled is not None:
            row.cache_enabled = cache_enabled

        # capture_enabled: None = no change; True/False = set to value
        if capture_enabled is not None:
            row.capture_enabled = capture_enabled

        # tier: absent (not in clear, tier arg is None) = no change
        #        "tier" in clear = clear the override, revert to tenant default
        #        tier is not None = set the key-level override value
        if tier is not None or "tier" in clear:
            row.tier = None if "tier" in clear else tier

        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_api_key(row)

    async def rotate(
        self,
        *,
        old_key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_key_id: uuid.UUID,
        new_key_hash: str,
        new_name: str,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
    ) -> ApiKey | None:
        """Atomically revoke old key and insert new key in a single transaction.

        Returns the new ApiKey on success, None when old key not found/revoked/cross-tenant.
        Atomicity guarantee: both revoke + insert are committed together (M5/A6).

        Uses begin_nested() (savepoint) so this method is safe to call whether or not
        the session already has an active autobegin transaction.
        """
        # begin_nested() works both when a transaction is already open (uses savepoint)
        # and when none is open yet (starts one). The outer commit() flushes everything.
        async with self._session.begin_nested():
            # Fetch old key — must be active (not revoked) and owned by this tenant
            old_row = (
                await self._session.execute(
                    select(ApiKeyRow).where(
                        ApiKeyRow.id == old_key_id,
                        ApiKeyRow.tenant_id == tenant_id,
                        ApiKeyRow.revoked_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if old_row is None:
                return None

            # Revoke the old key in the same savepoint
            old_row.revoked_at = func.now()

            # Create the new key row
            new_row = ApiKeyRow(
                id=new_key_id,
                tenant_id=tenant_id,
                name=new_name,
                key_hash=new_key_hash,
                monthly_budget_usd=monthly_budget_usd,
                soft_budget_usd=soft_budget_usd,
                expires_at=expires_at,
                model_allowlist=model_allowlist,
                rotated_from_key_id=old_key_id,
            )
            self._session.add(new_row)
            # Savepoint released here — both writes flushed to the outer transaction

        # Commit the outer transaction (includes both the revoke + insert)
        await self._session.commit()

        # Refresh new row to get server-side created_at
        await self._session.refresh(new_row)
        return _row_to_api_key(new_row)
