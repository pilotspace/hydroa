"""Red suite for nonchat-soft-budget-alert (v12).

NonChatGovernance must fire the SAME advisory soft-budget alert the chat path fires
(reusing persist_soft_budget_alert + the alert_events table), so an embeddings/images/
audio request crossing a key's soft_budget_usd writes a soft_budget_exceeded event
identically to chat. The HARD 402 enforcement is UNCHANGED — this adds only the advisory
alert.

Contract: nonchat-soft-budget-alert TASK.md §3 (FROZEN @ v1).

These tests assert behavior (alert scheduled / not scheduled, 402 raised / not raised,
swallowed failure) — not internals.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

from gateway.core.error_catalog import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import ModelAccess

_TENANT = uuid.uuid4()
_KEY = uuid.uuid4()
_MODEL = "openai/gpt-4o-mini"
_SF_SENTINEL = object()  # opaque session_factory; the fake persist never calls it


# --------------------------------------------------------------------------- fakes
class _FakeAuthenticator:
    def __init__(self, authz: AuthzResult) -> None:
        self._authz = authz

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return self._authz


class _FakeModelChecker:
    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        return ModelAccess.ACTIVE


class _FakeRedis:
    def __init__(self, value: str | None) -> None:
        self._value = value

    async def get(self, key: str) -> str | None:
        return self._value


class _FakeBudgetGuard:
    async def check(self, tenant_id: uuid.UUID) -> None:
        return None


def _authz(
    *,
    soft: Decimal | None = None,
    hard: Decimal | None = None,
) -> AuthzResult:
    return AuthzResult(
        tenant_id=_TENANT,
        key_id=_KEY,
        monthly_budget_usd=hard,
        soft_budget_usd=soft,
    )


def _governance(
    authz: AuthzResult,
    *,
    spend: str | None,
    session_factory: Any = _SF_SENTINEL,
) -> NonChatGovernance:
    return NonChatGovernance(
        authenticator=_FakeAuthenticator(authz),
        model_checker=_FakeModelChecker(),
        budget_guard=_FakeBudgetGuard(),
        rate_limiter=None,
        redis_client=_FakeRedis(spend),
        session_factory=session_factory,
    )


@pytest.fixture
def alert_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Capture every persist_soft_budget_alert(...) scheduling without touching a DB."""
    calls: list[tuple[Any, ...]] = []

    async def _fake_persist(
        session_factory: Any,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        soft_budget_usd: Decimal,
        key_spend_usd: Decimal,
    ) -> None:
        calls.append((session_factory, tenant_id, key_id, soft_budget_usd, key_spend_usd))

    monkeypatch.setattr(
        "gateway.usage.application.alert_writer.persist_soft_budget_alert",
        _fake_persist,
    )
    return calls


# --------------------------------------------------------------------------- tests
async def test_soft_crossing_no_hard_budget_fires_alert(
    alert_calls: list[tuple[Any, ...]],
) -> None:
    """soft=10, no hard, spend=12 → alert scheduled (tenant,key,10,12); no raise."""
    gov = _governance(_authz(soft=Decimal("10")), spend="12")

    authz = await gov.authorize("sk-good", _MODEL)
    await asyncio.sleep(0)  # let the fire-and-forget task run

    assert authz.key_id == _KEY
    assert alert_calls == [(_SF_SENTINEL, _TENANT, _KEY, Decimal("10"), Decimal("12"))]


async def test_soft_crossing_with_hard_under_cap_fires_no_402(
    alert_calls: list[tuple[Any, ...]],
) -> None:
    """soft=10, hard=100, spend=12 → alert fires AND authorize does not raise (12<100)."""
    gov = _governance(_authz(soft=Decimal("10"), hard=Decimal("100")), spend="12")

    authz = await gov.authorize("sk-good", _MODEL)
    await asyncio.sleep(0)

    assert authz.key_id == _KEY
    assert len(alert_calls) == 1
    assert alert_calls[0][3:] == (Decimal("10"), Decimal("12"))


async def test_hard_cap_exceeded_still_402(alert_calls: list[tuple[Any, ...]]) -> None:
    """hard=100, spend=120 → BUDGET_EXCEEDED (402); byte-identical to today."""
    gov = _governance(_authz(hard=Decimal("100")), spend="120")

    with pytest.raises(ProblemError) as exc:
        await gov.authorize("sk-good", _MODEL)

    assert exc.value.status == 402
    assert exc.value.code == "ERR_BUDGET_EXCEEDED"


async def test_spend_below_soft_no_alert(alert_calls: list[tuple[Any, ...]]) -> None:
    """soft=10, spend=5 → no alert; authorize proceeds."""
    gov = _governance(_authz(soft=Decimal("10")), spend="5")

    authz = await gov.authorize("sk-good", _MODEL)
    await asyncio.sleep(0)

    assert authz.key_id == _KEY
    assert alert_calls == []


async def test_no_session_factory_skips_alert(alert_calls: list[tuple[Any, ...]]) -> None:
    """soft=10, spend=12, session_factory=None → alert skipped, no crash."""
    gov = _governance(_authz(soft=Decimal("10")), spend="12", session_factory=None)

    authz = await gov.authorize("sk-good", _MODEL)
    await asyncio.sleep(0)

    assert authz.key_id == _KEY
    assert alert_calls == []


async def test_alert_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The alert write raising must NOT fail the request path (fire-and-forget swallow)."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("alert write failed")

    monkeypatch.setattr(
        "gateway.usage.application.alert_writer.persist_soft_budget_alert",
        _boom,
    )
    gov = _governance(_authz(soft=Decimal("10")), spend="12")

    authz = await gov.authorize("sk-good", _MODEL)
    await asyncio.sleep(0)  # drive the failing task; exception must be swallowed

    assert authz.key_id == _KEY  # request path unaffected


async def test_default_session_factory_is_none() -> None:
    """Existing construction (no session_factory kwarg) stays valid → alert disabled."""
    gov = NonChatGovernance(
        authenticator=_FakeAuthenticator(_authz(soft=Decimal("10"))),
        model_checker=_FakeModelChecker(),
        budget_guard=_FakeBudgetGuard(),
        rate_limiter=None,
        redis_client=_FakeRedis("12"),
    )
    # No alert wired → crossing must not crash and must proceed.
    authz = await gov.authorize("sk-good", _MODEL)
    assert authz.key_id == _KEY


def test_deps_wire_session_factory() -> None:
    """embeddings/images/audio deps must build NonChatGovernance with app.state.sessionmaker."""
    import inspect

    from gateway.proxy.api import audio_deps, embeddings_deps, images_deps

    for module in (embeddings_deps, images_deps, audio_deps):
        src = inspect.getsource(module)
        assert "session_factory=request.app.state.sessionmaker" in src, (
            f"{module.__name__} must wire session_factory=request.app.state.sessionmaker"
        )
