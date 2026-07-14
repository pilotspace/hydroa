"""Shared fixtures for the claude-gateway-protocol-compat red/green suite (TASK.md §4).

Reuses the sibling anthropic-messages-ingress suite's fixture helpers (auth_bearer,
anthropic_payload, FakeCompletionUpstream, signup_and_login) rather than duplicating
them, and the root conftest's app/client/db_session (real Postgres, fresh schema per
test). `settings` is overridden HERE (not in root conftest) to pre-configure a
claude-branded model_groups alias — needed by the M1 discovery and M8 failover-gate
tests; harmless no-op for every other test in this module (an unused alias key).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from tests import _redis_env
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET

# Re-exported for test modules — mirrors the sibling suite's own conftest surface.
# `api_key` is a @pytest.fixture; importing it into this module's namespace makes it
# an active fixture for every test under this directory (standard pytest technique).
from tests.anthropic_messages_ingress.conftest import (  # noqa: F401
    FakeCompletionUpstream,
    anthropic_payload,
    api_key,
    auth_bearer,
    auth_jwt,
    signup_and_login,
)

#: A Claude-branded alias whose ONLY configured candidate is non-Anthropic — the exact
#: M8/R3 precondition ("whose only available candidates ... are non-Anthropic").
CLAUDE_ALIAS = "claude-sonnet-4-6"
NON_ANTHROPIC_CANDIDATE = "openrouter/haiku-substitute"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # type: ignore[call-arg]
        model_groups={CLAUDE_ALIAS: [NON_ANTHROPIC_CANDIDATE]},
    )


async def _insert_model(
    db_session: AsyncSession, model_id: str, *, provider: str = "openrouter"
) -> str:
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, provider)"
            " VALUES (:i, :n, 128000, true, :p) ON CONFLICT (id) DO UPDATE SET provider = :p"
        ),
        {"i": model_id, "n": model_id, "p": provider},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    return await _insert_model(db_session, "openai/gpt-4o", provider="openrouter")


@pytest.fixture
async def anthropic_model(db_session: AsyncSession) -> str:
    return await _insert_model(db_session, "anthropic/claude-opus-4", provider="anthropic")


async def set_allow_non_claude_failover(
    db_session: AsyncSession, tenant_id: str, *, value: bool
) -> None:
    """Directly flip the new per-tenant flag — no management endpoint is in this
    task's Musts (M8 names only the flag's effect, not a PUT surface)."""
    await db_session.execute(
        text("UPDATE tenants SET allow_non_claude_failover = :v WHERE id = :t"),
        {"v": value, "t": tenant_id},
    )
    await db_session.commit()


class FakeUsageRecorder:
    """Captures every kwarg record() receives (incl. the new cc_* extras) —
    mirrors the sibling suite's own FakeUsageRecorder but keeps **_kwargs, not
    discarding them, so M4's raw-key assertions can inspect what was passed."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, Any] | None,
        status: int,
        **kwargs: Any,
    ) -> None:
        self.records.append(
            {
                "tenant_id": tenant_id,
                "key_id": key_id,
                "model": model,
                "usage": usage,
                "status": status,
                **kwargs,
            }
        )

    # Typed-extras seam (proxy/domain/ports.py) — declares every extra this fake
    # accepts, mirroring RecordingUsageRecorder.supported_extras exactly, so
    # _dispatch_record forwards the SAME kwargs a real recorder would receive.
    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
            "usage_source",
            "provider_generation_id",
            "disconnect_estimate",
            "request_id",
            "tags",
            "tier_served",
            "tier_capacity_degraded",
            "cc_session_id",
            "cc_agent_id",
            "cc_parent_agent_id",
        }
    )
