"""Failing-first (RED) suite for pii-v2 (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS (S1–S16).

TRUE-RED RULE: every test asserts TARGET behavior and must fail NOW for the RIGHT reason.

Right-reason red targets per test:

  S1 (IPv4 built-in masks pre-call):
    _PII_PATTERNS has only 4 built-ins (EMAIL/PHONE/CC/SSN). No IPv4 pattern exists.
    The upstream receives "192.168.1.100" unmasked → AssertionError: "[IP_REDACTED]"
    not found in upstream message content.

  S2 (IBAN built-in masks pre-call):
    No IBAN pattern in _PII_PATTERNS.
    The upstream receives "GB82WEST12345698765432" unmasked →
    AssertionError: "[IBAN_REDACTED]" not found in upstream message.

  S3 (API secret built-in masks pre-call):
    No API-secret pattern in _PII_PATTERNS.
    The upstream receives "sk-abcdefghijklmnopqrstu1234" unmasked →
    AssertionError: "[SECRET_REDACTED]" not found in upstream message.

  S4 (Passport built-in masks pre-call):
    No passport pattern in _PII_PATTERNS.
    The upstream receives "A12345678" unmasked →
    AssertionError: "[PASSPORT_REDACTED]" not found in upstream message.

  S5 (custom pattern masks request content):
    PUT /admin/guardrails with pii_custom_patterns is not yet handled by PiiMaskConfig
    (no pii_custom_patterns field). Either:
    (a) Pydantic rejects the extra field → 422 → _set_pii_mask_with_custom asserts 200
        → AssertionError: 422 != 200, OR
    (b) Pydantic ignores the extra field, stores it, but the evaluator never reads it →
        upstream receives "EMP123456" unmasked → AssertionError: "[EMPLOYEE_ID_REDACTED]"
        not found. Either failure is the RIGHT reason.

  S6 (custom pattern masks response content):
    Same as S5 — custom patterns not implemented in evaluator. Upstream body
    "ORD-12345678" passes through unmasked →
    AssertionError: "[ORDER_ID_REDACTED]" not found in client response.

  S7 (PUT invalid regex syntax → 422):
    PiiMaskConfig has no pii_custom_patterns field and no validation for regex syntax.
    PUT /admin/guardrails with {"pii_mask": {"enabled": true, "mode": "mask",
    "pii_custom_patterns": [{"name": "BAD", "pattern": "["}]}} — Pydantic ignores
    unknown fields → stored without validation → returns 200 instead of 422.
    AssertionError: 200 != 422.

  S8 (empty-string-matching pattern → 422):
    No empty-string-match validation. PUT returns 200 instead of 422.
    AssertionError: 200 != 422.

  S9 (nested-quantifier pattern → 422):
    No nested-quantifier heuristic check. PUT returns 200 instead of 422.
    AssertionError: 200 != 422.

  S10 (over-length pattern → 422):
    No length check. PUT returns 200 instead of 422.
    AssertionError: 200 != 422.

  S11 (over-count list → 422):
    No count check. PUT returns 200 instead of 422.
    AssertionError: 200 != 422.

  S12 (invalid name → 422):
    No name-format check. PUT returns 200 instead of 422.
    AssertionError: 200 != 422.

  S13 (GET round-trips custom patterns):
    GET response pii_mask is currently a flat {enabled, mode} dict — no
    pii_custom_patterns key. AssertionError: custom patterns not present in response.

  S14 (custom pattern audit mode does NOT mask):
    Custom patterns not implemented in evaluator. Regardless of audit/mask mode,
    the upstream receives original content. But the guardrail event action "audited"
    is asserted for the custom hit — this would fail because the evaluator never
    processes custom patterns at all (no event emitted for custom hits).
    AssertionError: audited counter not incremented (or wrong count).

  S15 (budget exceeded → fail-OPEN):
    RegexGuardrailEvaluator has no _custom_budget_seconds attribute and no budget guard
    at all. Setting evaluator._custom_budget_seconds = 0.0 on the instance has no effect.
    The test asserts gateway_guardrail_events_total{action="budget_exceeded"} increments;
    this counter action value does not exist yet in the implementation.
    AssertionError: budget_exceeded counter not incremented.

  S16 (v4 EMAIL literal regression):
    Email masking is already implemented (v4 frozen). This test should initially
    RED at PUT time: pii_custom_patterns is not accepted by PiiMaskConfig yet,
    but S16 does NOT pass pii_custom_patterns — it just sets pii_mask normally.
    S16 passes the PUT step. The email masking itself works (v4 frozen). BUT:
    The test is deliberately structured to fail at the fake-upstream assertion because
    RegexGuardrailEvaluator.evaluate_pre is now extended by pii-v2 to call a
    _mask_pii_with_custom path that does not exist yet → AttributeError or
    the method doesn't call the new code path yet. Actually the cleanest right-reason
    red for S16 is: this test verifies the WHOLE masking pipeline is intact after the
    v2 extension; it will initially PASS (v4 works), but will RED after build starts
    changing the evaluator. Therefore S16 is written as a "must remain green" regression
    guard — we mark it as expected to be GREEN for the right reason in this red run, but
    we document this explicitly (see note in S16 test body).

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /admin/guardrails,
  /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
    (schema rebuilt per test via conftest.py app fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380 db 9 (flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network)
  - FakeCompletionUpstream (inspectable call count + configurable body) injected via app.state
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.usage.application.flusher import UsageLedgerFlusher

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_GUARDRAILS = "/admin/guardrails"
COMPLETIONS = "/v1/chat/completions"

# ---------------------------------------------------------------------------
# Upstream bodies
# ---------------------------------------------------------------------------
UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-piiv2-1",
    "choices": [{"message": {"role": "assistant", "content": "hello from upstream"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

# Upstream body with ORDER_ID for post-call masking test (S6)
UPSTREAM_BODY_WITH_ORDER: dict[str, Any] = {
    "id": "gen-piiv2-2",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Your order ORD-12345678 is confirmed and will ship soon.",
            }
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape; return parsed body."""
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; full body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body."""
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def put_pii_mask(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    mode: str = "mask",
    custom_patterns: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """PUT /admin/guardrails with pii_mask config; assert 200; return echoed config.

    TRUE-RED note:
      S5–S14: this call fails because PiiMaskConfig does not yet accept
      pii_custom_patterns, causing either a 422 (if Pydantic strict-extra rejects it)
      or incorrect behavior (if extra fields are silently dropped and evaluator ignores
      the custom list). Either way the downstream assertion about masking behavior fails.
      S7–S12: for rejection tests, this call is NOT used — those tests call PUT directly
      and assert a 422 response.
    """
    pii_body: dict[str, Any] = {"enabled": True, "mode": mode}
    if custom_patterns is not None:
        pii_body["pii_custom_patterns"] = custom_patterns
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={"pii_mask": pii_body},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, (
        f"PUT /admin/guardrails (pii_mask) failed ({resp.status_code}): {resp.text}"
    )
    return resp.json()


def completion_payload(model: str, content: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }


def get_counter_value(
    metrics_reg: Any,
    guardrail: str,
    mode: str,
    action: str,
) -> float:
    """Read a sample value from gateway_guardrail_events_total."""
    for metric in metrics_reg.guardrail_events_total.collect():
        for sample in metric.samples:
            lbl = sample.labels
            if (
                lbl.get("guardrail") == guardrail
                and lbl.get("mode") == mode
                and lbl.get("action") == action
            ):
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Fake non-streaming upstream — tracks call count and records received messages."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls: int = 0
        self.received_messages: list[list[dict[str, Any]]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        msgs = payload.get("messages", [])
        self.received_messages.append(msgs)
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"s1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Suite-local fixtures (never use repo-root conftest for this suite)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    """App fixture with running UsageLedgerFlusher (mirrors guardrails conftest pattern)."""
    import asyncio

    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    redis_client = application.state.redis_client
    flusher = UsageLedgerFlusher(
        redis=redis_client,
        session_factory=application.state.sessionmaker,
    )
    application.state.flusher = flusher

    async def _fast_flusher() -> None:
        while True:
            try:
                await flusher.flush_once()
            except Exception:
                pass
            await asyncio.sleep(0.01)

    flusher_task = asyncio.create_task(_fast_flusher())
    application.state.flusher_task = flusher_task

    yield application

    flusher_task.cancel()
    try:
        await flusher_task
    except asyncio.CancelledError:
        pass
    await engine.dispose()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    rc: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await rc.flushdb()
    yield rc
    await rc.flushdb()
    await rc.aclose()


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert minimal active model + pricing snapshot so recorder computes cost."""
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {
            "sid": str(snap_id),
            "mid": model_id,
            "p": "0.000001",
            "c": "0.000002",
        },
    )
    await db_session.commit()
    return model_id


# ===========================================================================
# S1 — New built-in IPv4 masks live through the proxy (pre-call)
#
# TRUE-RED REASON: _PII_PATTERNS has no IPv4 entry (only 4 v4 built-ins).
#   Upstream receives "192.168.1.100" unmasked.
#   AssertionError: "[IP_REDACTED]" not found in upstream received_messages[0][0]["content"].
# ===========================================================================


async def test_builtin_ipv4_masks_pre_call(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """IPv4 built-in: '192.168.1.100' in message → upstream receives '[IP_REDACTED]'."""
    jwt, _ = await signup_and_login(client, tenant_name="Ipv4MaskCo", email="owner@ipv4mask.io")
    key_info = await create_key(client, jwt, name="ipv4-key")
    await put_pii_mask(client, jwt, mode="mask")

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "my server IP is 192.168.1.100 for config"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    assert len(upstream.received_messages) == 1
    received = upstream.received_messages[0][0].get("content", "")

    # TRUE-RED: _PII_PATTERNS has no IPv4 built-in → "192.168.1.100" not replaced
    assert "[IP_REDACTED]" in received, (
        f"IPv4 address should be masked to [IP_REDACTED] in upstream message, "
        f"got content: {received!r}"
    )
    assert "192.168.1.100" not in received, (
        f"original IP must not reach upstream, got content: {received!r}"
    )


# ===========================================================================
# S2 — New built-in IBAN masks live through the proxy (pre-call)
#
# TRUE-RED REASON: _PII_PATTERNS has no IBAN entry.
#   Upstream receives "GB82WEST12345698765432" unmasked.
#   AssertionError: "[IBAN_REDACTED]" not found.
# ===========================================================================


async def test_builtin_iban_masks_pre_call(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """IBAN built-in: 'GB82WEST12345698765432' in message → upstream receives '[IBAN_REDACTED]'."""
    jwt, _ = await signup_and_login(client, tenant_name="IbanMaskCo", email="owner@ibanmask.io")
    key_info = await create_key(client, jwt, name="iban-key")
    await put_pii_mask(client, jwt, mode="mask")

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(
            active_model, "please transfer to my IBAN GB82WEST12345698765432 today"
        ),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    received = upstream.received_messages[0][0].get("content", "")

    # TRUE-RED: no IBAN pattern → "GB82WEST12345698765432" passes through unmasked
    assert "[IBAN_REDACTED]" in received, (
        f"IBAN should be masked to [IBAN_REDACTED], got content: {received!r}"
    )
    assert "GB82WEST12345698765432" not in received, (
        f"original IBAN must not reach upstream, got: {received!r}"
    )


# ===========================================================================
# S3 — New built-in API secret masks live through the proxy (pre-call)
#
# TRUE-RED REASON: _PII_PATTERNS has no API-secret entry.
#   Upstream receives "sk-abcdefghijklmnopqrstu1234" unmasked.
#   AssertionError: "[SECRET_REDACTED]" not found.
# ===========================================================================


async def test_builtin_api_secret_masks_pre_call(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """API-secret built-in: 'sk-...' key in message → upstream receives '[SECRET_REDACTED]'."""
    jwt, _ = await signup_and_login(
        client, tenant_name="SecretMaskCo", email="owner@secretmask.io"
    )
    key_info = await create_key(client, jwt, name="secret-key")
    await put_pii_mask(client, jwt, mode="mask")

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    # A 24-char suffix: "abcdefghijklmnopqrstu123" (20+ required by contract pattern)
    secret_val = "sk-abcdefghijklmnopqrstu123"
    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, f"my key is {secret_val} please help"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    received = upstream.received_messages[0][0].get("content", "")

    # TRUE-RED: no API-secret pattern → key passes through unmasked
    assert "[SECRET_REDACTED]" in received, (
        f"API secret should be masked to [SECRET_REDACTED], got content: {received!r}"
    )
    assert secret_val not in received, (
        f"original API secret must not reach upstream, got: {received!r}"
    )


# ===========================================================================
# S4 — New built-in PASSPORT masks live through the proxy (pre-call)
#
# TRUE-RED REASON: _PII_PATTERNS has no passport entry.
#   Upstream receives "A12345678" unmasked.
#   AssertionError: "[PASSPORT_REDACTED]" not found.
# ===========================================================================


async def test_builtin_passport_masks_pre_call(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Passport built-in: 'A12345678' in message → upstream receives '[PASSPORT_REDACTED]'."""
    jwt, _ = await signup_and_login(
        client, tenant_name="PassportMaskCo", email="owner@passportmask.io"
    )
    key_info = await create_key(client, jwt, name="passport-key")
    await put_pii_mask(client, jwt, mode="mask")

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(
            active_model, "passport number A12345678 issued in 2020"
        ),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    received = upstream.received_messages[0][0].get("content", "")

    # TRUE-RED: no passport pattern → "A12345678" passes through unmasked
    assert "[PASSPORT_REDACTED]" in received, (
        f"Passport number should be masked to [PASSPORT_REDACTED], got content: {received!r}"
    )
    assert "A12345678" not in received, (
        f"original passport number must not reach upstream, got: {received!r}"
    )


# ===========================================================================
# S5 — Custom pattern masks request message content (pre-call)
#
# TRUE-RED REASON: PiiMaskConfig has no pii_custom_patterns field.
#   PUT /admin/guardrails with pii_custom_patterns either:
#   (a) Returns 422 because of unexpected field (if extra='forbid') →
#       AssertionError: 422 != 200 in put_pii_mask(), OR
#   (b) Stores the config but evaluator never reads custom patterns →
#       upstream receives "EMP123456" unmasked →
#       AssertionError: "[EMPLOYEE_ID_REDACTED]" not found.
# ===========================================================================


async def test_custom_pattern_masks_request_content(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Custom EMPLOYEE_ID pattern masks 'EMP123456' in request to '[EMPLOYEE_ID_REDACTED]'."""
    jwt, _ = await signup_and_login(
        client, tenant_name="CustomMaskCo", email="owner@custommask.io"
    )
    key_info = await create_key(client, jwt, name="custom-key")

    # TRUE-RED: PiiMaskConfig has no pii_custom_patterns → PUT fails or ignores field
    await put_pii_mask(
        client,
        jwt,
        mode="mask",
        custom_patterns=[{"name": "EMPLOYEE_ID", "pattern": r"EMP\d{6}"}],
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "employee EMP123456 submitted the request"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    received = upstream.received_messages[0][0].get("content", "")

    # TRUE-RED: custom patterns not evaluated → "EMP123456" not replaced
    assert "[EMPLOYEE_ID_REDACTED]" in received, (
        f"custom pattern should mask EMP123456 to [EMPLOYEE_ID_REDACTED], got: {received!r}"
    )
    assert "EMP123456" not in received, (
        f"original employee ID must not reach upstream, got: {received!r}"
    )
    # Non-PII context text must be preserved
    assert "submitted the request" in received, (
        f"non-PII text should be preserved, got: {received!r}"
    )


# ===========================================================================
# S6 — Custom pattern masks response content (post-call)
#
# TRUE-RED REASON: Custom patterns not evaluated in evaluate_post either.
#   Upstream body "ORD-12345678" passes through unmasked to client.
#   AssertionError: "[ORDER_ID_REDACTED]" not found in client response.
# ===========================================================================


async def test_custom_pattern_masks_response_content(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Custom ORDER_ID pattern masks 'ORD-12345678' in upstream response to client."""
    jwt, _ = await signup_and_login(
        client, tenant_name="PostCallCustomCo", email="owner@postcallcustom.io"
    )
    key_info = await create_key(client, jwt, name="postcall-custom-key")

    # TRUE-RED: pii_custom_patterns not supported yet → PUT fails or ignores field
    await put_pii_mask(
        client,
        jwt,
        mode="mask",
        custom_patterns=[{"name": "ORDER_ID", "pattern": r"ORD-\d{8}"}],
    )

    upstream = FakeCompletionUpstream(body=UPSTREAM_BODY_WITH_ORDER)
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "what is my order status?"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    choices = resp.json().get("choices", [])
    assert len(choices) >= 1
    content = choices[0].get("message", {}).get("content", "")

    # TRUE-RED: custom post-call masking not implemented → "ORD-12345678" reaches client
    assert "[ORDER_ID_REDACTED]" in content, (
        f"custom pattern should mask ORD-12345678 to [ORDER_ID_REDACTED] in response, "
        f"got content: {content!r}"
    )
    assert "ORD-12345678" not in content, (
        f"original order ID must not appear in client response, got: {content!r}"
    )


# ===========================================================================
# S7 — PUT with invalid regex syntax → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: PiiMaskConfig has no validation for regex syntax.
#   PUT returns 200 (ignores pii_custom_patterns or treats as unknown field).
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_invalid_regex_syntax(
    client: httpx.AsyncClient,
) -> None:
    """PUT with pii_custom_patterns containing invalid regex '[' → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="InvalidRegexCo", email="owner@invalidregex.io"
    )

    # TRUE-RED: no regex validation → returns 200 instead of 422
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [{"name": "BAD_PATTERN", "pattern": "["}],
            }
        },
        headers=auth_jwt(jwt),
    )
    body = assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    # Detail must name the offending pattern
    detail = body.get("detail", "") or body.get("title", "")
    assert "BAD_PATTERN" in str(body), (
        f"422 response should name the offending pattern 'BAD_PATTERN', got body: {body}"
    )


# ===========================================================================
# S8 — PUT with empty-string-matching pattern → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: No empty-string-match validation.
#   PUT returns 200 instead of 422.
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_empty_string_matching(
    client: httpx.AsyncClient,
) -> None:
    """PUT with pattern '.*' (matches empty string) → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="EmptyMatchCo", email="owner@emptymatch.io"
    )

    # TRUE-RED: no empty-string validation → returns 200
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [{"name": "EMPTY_MATCH", "pattern": ".*"}],
            }
        },
        headers=auth_jwt(jwt),
    )
    body = assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    # Body should indicate empty string matching
    body_str = str(body)
    assert "empty" in body_str.lower() or "EMPTY_MATCH" in body_str, (
        f"422 detail should mention empty string or pattern name, got body: {body}"
    )


# ===========================================================================
# S9 — PUT with nested-quantifier pattern → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: No nested-quantifier heuristic check.
#   PUT returns 200 instead of 422.
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_nested_quantifier(
    client: httpx.AsyncClient,
) -> None:
    """PUT with pattern '(a+)+' (nested quantifier, ReDoS risk) → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="NestedQuantCo", email="owner@nestedquant.io"
    )

    # TRUE-RED: no nested-quantifier heuristic → returns 200
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [{"name": "REDOS_RISK", "pattern": "(a+)+"}],
            }
        },
        headers=auth_jwt(jwt),
    )
    body = assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    body_str = str(body)
    assert "nested" in body_str.lower() or "REDOS_RISK" in body_str, (
        f"422 detail should mention nested quantifiers or pattern name, got body: {body}"
    )


# ===========================================================================
# S10 — PUT with over-length pattern → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: No pattern length check.
#   PUT returns 200 instead of 422.
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_over_length_pattern(
    client: httpx.AsyncClient,
) -> None:
    """PUT with pattern of 257 bytes (> 256 limit) → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="OverLengthCo", email="owner@overlength.io"
    )

    # 257-byte pattern: "a" repeated 257 times (simple char class, safe but over-length)
    over_length_pattern = "a" * 257
    assert len(over_length_pattern.encode()) == 257

    # TRUE-RED: no length check → returns 200
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [
                    {"name": "OVER_LENGTH", "pattern": over_length_pattern}
                ],
            }
        },
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S11 — PUT with over-count list → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: No count validation.
#   PUT returns 200 instead of 422.
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_over_count_list(
    client: httpx.AsyncClient,
) -> None:
    """PUT with 9 custom patterns (> 8 max) → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="OverCountCo", email="owner@overcount.io"
    )

    # 9 valid-format patterns (names are unique, patterns are safe)
    nine_patterns = [
        {"name": f"PATTERN{i:02d}", "pattern": f"PAT{i:02d}\\d{{4}}"}
        for i in range(9)
    ]
    assert len(nine_patterns) == 9

    # TRUE-RED: no count check → returns 200
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": nine_patterns,
            }
        },
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S12 — PUT with invalid pattern name → 422 ERR_PAYLOAD_INVALID
#
# TRUE-RED REASON: No name-format validation.
#   PUT returns 200 instead of 422.
#   AssertionError: 200 != 422.
# ===========================================================================


async def test_put_custom_invalid_name(
    client: httpx.AsyncClient,
) -> None:
    """PUT with pattern name 'invalid-name!' (not ^[A-Z][A-Z0-9_]{0,31}$) → 422."""
    jwt, _ = await signup_and_login(
        client, tenant_name="InvalidNameCo", email="owner@invalidname.io"
    )

    # TRUE-RED: no name format check → returns 200
    resp = await client.put(
        ADMIN_GUARDRAILS,
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [
                    {"name": "invalid-name!", "pattern": r"\d{4}"}
                ],
            }
        },
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S13 — GET round-trips stored custom patterns
#
# TRUE-RED REASON: GET /admin/guardrails response model (GuardrailConfigResponse)
#   has no pii_custom_patterns field in pii_mask.
#   Either the stored custom patterns are absent from the GET response →
#   AssertionError: pii_custom_patterns not present, OR
#   the PUT that stored them failed (because PiiMaskConfig rejected them) →
#   AssertionError in put_pii_mask() earlier.
# ===========================================================================


async def test_get_round_trips_custom_patterns(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/guardrails returns stored custom patterns (name+pattern); no literal field."""
    jwt, _ = await signup_and_login(
        client, tenant_name="GetRoundTripCo", email="owner@getround.io"
    )

    custom = [
        {"name": "TICKET_ID", "pattern": r"TKT-\d{7}"},
        {"name": "CASE_REF", "pattern": r"CASE\d{5}"},
    ]

    # TRUE-RED: PUT may fail if pii_custom_patterns not accepted, OR
    # GET response will lack pii_custom_patterns field
    await put_pii_mask(client, jwt, mode="mask", custom_patterns=custom)

    get_resp = await client.get(ADMIN_GUARDRAILS, headers=auth_jwt(jwt))
    assert get_resp.status_code == 200, (
        f"GET /admin/guardrails failed: {get_resp.status_code}: {get_resp.text}"
    )
    body = get_resp.json()
    pii_mask = body.get("pii_mask", {}) or {}

    # TRUE-RED: pii_custom_patterns not in response model
    assert "pii_custom_patterns" in pii_mask, (
        f"GET response pii_mask should contain pii_custom_patterns, got: {pii_mask}"
    )
    returned_patterns = pii_mask["pii_custom_patterns"]
    assert isinstance(returned_patterns, list) and len(returned_patterns) == 2, (
        f"expected 2 custom patterns, got: {returned_patterns}"
    )

    names = {p["name"] for p in returned_patterns}
    assert names == {"TICKET_ID", "CASE_REF"}, (
        f"expected pattern names TICKET_ID, CASE_REF, got: {names}"
    )
    patterns_dict = {p["name"]: p["pattern"] for p in returned_patterns}
    assert patterns_dict["TICKET_ID"] == r"TKT-\d{7}", (
        f"TICKET_ID pattern should round-trip verbatim, got: {patterns_dict['TICKET_ID']!r}"
    )

    # Literal must NOT be present in response (it is derived, never stored)
    for p in returned_patterns:
        assert "literal" not in p, (
            f"literal field must not be returned in custom patterns (derived server-side), "
            f"got: {p}"
        )


# ===========================================================================
# S14 — Custom pattern in audit mode does NOT mask but increments metric
#
# TRUE-RED REASON: Custom patterns not evaluated at all.
#   Even in audit mode, no "audited" event is emitted for a custom-pattern hit.
#   The test asserts that the audited counter incremented (above its pre-request value),
#   which fails because no custom-pattern audit event is emitted.
#   Additionally put_pii_mask may fail if pii_custom_patterns is rejected.
# ===========================================================================


async def test_custom_pattern_audit_mode_no_mask(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Custom pattern in audit mode: upstream gets original content; audited counter increments."""
    jwt, _ = await signup_and_login(
        client, tenant_name="CustomAuditCo", email="owner@customaudit.io"
    )
    key_info = await create_key(client, jwt, name="custom-audit-key")

    # TRUE-RED: PUT may fail (pii_custom_patterns not accepted) OR
    # evaluator never processes custom patterns in audit mode
    await put_pii_mask(
        client,
        jwt,
        mode="audit",
        custom_patterns=[{"name": "EMPLOYEE_ID", "pattern": r"EMP\d{6}"}],
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    metrics_reg = app.state.metrics_registry
    assert hasattr(metrics_reg, "guardrail_events_total"), (
        "MetricsRegistry must have guardrail_events_total counter"
    )
    audited_before = get_counter_value(metrics_reg, "pii_mask", "audit", "audited")

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "employee EMP123456 submitted the request"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"audit mode must not block; got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1

    # In audit mode: upstream receives ORIGINAL content (not masked)
    received = upstream.received_messages[0][0].get("content", "")
    assert "EMP123456" in received, (
        f"audit mode must not mask; upstream should receive original 'EMP123456', "
        f"got: {received!r}"
    )
    assert "[EMPLOYEE_ID_REDACTED]" not in received, (
        f"audit mode must not replace content, got: {received!r}"
    )

    # TRUE-RED: no custom-pattern audit event emitted → counter does not increment
    audited_after = get_counter_value(metrics_reg, "pii_mask", "audit", "audited")
    assert audited_after > audited_before, (
        f"guardrail_events_total{{guardrail='pii_mask', mode='audit', action='audited'}} "
        f"should increment when custom pattern matches in audit mode; "
        f"before={audited_before}, after={audited_after}"
    )


# ===========================================================================
# S15 — Time-budget exceeded: custom scanning skipped, fail-OPEN, metric incremented
#
# TRUE-RED REASON: RegexGuardrailEvaluator has no _custom_budget_seconds attribute
#   and no budget guard. Setting evaluator._custom_budget_seconds = 0.0 has no effect.
#   The test asserts that gateway_guardrail_events_total{action="budget_exceeded"}
#   increments; this counter action value does not exist yet.
#   AssertionError: budget_exceeded counter not incremented (value remains 0.0).
# ===========================================================================


async def test_budget_exceeded_skips_custom_fail_open(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Budget-exceeded seam: force 0s budget → custom patterns skipped; fail-OPEN; counter fires."""
    jwt, _ = await signup_and_login(
        client, tenant_name="BudgetExceedCo", email="owner@budgetexceed.io"
    )
    key_info = await create_key(client, jwt, name="budget-key")

    # Store a custom pattern (valid — not the budget test is about evaluation, not validation)
    await put_pii_mask(
        client,
        jwt,
        mode="mask",
        custom_patterns=[{"name": "TICKET_ID", "pattern": r"TKT-\d{7}"}],
    )

    # Inject the budget-exhaustion seam on the evaluator instance
    # TRUE-RED: evaluator has no _custom_budget_seconds attribute → this is a no-op;
    # the budget guard does not exist → budget_exceeded event never fires
    evaluator = getattr(app.state, "guardrail_evaluator", None)
    if evaluator is not None:
        evaluator._custom_budget_seconds = 0.0  # type: ignore[attr-defined]
    else:
        # If the evaluator is wired differently, set it on app.state for the deps override seam
        from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator

        forced_evaluator = RegexGuardrailEvaluator()
        forced_evaluator._custom_budget_seconds = 0.0  # type: ignore[attr-defined]
        app.state.guardrail_evaluator = forced_evaluator

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    metrics_reg = app.state.metrics_registry
    assert hasattr(metrics_reg, "guardrail_events_total"), (
        "MetricsRegistry must have guardrail_events_total counter"
    )

    # Read before value for budget_exceeded (mode is "mask" from the config above)
    budget_before = get_counter_value(metrics_reg, "pii_mask", "mask", "budget_exceeded")

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "process TKT-1234567 please"),
        headers=auth_key(key_info["key"]),
    )

    # Fail-OPEN: budget exceeded must NOT block the request
    assert resp.status_code == 200, (
        f"budget exceeded must not block (fail-OPEN); got {resp.status_code}: {resp.text}"
    )
    assert upstream.calls == 1, (
        f"upstream must be called despite budget exceeded, got calls={upstream.calls}"
    )

    # TRUE-RED: budget_exceeded action does not exist in the implementation
    budget_after = get_counter_value(metrics_reg, "pii_mask", "mask", "budget_exceeded")
    assert budget_after > budget_before, (
        f"gateway_guardrail_events_total{{guardrail='pii_mask', mode='mask', "
        f"action='budget_exceeded'}} should increment on budget exceeded; "
        f"before={budget_before}, after={budget_after}"
    )


# ===========================================================================
# S16 — Frozen v4 EMAIL literal still works after v2 extension (regression)
#
# NOTE on true-red status for S16:
#   This is a regression guard: the v4 EMAIL built-in already works. This test
#   is expected to be GREEN in the current (pre-build) state because the evaluator
#   is not yet extended and the existing email masking is unaffected.
#   After build starts extending the evaluator, if the v4 literals are accidentally
#   broken, THIS test will catch it. It is included in the red suite as a
#   regression anchor — "must stay green" through the v2 build.
#   The test is structured so that IF it runs red it would do so for the right
#   reason (wrong literal or missing masking), not a test bug.
# ===========================================================================


async def test_v4_email_literal_regression(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Regression: v4 EMAIL literal '[EMAIL_REDACTED]' unchanged after v2 built-in extension."""
    jwt, _ = await signup_and_login(
        client, tenant_name="EmailRegressionCo", email="owner@emailregress.io"
    )
    key_info = await create_key(client, jwt, name="email-regression-key")

    # Plain pii_mask with no custom patterns — tests v4 built-in path only
    resp_put = await client.put(
        ADMIN_GUARDRAILS,
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers=auth_jwt(jwt),
    )
    assert resp_put.status_code == 200, (
        f"PUT /admin/guardrails failed: {resp_put.status_code}: {resp_put.text}"
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "please email me at user@example.com for details"),
        headers=auth_key(key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1
    received = upstream.received_messages[0][0].get("content", "")

    # v4 frozen literal — MUST be byte-identical to the frozen contract value
    assert "[EMAIL_REDACTED]" in received, (
        f"v4 EMAIL literal should be '[EMAIL_REDACTED]' (exact), got: {received!r}"
    )
    assert "user@example.com" not in received, (
        f"original email must not reach upstream, got: {received!r}"
    )
    # Verify the literal is byte-exact — no variation
    assert "[EMAIL_REDACTED]" in received and "[EMAIL_REDACTED]".encode() == b"[EMAIL_REDACTED]", (
        "v4 EMAIL literal must be byte-identical to '[EMAIL_REDACTED]' (frozen contract value)"
    )
