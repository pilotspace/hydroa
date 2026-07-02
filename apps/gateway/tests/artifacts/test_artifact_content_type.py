"""Red suite for the artifact upload content-type allow-policy (v55 task 4).

Contract FROZEN @ v1 (TASK.md §3), Tin 2026-06-30 — 422-first ordering:
  POST /v1/artifacts order: auth -> base64 decode (422) -> size cap (413)
                            -> content-type allow-policy (NEW, 415) -> store -> commit

  GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES (CSV; default "" = allow-all, byte-identical).
  When non-empty: normalize(content_type) must be in the normalized allow-list, else
  415 ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED — before any object-store put or DB commit.
  normalize(s) = s.split(";",1)[0].strip().lower()  (media-type only, case/param-insensitive).

One integration test per §2 scenario + a unit test for the pure helper.
The allow-list is toggled per-test by mutating app.state.settings (the proven pattern).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror tests/artifacts/test_artifacts.py)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {"key": created.json()["key"], "tenant_id": tenant_id}


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="ArtCT", email="art-ct@example.io", password="art-ct-battery"
    )


def _set_allowlist(app: Any, csv: str) -> None:
    """Toggle the content-type allow-list on the live app settings."""
    app.state.settings.artifact_allowed_content_types = csv


async def _list_count(client: Any, key: str) -> int:
    resp = await client.get("/v1/artifacts", headers=_bearer(key))
    assert resp.status_code == 200, resp.text
    return len(resp.json()["data"])


def _payload(content_type: str, raw: bytes = b"hello", *, valid_b64: bool = True) -> dict[str, str]:
    content_b64 = base64.b64encode(raw).decode() if valid_b64 else "@@@not-base64@@@"
    return {"name": "f.bin", "content_type": content_type, "content_base64": content_b64}


class _SpyStore:
    """ObjectStore spy — records put() calls so a refused upload can prove no storage."""

    def __init__(self) -> None:
        self.put_calls = 0

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.put_calls += 1


# ---------------------------------------------------------------------------
# Integration: scenarios
# ---------------------------------------------------------------------------


async def test_empty_allowlist_accepts_any(
    app: Any, client: Any, api_key_info: dict[str, str]
) -> None:
    """Allow-list "" (default) → any content_type uploads 201 (byte-identical)."""
    _set_allowlist(app, "")
    resp = await client.post(
        "/v1/artifacts",
        json=_payload("application/x-anything"),
        headers=_bearer(api_key_info["key"]),
    )
    assert resp.status_code == 201, resp.text


async def test_allowed_type_passes(app: Any, client: Any, api_key_info: dict[str, str]) -> None:
    """Allow-list set, content_type in it → 201."""
    _set_allowlist(app, "image/png,image/jpeg,application/pdf")
    resp = await client.post(
        "/v1/artifacts", json=_payload("image/png"), headers=_bearer(api_key_info["key"])
    )
    assert resp.status_code == 201, resp.text


async def test_disallowed_type_rejected_415(
    app: Any, client: Any, api_key_info: dict[str, str]
) -> None:
    """Allow-list set, content_type NOT in it → 415, no row, no object-store put."""
    _set_allowlist(app, "image/png,image/jpeg")
    spy = _SpyStore()
    app.state.object_store = spy

    before = await _list_count(client, api_key_info["key"])
    resp = await client.post(
        "/v1/artifacts", json=_payload("text/html"), headers=_bearer(api_key_info["key"])
    )

    assert resp.status_code == 415, resp.text
    body = resp.json()
    assert body["code"] == "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED"
    assert body["status"] == 415
    assert "text/html" in body.get("detail", "") or "text/html" in body.get("title", "")
    assert await _list_count(client, api_key_info["key"]) == before, (
        "rejected upload must not persist"
    )
    assert spy.put_calls == 0, "object store put must NOT be called on a rejected upload"


async def test_normalized_match_case_and_params(
    app: Any, client: Any, api_key_info: dict[str, str]
) -> None:
    """content_type match is normalized: 'IMAGE/PNG; charset=utf-8' matches 'image/png'."""
    _set_allowlist(app, "image/png")
    resp = await client.post(
        "/v1/artifacts",
        json=_payload("IMAGE/PNG; charset=utf-8"),
        headers=_bearer(api_key_info["key"]),
    )
    assert resp.status_code == 201, resp.text


async def test_base64_precedes_contenttype(
    app: Any, client: Any, api_key_info: dict[str, str]
) -> None:
    """Disallowed type AND invalid base64 → 422 (decode wins; 415 never reached)."""
    _set_allowlist(app, "image/png")
    before = await _list_count(client, api_key_info["key"])
    resp = await client.post(
        "/v1/artifacts",
        json=_payload("text/html", valid_b64=False),
        headers=_bearer(api_key_info["key"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "ERR_PAYLOAD_INVALID_BASE64"
    assert await _list_count(client, api_key_info["key"]) == before


# ---------------------------------------------------------------------------
# Unit: pure helper
# ---------------------------------------------------------------------------


def test_content_type_policy_helper() -> None:
    """normalize lowercases + strips params/whitespace; empty allow-list → allow-all."""
    from gateway.artifacts.content_type_policy import (
        is_content_type_allowed,
        normalize_content_type,
        parse_allowed_content_types,
    )

    assert normalize_content_type("IMAGE/PNG; charset=utf-8") == "image/png"
    assert normalize_content_type("  text/Plain  ") == "text/plain"

    assert parse_allowed_content_types("") == frozenset()
    assert parse_allowed_content_types("image/png, IMAGE/JPEG ,,") == frozenset(
        {"image/png", "image/jpeg"}
    )

    # Empty allow-list → everything allowed (byte-identical default)
    assert is_content_type_allowed("anything/here", "") is True
    # Non-empty → membership under normalization
    assert is_content_type_allowed("IMAGE/PNG; q=1", "image/png") is True
    assert is_content_type_allowed("text/html", "image/png,image/jpeg") is False
