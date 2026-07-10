"""Failing-first (RED) suite for guardrail-analytics — verdict RECORDING (TASK.md §4).

Covers the write-side scenarios in §2 SCENARIOS: M1 (non-streaming + streaming
recording, independent of payload capture), M2 (policy_source attribution), M9/R8
(a verdict-write failure never fails the proxied request).

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets:
  - `from gateway.guardrail_analytics...` imports raise ImportError/ModuleNotFoundError
    at collection time (module does not exist yet) — the most emphatic RED signal.
  - guardrail_verdict_events table does not exist -> ProgrammingError on the
    verification SELECT (once imports are bypassed / mocked).

Infrastructure:
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (schema rebuilt per test via the root
    conftest.py `app` fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380 db 9 (exact-cache path)
  - httpx.ASGITransport (no network)
  - FakeCompletionUpstream mirrors tests/guardrails/test_guardrails_core.py's own fake
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    COMPLETIONS,
    INJECTION_CONTENT,
    PII_CONTENT,
    FakeCompletionUpstream,
    assert_problem,
    auth_key,
    completion_payload,
    create_key,
    set_key_guardrails,
    set_tenant_guardrails,
    signup_and_login,
    verdict_rows,
)


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.create_task(record_guardrail_verdicts(...)) task
    complete before the test asserts on the DB (mirrors tests/payload_capture/
    test_payload_capture_store.py's own 0.3s drain convention)."""
    await asyncio.sleep(0.3)


async def _request_log_rows(session: AsyncSession, tenant_id: str) -> list[Any]:
    result = await session.execute(
        text("SELECT id FROM request_logs WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    return list(result.fetchall())


# ===========================================================================
# Scenario 1 — non-streaming pre-call verdict is recorded independent of capture (M1)
# ===========================================================================


async def test_nonstream_verdict_recorded_independent_of_capture(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """A blocked prompt_injection hit writes ONE guardrail_verdict_events row even
    though payload capture is OFF for the key (the default) — the verdict row is
    independent of it, and no request_logs row is ever produced for this call."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Verdict1Co", email="owner@verdict1.io"
    )
    key_info = await create_key(client, jwt, name="v1-key")
    await set_tenant_guardrails(
        client, jwt, {"prompt_injection": {"enabled": True, "mode": "block"}}
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, INJECTION_CONTENT),
        headers=auth_key(key_info["key"]),
    )
    assert_problem(resp, 400, "ERR_GUARDRAIL_BLOCKED")

    await _drain_fire_and_forget()

    rows = await verdict_rows(db_session, tenant_id=tenant_id)
    assert len(rows) == 1, f"expected exactly 1 verdict row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row.guardrail == "prompt_injection"
    assert row.action == "blocked"
    assert str(row.key_id) == key_info["key_id"]

    capture_rows = await _request_log_rows(db_session, tenant_id)
    assert capture_rows == [], (
        f"capture is OFF for this key — expected 0 request_logs rows, got {len(capture_rows)}"
    )


# ===========================================================================
# Scenario 2 — streaming pre-call verdict is recorded (closes the metrics gap) (M1)
# ===========================================================================


async def test_stream_verdict_recorded_closes_metrics_gap(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """An audited pii_mask hit on a STREAMING request writes ONE guardrail_verdict_events
    row — parity holds even though the streaming path never fires the Prometheus
    guardrail_events_total counter (a separate, pre-existing gap this task does not
    touch)."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Verdict2Co", email="owner@verdict2.io"
    )
    key_info = await create_key(client, jwt, name="v2-key")
    await set_tenant_guardrails(client, jwt, {"pii_mask": {"enabled": True, "mode": "audit"}})

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    async with client.stream(
        "POST",
        COMPLETIONS,
        json=completion_payload(active_model, PII_CONTENT, stream=True),
        headers=auth_key(key_info["key"]),
    ) as resp:
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        async for _ in resp.aiter_bytes():
            pass

    await _drain_fire_and_forget()

    rows = await verdict_rows(db_session, tenant_id=tenant_id)
    assert len(rows) == 1, f"expected exactly 1 verdict row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row.guardrail == "pii_mask"
    assert row.action == "audited"


# ===========================================================================
# Scenario 3 — recorded row carries policy_source from the resolved AuthzResult (M2)
# ===========================================================================


async def test_policy_source_recorded_from_authz_result(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """A key with its own guardrail_policy override records policy_source="key" on its
    hit; a sibling key with no override records policy_source="tenant" on its own hit."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Verdict3Co", email="owner@verdict3.io"
    )
    await set_tenant_guardrails(
        client, jwt, {"prompt_injection": {"enabled": True, "mode": "block"}}
    )

    key_override = await create_key(client, jwt, name="v3-key-override")
    await set_key_guardrails(
        client,
        jwt,
        key_override["key_id"],
        {"prompt_injection": {"enabled": True, "mode": "block"}},
    )
    key_inherited = await create_key(client, jwt, name="v3-key-inherited")

    upstream_a = FakeCompletionUpstream()
    app.state.completion_upstream = upstream_a
    resp_a = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, INJECTION_CONTENT),
        headers=auth_key(key_override["key"]),
    )
    assert resp_a.status_code == 400

    upstream_b = FakeCompletionUpstream()
    app.state.completion_upstream = upstream_b
    resp_b = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, INJECTION_CONTENT),
        headers=auth_key(key_inherited["key"]),
    )
    assert resp_b.status_code == 400

    await _drain_fire_and_forget()

    rows = await verdict_rows(db_session, tenant_id=tenant_id)
    assert len(rows) == 2, f"expected 2 verdict rows, got {len(rows)}: {rows}"
    by_key = {str(r.key_id): r for r in rows}
    assert by_key[key_override["key_id"]].policy_source == "key"
    assert by_key[key_inherited["key_id"]].policy_source == "tenant"


# ===========================================================================
# Scenario — a verdict-write failure never fails the proxied request (M9, R8)
# ===========================================================================


async def test_verdict_write_failure_never_fails_proxied_request(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the guardrail-verdict recorder raises, the proxied completion still
    succeeds/fails purely on its own merits (structural fire-and-forget guarantee —
    the exception is swallowed at the asyncio.ensure_future done-callback, never
    propagated to the request), and no verdict row is written for that call."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Verdict4Co", email="owner@verdict4.io"
    )
    key_info = await create_key(client, jwt, name="v4-key")
    await set_tenant_guardrails(client, jwt, {"pii_mask": {"enabled": True, "mode": "audit"}})

    async def _raising_recorder(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated guardrail_verdict_events INSERT failure")

    monkeypatch.setattr(
        "gateway.proxy.application.use_cases.record_guardrail_verdicts",
        _raising_recorder,
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, PII_CONTENT),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200, (
        f"a verdict-write failure must never fail the proxied request; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert upstream.calls == 1

    await _drain_fire_and_forget()

    rows = await verdict_rows(db_session, tenant_id=tenant_id)
    assert rows == [], f"the failed write must leave no verdict row, got {len(rows)}"


async def test_record_guardrail_verdicts_unit_swallows_db_failure() -> None:
    """Unit-level (no HTTP): record_guardrail_verdicts itself must never raise, even
    when its own session/commit blows up — mirrors tests/payload_capture/
    test_payload_capture_store.py's test_capture_store_outage_never_affects_proxied_response
    convention exactly."""
    from gateway.guardrail_analytics.application.verdict_recorder import (
        record_guardrail_verdicts,
    )
    from gateway.proxy.domain.entities import GuardrailEvent

    class _RaisingSession:
        async def __aenter__(self) -> _RaisingSession:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def add_all(self, _objs: Any) -> None:
            pass

        async def commit(self) -> None:
            raise RuntimeError("simulated DB commit failure")

    def _raising_session_factory() -> _RaisingSession:
        return _RaisingSession()

    # Must NEVER raise.
    await record_guardrail_verdicts(
        _raising_session_factory,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        team_id=None,
        policy_source="tenant",
        events=[GuardrailEvent(guardrail="pii_mask", action="audited", detail="")],
    )
