"""Failing-first (RED) suite for routing-admin (TASK.md §4).

One test per scenario RA1–RA8. All tests MUST FAIL before BUILD for the right reason:
  - 404 from the gateway (route not yet registered — routing_admin_router absent or not mounted)
  - OR AttributeError on RedisCooldownGate.snapshot_state (method absent)

Collection-time strategy: the module-level import of routing_admin_router is guarded
in a try/except so collection succeeds and each test runs individually and fails
at the assertion level (404 or AttributeError) — not at collection time (which
would produce a single collection error rather than 8 individual FAILs).

All arrangements use local fakes — no live Redis, no live DB, no live HTTP.
Auth uses a real HS256 JWT issued against the test secret.

Test structure per ADD §4 convention:
  - Arrange: inject fakes into app.state AFTER create_app()
  - Act: httpx.AsyncClient over ASGITransport (no network)
  - Assert: observable HTTP behavior only (status, body shape, absence of secret strings)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from gateway.tenants.domain.entities import Role

from .conftest import (
    ADMIN_MODELS,
    ADMIN_ROUTING,
    CalledGate,
    ErrorGate,
    FakeGate,
    LocalFakeRedis,
    auth_header,
    issue_jwt,
    make_settings,
)

# ---------------------------------------------------------------------------
# Module-under-test import — guarded so collection succeeds even before BUILD.
# When the module exists, this sets ROUTER_AVAILABLE=True.
# When absent, tests assert 404 (route not mounted) which is the right RED reason.
# ---------------------------------------------------------------------------
try:
    from gateway.proxy.api.routing_admin_router import routing_admin_router as _routing_admin_router  # noqa: F401
    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_state(settings: Settings, **state_overrides: Any) -> Any:
    """Create the app from settings and override app.state attributes after creation."""
    app = create_app(settings)
    for key, val in state_overrides.items():
        setattr(app.state, key, val)
    return app


async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _candidates_by_model(body: dict[str, Any], model_id: str) -> list[dict[str, Any]]:
    """Extract all candidate entries matching a given model_id."""
    return [c for c in body["candidates"] if c["model_id"] == model_id]


def _candidates_by_pair(body: dict[str, Any], alias: str, model_id: str) -> list[dict[str, Any]]:
    return [c for c in body["candidates"] if c["alias"] == alias and c["model_id"] == model_id]


# ===========================================================================
# RA1 — Full shape, authenticated, cooldown enabled
# ===========================================================================


@pytest.mark.asyncio
async def test_ra1_full_shape_authenticated_with_cooldown() -> None:
    """RA1: Authenticated GET /admin/routing with model_groups + enabled cooldown → 200 full shape.

    RED reason: ImportError on routing_admin_router (module absent), or 404 (not mounted).
    """
    settings = make_settings(
        model_groups={"fast": ["vendor/a", "vendor/b"]},
        cooldown_failure_threshold=3,
        cooldown_ttl_s=60,
        cooldown_window_s=60,
        upstream_max_retries=2,
        upstream_retry_backoff_base_s=1.0,
    )
    fake_gate = FakeGate(states={"vendor/a": "closed", "vendor/b": "open"})
    app = _make_app_with_state(settings, cooldown_gate=fake_gate)

    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body: dict[str, Any] = resp.json()

    # retry_policy block
    assert "retry_policy" in body, f"Missing retry_policy: {body}"
    rp = body["retry_policy"]
    assert rp["max_retries"] == 2, f"max_retries mismatch: {rp}"
    assert rp["backoff_base_s"] == 1.0, f"backoff_base_s mismatch: {rp}"

    # cooldown block
    assert "cooldown" in body, f"Missing cooldown: {body}"
    cd = body["cooldown"]
    assert cd["enabled"] is True, f"cooldown.enabled must be True: {cd}"
    assert cd["threshold"] == 3, f"threshold mismatch: {cd}"
    assert cd["ttl_s"] == 60, f"ttl_s mismatch: {cd}"
    assert cd["window_s"] == 60, f"window_s mismatch: {cd}"

    # model_groups
    assert body["model_groups"] == {"fast": ["vendor/a", "vendor/b"]}, (
        f"model_groups mismatch: {body['model_groups']}"
    )

    # candidates
    candidates = body["candidates"]
    assert len(candidates) == 2, f"Expected 2 candidates, got: {candidates}"

    a_entries = _candidates_by_pair(body, "fast", "vendor/a")
    assert len(a_entries) == 1, f"Expected 1 entry for (fast, vendor/a): {candidates}"
    assert a_entries[0]["state"] == "closed", f"vendor/a state mismatch: {a_entries[0]}"

    b_entries = _candidates_by_pair(body, "fast", "vendor/b")
    assert len(b_entries) == 1, f"Expected 1 entry for (fast, vendor/b): {candidates}"
    assert b_entries[0]["state"] == "open", f"vendor/b state mismatch: {b_entries[0]}"

    # Confirm snapshot_state was called for both candidates
    assert "vendor/a" in fake_gate.calls, f"snapshot_state not called for vendor/a: {fake_gate.calls}"
    assert "vendor/b" in fake_gate.calls, f"snapshot_state not called for vendor/b: {fake_gate.calls}"


# ===========================================================================
# RA2 — Cooldown disabled (gate None); all states "closed"; zero gate interaction
# ===========================================================================


@pytest.mark.asyncio
async def test_ra2_cooldown_disabled_gate_none_all_closed() -> None:
    """RA2: cooldown_failure_threshold=0 → enabled=false, all states "closed", no gate calls.

    RED reason: ImportError or 404.
    """
    settings = make_settings(
        model_groups={"group": ["m1", "m2"]},
        cooldown_failure_threshold=0,  # gate is None at default
    )
    # Inject a CalledGate that raises AssertionError if snapshot_state is called.
    # Because gate is None in this scenario, the router must NOT call snapshot_state.
    # We inject None explicitly to match what create_app() does at threshold=0.
    app = _make_app_with_state(settings, cooldown_gate=None)

    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body: dict[str, Any] = resp.json()

    assert body["cooldown"]["enabled"] is False, f"enabled must be False: {body['cooldown']}"

    candidates = body["candidates"]
    assert len(candidates) == 2, f"Expected 2 candidates: {candidates}"
    for c in candidates:
        assert c["state"] == "closed", (
            f"All states must be 'closed' when gate is None: {c}"
        )


# ===========================================================================
# RA3 — State derivation: open key → "open"; half marker only → "half_open"
# ===========================================================================


@pytest.mark.asyncio
async def test_ra3_state_derivation_open_and_half_open() -> None:
    """RA3: Real RedisCooldownGate over LocalFakeRedis; open key → open; half marker → half_open.

    RED reason: ImportError or 404 (routing_admin router absent);
    also snapshot_state method absent on RedisCooldownGate → AttributeError.
    """
    from gateway.observability.metrics import MetricsRegistry
    from prometheus_client import CollectorRegistry

    settings = make_settings(
        model_groups={"g": ["model-open", "model-half", "model-closed"]},
        cooldown_failure_threshold=3,
        cooldown_ttl_s=60,
        cooldown_window_s=60,
    )

    fake_redis = LocalFakeRedis()
    # Seed open key for model-open
    fake_redis.seed("gateway:cooldown:open:model-open", "1", ex=3600)
    # Seed half marker (no open key) for model-half
    fake_redis.seed("gateway:cooldown:half:model-half", "1", ex=7200)
    # model-closed: no keys at all

    # Build a real gate over the fake redis
    metrics = MetricsRegistry(registry=CollectorRegistry())
    gate = RedisCooldownGate(
        redis=fake_redis,
        metrics_registry=metrics,
        threshold=3,
        ttl_s=60,
        window_s=60,
    )

    app = _make_app_with_state(settings, cooldown_gate=gate)
    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body: dict[str, Any] = resp.json()
    candidates = {c["model_id"]: c["state"] for c in body["candidates"]}

    assert candidates["model-open"] == "open", (
        f"model-open must be 'open', got {candidates['model-open']!r}"
    )
    assert candidates["model-half"] == "half_open", (
        f"model-half must be 'half_open', got {candidates['model-half']!r}"
    )
    assert candidates["model-closed"] == "closed", (
        f"model-closed must be 'closed', got {candidates['model-closed']!r}"
    )


# ===========================================================================
# RA4 — Redis error during snapshot → state "unknown"; 200 still returned
# ===========================================================================


@pytest.mark.asyncio
async def test_ra4_redis_error_yields_unknown_still_200() -> None:
    """RA4: snapshot_state raises ConnectionError → state "unknown"; endpoint returns 200.

    RED reason: ImportError or 404.
    """
    settings = make_settings(
        model_groups={"g": ["model-x"]},
        cooldown_failure_threshold=3,
    )
    error_gate = ErrorGate()
    app = _make_app_with_state(settings, cooldown_gate=error_gate)
    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, (
        f"Expected 200 (fail-open), got {resp.status_code}: {resp.text}"
    )

    body: dict[str, Any] = resp.json()
    candidates = {c["model_id"]: c["state"] for c in body["candidates"]}
    assert candidates["model-x"] == "unknown", (
        f"model-x must be 'unknown' on Redis error, got {candidates['model-x']!r}"
    )


# ===========================================================================
# RA5 — Unauthenticated → 401 matching existing admin-route parity
# ===========================================================================


@pytest.mark.asyncio
async def test_ra5_unauthenticated_401_parity() -> None:
    """RA5: No Authorization header → 401 ERR_AUTH_INVALID_TOKEN; same shape as /admin/models.

    RED reason: ImportError or 404 on routing-admin route (admin/models route exists so
    its 401 is a valid comparison baseline — if routing-admin returns 404 instead of 401,
    the parity assert fails).
    """
    settings = make_settings()
    app = create_app(settings)

    async for client in _client(app):
        routing_resp = await client.get(ADMIN_ROUTING)
        models_resp = await client.get(ADMIN_MODELS)

    # Both must be 401
    assert routing_resp.status_code == 401, (
        f"Expected 401 from /admin/routing (no auth), got {routing_resp.status_code}: {routing_resp.text}"
    )
    assert models_resp.status_code == 401, (
        f"Expected 401 from /admin/models (no auth), got {models_resp.status_code}: {models_resp.text}"
    )

    routing_body = routing_resp.json()
    models_body = models_resp.json()

    # Same error code (parity pin)
    assert routing_body.get("code") == "ERR_AUTH_INVALID_TOKEN", (
        f"routing-admin 401 code mismatch: {routing_body}"
    )
    assert models_body.get("code") == "ERR_AUTH_INVALID_TOKEN", (
        f"admin/models 401 code mismatch: {models_body}"
    )
    assert routing_body.get("status") == 401, f"routing-admin 401 status mismatch: {routing_body}"
    assert models_body.get("status") == 401, f"admin/models 401 status mismatch: {models_body}"


# ===========================================================================
# RA6 — Secrets-free: sentinel secret values never appear in serialized response
# ===========================================================================


@pytest.mark.asyncio
async def test_ra6_secrets_free_sentinel_values() -> None:
    """RA6: Sentinel secrets in Settings never appear in serialized 200 response body.

    RED reason: ImportError or 404.
    """
    SENTINEL_JWT = "SENTINEL_JWT_SECRET_XYZZY_TEST_UNIQUE"
    SENTINEL_OR = "SENTINEL_OR_KEY_ABCDE_TEST_UNIQUE"
    SENTINEL_OIDC = "SENTINEL_OIDC_SECRET_99999_TEST_UNIQUE"

    settings = make_settings(
        jwt_secret=SENTINEL_JWT,
        openrouter_api_key=SENTINEL_OR,
        oidc_client_secret=SENTINEL_OIDC,
        model_groups={},
        cooldown_failure_threshold=0,
    )
    app = create_app(settings)
    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    serialized = json.dumps(resp.json())

    assert SENTINEL_JWT not in serialized, (
        f"jwt_secret sentinel MUST NOT appear in response body: found in {serialized[:200]}"
    )
    assert SENTINEL_OR not in serialized, (
        f"openrouter_api_key sentinel MUST NOT appear in response body: found in {serialized[:200]}"
    )
    assert SENTINEL_OIDC not in serialized, (
        f"oidc_client_secret sentinel MUST NOT appear in response body: found in {serialized[:200]}"
    )


# ===========================================================================
# RA7 — Empty model_groups → 200 with empty groups/candidates; blocks still present
# ===========================================================================


@pytest.mark.asyncio
async def test_ra7_empty_groups_returns_retry_cooldown_blocks() -> None:
    """RA7: model_groups={} → 200; model_groups={}; candidates=[]; retry+cooldown blocks present.

    RED reason: ImportError or 404.
    """
    settings = make_settings(
        model_groups={},
        cooldown_failure_threshold=0,
        upstream_max_retries=0,
        upstream_retry_backoff_base_s=0.5,
    )
    app = create_app(settings)
    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body: dict[str, Any] = resp.json()

    assert body["model_groups"] == {}, f"Expected empty model_groups: {body}"
    assert body["candidates"] == [], f"Expected empty candidates: {body}"

    assert "retry_policy" in body, f"retry_policy block missing: {body}"
    assert "cooldown" in body, f"cooldown block missing: {body}"

    assert body["retry_policy"]["max_retries"] == 0
    assert body["cooldown"]["enabled"] is False


# ===========================================================================
# RA8 — Response is JSON-schema stable (required keys always present)
# ===========================================================================


@pytest.mark.asyncio
async def test_ra8_schema_stable_all_top_level_keys_present() -> None:
    """RA8: Response always has exactly the four top-level keys with correct subkeys.

    GREEN-BY-DESIGN once implementation exists. RED now because:
      ImportError or 404 prevents any 200 response.
    """
    settings = make_settings(
        model_groups={},
        cooldown_failure_threshold=0,
    )
    app = create_app(settings)
    token = issue_jwt(settings, role=Role.SUPERADMIN)  # S1 M7/M9: routing is now superadmin-only

    async for client in _client(app):
        resp = await client.get(ADMIN_ROUTING, headers=auth_header(token))

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body: dict[str, Any] = resp.json()

    # Exact top-level keys
    # DISPOSITION EDIT (routing-config-write §3, 2026-06-23): the FROZEN routing-admin shape is
    # ADDITIVELY extended with `routing_strategy` + `deployments` so the write endpoint round-trips
    # and the editor can read strategy/deployment detail. The four original blocks are unchanged;
    # the secrets-free intent of this assertion is preserved (both additions are config, not secrets).
    expected_top = {
        "retry_policy",
        "cooldown",
        "model_groups",
        "candidates",
        "routing_strategy",
        "deployments",
    }
    actual_top = set(body.keys())
    assert actual_top == expected_top, (
        f"Top-level keys mismatch.\n  Expected: {expected_top}\n  Actual: {actual_top}"
    )

    # retry_policy subkeys
    rp = body["retry_policy"]
    assert set(rp.keys()) == {"max_retries", "backoff_base_s"}, (
        f"retry_policy keys mismatch: {set(rp.keys())}"
    )
    assert isinstance(rp["max_retries"], int), f"max_retries must be int: {rp}"
    assert isinstance(rp["backoff_base_s"], float), f"backoff_base_s must be float: {rp}"

    # cooldown subkeys
    cd = body["cooldown"]
    assert set(cd.keys()) == {"enabled", "threshold", "ttl_s", "window_s"}, (
        f"cooldown keys mismatch: {set(cd.keys())}"
    )
    assert isinstance(cd["enabled"], bool), f"enabled must be bool: {cd}"
    assert isinstance(cd["threshold"], int), f"threshold must be int: {cd}"
    assert isinstance(cd["ttl_s"], int), f"ttl_s must be int: {cd}"
    assert isinstance(cd["window_s"], int), f"window_s must be int: {cd}"

    # model_groups is a dict
    assert isinstance(body["model_groups"], dict), (
        f"model_groups must be dict: {type(body['model_groups'])}"
    )

    # candidates is a list
    assert isinstance(body["candidates"], list), (
        f"candidates must be list: {type(body['candidates'])}"
    )

    # All candidate entries have exactly {model_id, alias, state} with valid state
    valid_states = {"closed", "open", "half_open", "unknown"}
    for entry in body["candidates"]:
        assert set(entry.keys()) == {"model_id", "alias", "state"}, (
            f"Candidate entry has wrong keys: {entry}"
        )
        assert entry["state"] in valid_states, (
            f"state must be one of {valid_states}, got {entry['state']!r}"
        )
