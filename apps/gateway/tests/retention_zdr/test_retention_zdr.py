"""RED->GREEN suite for tenant-retention-zdr (TASK.md §4).

One test per §2 scenario (Must M1-M10, Reject R1-R5), plus a dedicated is_zdr() read-
port unit test (M9). Tests MUST fail (ImportError) before the build phase because
gateway.tenants.application.retention_policy / gateway.tenants.api.retention_policy_router
do not exist yet, and the ZDR gate/columns are absent from the 5 payload repositories +
TenantRow.

TRUE-RED RULE: every test asserts TARGET behavior for the RIGHT reason (ImportError on
the new module, or an AssertionError against a field/endpoint/column that does not exist
yet — never a wrong-reason red from a typo'd fixture).

Infrastructure (mirrors tests/test_retention_sweep.py + tests/response_caching):
  - Real Postgres (schema rebuilt per test via the `app` fixture: drop_all + create_all).
  - Real Redis (redis://localhost:6380/9) for the ZDR cache-purge + cache-skip scenarios.
  - httpx.ASGITransport — no network.
  - RetentionSweeper is constructed manually per test (mirrors test_retention_sweep.py —
    the app's own lifespan-started sweeper never runs under ASGITransport).

Known test-environment limitation (documented, not silently worked around): AlertEventRow
deliberately OMITS tenant_id from its ORM mapped_column (so Base.metadata.create_all
never enforces the production FK, letting existing suites insert system-wide alert rows
with no tenant). The `alert_tenant_column` fixture below patches the TEST schema only
(raw ALTER TABLE, mirroring test_retention_sweep.py's own `audit_trigger_session`
precedent) so the tenant-shortening pass on alert_events is exercised here without
touching gateway/usage/infrastructure/alert_events_orm.py (out of this task's declared
Scope).
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.artifacts.infrastructure.repository import ArtifactRepository
from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.conversations.infrastructure.repository import ConversationRepository
from gateway.memory.infrastructure.repository import MemoryRepository
from gateway.objectstore.errors import ObjectStoreUnavailableError
from gateway.tenants.application.retention_policy import is_zdr
from gateway.usage.application.retention_sweep import RetentionSweeper
from gateway.video.infrastructure.repository import VideoJobRepository

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
RETENTION_POLICY = "/admin/retention-policy"
COMPLETIONS = "/v1/chat/completions"
ARTIFACTS = "/v1/artifacts"
CONVERSATIONS = "/v1/conversations"
MEMORIES = "/v1/memories"
BATCHES = "/v1/batches"
VIDEO = "/v1/video/generations"

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeObjectStore:
    """In-memory ObjectStore — put/get succeed; delete can be told to fail for
    specific keys (raises ObjectStoreUnavailableError), simulating an outage."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.store[key] = data

    async def get(self, key: str) -> bytes:
        return self.store[key]

    async def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise ObjectStoreUnavailableError(f"simulated outage for {key}")
        self.store.pop(key, None)
        self.deleted.append(key)

    async def health(self) -> bool:
        return True


class FakeCompletionUpstream:
    """Fake non-streaming upstream — tracks call count (mirrors response_caching)."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.body = body or {
            "id": "gen-zdr-1",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return 200, self.body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


async def _signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner; return (jwt, tenant_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def _create_key(
    client: httpx.AsyncClient, jwt: str, *, name: str, cache_enabled: bool = False
) -> dict[str, Any]:
    resp = await client.post(
        ADMIN_KEYS,
        json={"name": name, "cache_enabled": cache_enabled},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 201, f"create_key failed: {resp.text}"
    return resp.json()  # {key, key_id, ...}


def _issue_role_token(app: Any, *, tenant_id: str, role_str: str, email: str) -> str:
    from gateway.tenants.domain.entities import Role

    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role(role_str),
        email=email,
    )
    return token


_UNSET: Any = object()


async def _set_tenant_zdr(
    db_session: AsyncSession,
    tenant_id: str,
    *,
    zdr_enabled: bool | None = None,
    retention_window_days: Any = _UNSET,
    zdr_enabled_at: Any = _UNSET,
) -> None:
    """Raw-SQL patch of a tenant's retention columns (bypasses the PUT router — used to
    arrange pre-conditions the router itself is not under test for in a given case)."""
    sets = []
    params: dict[str, Any] = {"tid": tenant_id}
    if zdr_enabled is not None:
        sets.append("zdr_enabled = :zdr")
        params["zdr"] = zdr_enabled
    if retention_window_days is not _UNSET:
        sets.append("retention_window_days = :wd")
        params["wd"] = retention_window_days
    if zdr_enabled_at is not _UNSET:
        sets.append("zdr_enabled_at = :zat")
        params["zat"] = zdr_enabled_at
    if not sets:
        return
    await db_session.execute(
        text(f"UPDATE tenants SET {', '.join(sets)} WHERE id = :tid"),  # noqa: S608
        params,
    )
    await db_session.commit()


def _make_sweeper(
    app: Any,
    *,
    usage_days: int = 365,
    alert_days: int = 90,
    audit_days: int = 0,
    redis: Any = None,
    object_store: Any = None,
) -> RetentionSweeper:
    from unittest.mock import MagicMock

    s = MagicMock()
    s.retention_check_interval_seconds = 86400
    s.retention_usage_records_days = usage_days
    s.retention_alert_events_days = alert_days
    s.retention_audit_events_days = audit_days
    s.retention_audit_floor_days = 0
    s.retention_batch_size = 1000
    return RetentionSweeper(
        session_factory=app.state.sessionmaker,
        settings=s,
        redis=redis,
        object_store=object_store,
    )


async def _age_row(
    db_session: AsyncSession, table: str, ts_col: str, row_id: Any, days: int
) -> None:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    await db_session.execute(
        text(f"UPDATE {table} SET {ts_col} = :ts WHERE id = :id"),  # noqa: S608 — fixed table/col set, test-only
        {"ts": ts.replace(tzinfo=None), "id": row_id},
    )
    await db_session.commit()


@pytest.fixture
async def redis_client() -> Any:
    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def alert_tenant_column(db_session: AsyncSession) -> AsyncSession:
    """Patch the TEST schema only: add a nullable tenant_id column to alert_events so the
    tenant-shortening pass can be exercised here (production already has this column via
    migration f4a9b3c7e8d2 — the ORM deliberately omits it from create_all, see module
    docstring). Mirrors test_retention_sweep.py's own audit_trigger_session precedent.
    """
    await db_session.execute(
        text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS tenant_id UUID NULL")
    )
    await db_session.commit()
    return db_session


# ===========================================================================
# M1 — GET default policy
# ===========================================================================


async def test_get_default_policy(client: httpx.AsyncClient) -> None:
    jwt, _tid = await _signup_owner(client, tenant_name="RetDefault", email="owner@retdef.io")
    resp = await client.get(RETENTION_POLICY, headers=_bearer(jwt))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_days"] is None
    assert body["zdr_enabled"] is False
    assert body["zdr_enabled_at"] is None
    assert body["operator_ceiling_days"] == 365
    effective = body["effective_window_days"]
    assert effective["usage_records"] == 365
    assert effective["alert_events"] == 90
    for table in (
        "artifacts",
        "conversations",
        "memories",
        "batch_job_items",
        "video_generation_jobs",
    ):
        assert effective[table] == 365, f"{table} default should inherit the ceiling (365)"
    assert "audit_events" not in effective, "audit_events must never appear in window_days scope"


# ===========================================================================
# M2/M3/M4 — Owner shortens the window; sweep purges all 7 non-audit tables
# ===========================================================================


async def test_put_shortens_window_and_sweep_purges_seven_tables(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    alert_tenant_column: AsyncSession,
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="RetShorten", email="owner@retshort.io")
    key_info = await _create_key(client, jwt, name="k1")
    key_id = key_info["key_id"]

    put_resp = await client.put(RETENTION_POLICY, json={"window_days": 30}, headers=_bearer(jwt))
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["window_days"] == 30
    for table in (
        "usage_records",
        "alert_events",
        "artifacts",
        "conversations",
        "memories",
        "batch_job_items",
        "video_generation_jobs",
    ):
        assert body["effective_window_days"][table] == 30, f"{table} != 30: {body}"

    # ── Arrange aged (50d, > window) and recent (5d, < window) rows in every table ──
    old_usage_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO usage_records (id, tenant_id, key_id, model_id, prompt_tokens,"
            " completion_tokens, cost_usd, status, raw, created_at, cost_basis, usage_source)"
            " VALUES (:id, :tid, :kid, 'openai/gpt-4o', 0, 0, 0, 200, '{}', :ts, 'catalog', 'frame')"
        ),
        {
            "id": old_usage_id,
            "tid": tid,
            "kid": key_id,
            "ts": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=50)).replace(
                tzinfo=None
            ),
        },
    )
    recent_usage_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO usage_records (id, tenant_id, key_id, model_id, prompt_tokens,"
            " completion_tokens, cost_usd, status, raw, created_at, cost_basis, usage_source)"
            " VALUES (:id, :tid, :kid, 'openai/gpt-4o', 0, 0, 0, 200, '{}', :ts, 'catalog', 'frame')"
        ),
        {
            "id": recent_usage_id,
            "tid": tid,
            "kid": key_id,
            "ts": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=5)).replace(
                tzinfo=None
            ),
        },
    )
    old_alert_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO alert_events (id, tenant_id, event_type, payload, created_at, dedupe_key)"
            " VALUES (:id, :tid, 'test.alert', '{}', :ts, :dk)"
        ),
        {
            "id": old_alert_id,
            "tid": tid,
            "ts": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=50)).replace(
                tzinfo=None
            ),
            "dk": f"dk-{old_alert_id}",
        },
    )
    await db_session.commit()

    artifact_repo = ArtifactRepository(db_session)
    old_artifact = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="old.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="inline",
        object_key=None,
        content=b"abc",
    )
    recent_artifact = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="recent.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="inline",
        object_key=None,
        content=b"abc",
    )
    await db_session.commit()
    await _age_row(db_session, "artifacts", "created_at", old_artifact.id, 50)
    await _age_row(db_session, "artifacts", "created_at", recent_artifact.id, 5)

    conv_repo = ConversationRepository(db_session)
    old_conv = await conv_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), title="old"
    )
    recent_conv = await conv_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), title="recent"
    )
    await db_session.commit()
    await _age_row(db_session, "conversations", "updated_at", old_conv.id, 50)
    await _age_row(db_session, "conversations", "updated_at", recent_conv.id, 5)

    mem_repo = MemoryRepository(db_session)
    old_mem = await mem_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content="old", meta=None
    )
    recent_mem = await mem_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content="recent", meta=None
    )
    await db_session.commit()
    await _age_row(db_session, "memories", "created_at", old_mem.id, 50)
    await _age_row(db_session, "memories", "created_at", recent_mem.id, 5)

    batch_repo = BatchJobRepository(db_session)
    old_job = await batch_repo.create(
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        line_items=[{"custom_id": "c1", "body": {"model": "m", "messages": []}}],
    )
    recent_job = await batch_repo.create(
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        line_items=[{"custom_id": "c1", "body": {"model": "m", "messages": []}}],
    )
    await db_session.commit()
    old_item_id = (await batch_repo.list_items(tenant_id=uuid.UUID(tid), job_id=old_job.id))[0].id
    recent_item_id = (await batch_repo.list_items(tenant_id=uuid.UUID(tid), job_id=recent_job.id))[
        0
    ].id
    await _age_row(db_session, "batch_job_items", "created_at", old_item_id, 50)
    await _age_row(db_session, "batch_job_items", "created_at", recent_item_id, 5)

    video_repo = VideoJobRepository(db_session)
    old_video = await video_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), model="m", prompt="old", params=None
    )
    recent_video = await video_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), model="m", prompt="recent", params=None
    )
    await db_session.commit()
    await _age_row(db_session, "video_generation_jobs", "created_at", old_video.id, 50)
    await _age_row(db_session, "video_generation_jobs", "created_at", recent_video.id, 5)

    # ── Sweep once with generous operator defaults (90/365d) so ONLY the tenant's
    # 30-day override explains any deletion below the operator default line ──
    sweeper = _make_sweeper(app, usage_days=365, alert_days=90)
    await sweeper.sweep_once()

    async def _count(table: str, row_id: Any) -> int:
        r = await db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row_id},
        )
        return int(r.scalar() or 0)

    assert await _count("usage_records", old_usage_id) == 0, "50d usage row must be purged"
    assert await _count("usage_records", recent_usage_id) == 1, "5d usage row must survive"
    assert await _count("alert_events", old_alert_id) == 0, "50d alert row must be purged"
    assert await _count("artifacts", old_artifact.id) == 0
    assert await _count("artifacts", recent_artifact.id) == 1
    assert await _count("conversations", old_conv.id) == 0
    assert await _count("conversations", recent_conv.id) == 1
    assert await _count("memories", old_mem.id) == 0
    assert await _count("memories", recent_mem.id) == 1
    assert await _count("batch_job_items", old_item_id) == 0
    assert await _count("batch_job_items", recent_item_id) == 1
    assert await _count("video_generation_jobs", old_video.id) == 0
    assert await _count("video_generation_jobs", recent_video.id) == 1


# ===========================================================================
# R1 — window_days validation
# ===========================================================================


async def test_put_window_above_ceiling_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tid = await _signup_owner(client, tenant_name="RetCeil", email="owner@retceil.io")
    resp = await client.put(RETENTION_POLICY, json={"window_days": 400}, headers=_bearer(jwt))
    assert_problem(resp, 422, "ERR_RETENTION_WINDOW_INVALID")

    get_resp = await client.get(RETENTION_POLICY, headers=_bearer(jwt))
    assert get_resp.json()["window_days"] is None, "rejected PUT must not persist"


async def test_put_window_nonpositive_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tid = await _signup_owner(client, tenant_name="RetZero", email="owner@retzero.io")
    resp = await client.put(RETENTION_POLICY, json={"window_days": 0}, headers=_bearer(jwt))
    assert_problem(resp, 422, "ERR_RETENTION_WINDOW_INVALID")

    get_resp = await client.get(RETENTION_POLICY, headers=_bearer(jwt))
    assert get_resp.json()["window_days"] is None, "rejected PUT must not persist"


# ===========================================================================
# R2 — non-owner cannot change the policy (byte-identical 403 across roles)
# ===========================================================================


@pytest.mark.parametrize("role", ["admin", "operator", "billing_admin", "viewer", "member"])
async def test_put_non_owner_forbidden(client: httpx.AsyncClient, app: Any, role: str) -> None:
    domain_role = role.replace("_", "-")
    jwt, tid = await _signup_owner(
        client, tenant_name=f"RetRole{role}", email=f"o@{domain_role}.retro.io"
    )
    non_owner_token = _issue_role_token(app, tenant_id=tid, role_str=role, email=f"{role}@retro.io")
    resp = await client.put(
        RETENTION_POLICY, json={"zdr_enabled": True}, headers=_bearer(non_owner_token)
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    get_resp = await client.get(RETENTION_POLICY, headers=_bearer(jwt))
    assert get_resp.json()["zdr_enabled"] is False, "rejected PUT must not persist"


# ===========================================================================
# M5/R3 — ZDR blocks new payload writes at all 5 gated repositories
# ===========================================================================


async def test_zdr_blocks_artifact_upload(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrArt", email="owner@zdrart.io")
    key_info = await _create_key(client, jwt, name="k1")
    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)

    pre = await db_session.execute(
        text("SELECT COUNT(*) FROM artifacts WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert pre.scalar() == 0

    resp = await client.post(
        ARTIFACTS,
        json={"name": "f.txt", "content_type": "text/plain", "content_base64": "aGVsbG8="},
        headers=_bearer(key_info["key"]),
    )
    assert_problem(resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

    post = await db_session.execute(
        text("SELECT COUNT(*) FROM artifacts WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert post.scalar() == 0, "no ArtifactRow, not even a metadata-only stub, may be created"


async def test_zdr_blocks_conversation_memory_batch_video(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrFour", email="owner@zdrfour.io")
    key_info = await _create_key(client, jwt, name="k1")
    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)
    headers = _bearer(key_info["key"])

    conv_resp = await client.post(CONVERSATIONS, json={"title": "x"}, headers=headers)
    assert_problem(conv_resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

    mem_resp = await client.post(MEMORIES, json={"content": "hello"}, headers=headers)
    assert_problem(mem_resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

    batch_resp = await client.post(
        BATCHES,
        json={
            "line_items": [
                {
                    "custom_id": "c1",
                    "body": {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                }
            ]
        },
        headers=headers,
    )
    assert_problem(batch_resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

    video_resp = await client.post(VIDEO, json={"model": "m", "prompt": "a cat"}, headers=headers)
    assert_problem(video_resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

    for table in ("conversations", "memories", "batch_job_items", "video_generation_jobs"):
        r = await db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
            {"tid": tid},
        )
        assert r.scalar() == 0, f"{table} must have zero rows for the ZDR tenant"


# ===========================================================================
# M6/M8 — ZDR completion is byte-identical minus caching; usage still billed
# ===========================================================================


async def test_zdr_completion_skips_cache_but_bills_usage(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrCache", email="owner@zdrcache.io")
    key_info = await _create_key(client, jwt, name="k1", cache_enabled=True)
    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)

    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token,"
            " completion_usd_per_token, captured_at) VALUES (:sid, :mid, :p, :c, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {"sid": str(uuid.uuid4()), "mid": model_id, "p": "0.000001", "c": "0.000002"},
    )
    await db_session.commit()

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    payload = {"model": model_id, "messages": [{"role": "user", "content": "what is 2+2?"}]}
    headers = _bearer(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=headers)
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text
    assert resp1.json() == resp2.json() == upstream.body

    # No cache hit ever happens under ZDR — both calls reach the upstream.
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (cache fully skipped), got {upstream.calls}"
    )

    # No resp-cache:/vec-cache: keys were ever written for this tenant.
    keys = [k async for k in redis_client.scan_iter(match=f"resp-cache:{tid}:*")]
    keys += [k async for k in redis_client.scan_iter(match=f"vec-cache:{tid}:*")]
    assert keys == [], f"expected zero cache keys under ZDR, found: {keys}"

    await asyncio.sleep(0.1)
    rows = (
        await db_session.execute(
            text(
                "SELECT prompt_tokens, completion_tokens, cost_usd FROM usage_records"
                " WHERE tenant_id = :tid"
            ),
            {"tid": tid},
        )
    ).fetchall()
    assert len(rows) == 2, f"expected 2 usage_records rows (metadata still billed), got {rows}"
    for prompt_tokens, completion_tokens, cost_usd in rows:
        assert prompt_tokens == 10
        assert completion_tokens == 5
        assert cost_usd > 0


# ===========================================================================
# M7 — Enabling ZDR purges pre-existing payload rows (+ audit + Redis + object store)
# ===========================================================================


async def test_zdr_purge_pre_existing_payload_rows(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrPurge", email="owner@zdrpurge.io")
    key_info = await _create_key(client, jwt, name="k1")
    key_id = key_info["key_id"]

    object_store = FakeObjectStore()
    artifact_repo = ArtifactRepository(db_session)
    inline_artifact = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="inline.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="inline",
        object_key=None,
        content=b"abc",
    )
    s3_key = f"artifacts/{tid}/s3-one"
    await object_store.put(s3_key, b"xyz", "text/plain")
    s3_artifact = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="s3.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="s3",
        object_key=s3_key,
        content=None,
    )
    conv_repo = ConversationRepository(db_session)
    conv = await conv_repo.create(tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), title="c")
    await conv_repo.append_message(
        tenant_id=uuid.UUID(tid), conversation_id=conv.id, role="user", content="hi"
    )
    mem_repo = MemoryRepository(db_session)
    mem = await mem_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content="m", meta=None
    )
    await db_session.commit()

    # Seed a resp-cache / vec-cache key for this tenant directly (as if a prior
    # cache_enabled=true request had stored one before ZDR was turned on).
    await redis_client.set(f"resp-cache:{tid}:abc123", "cached-body")
    await redis_client.set(f"vec-cache:{tid}:def456", "vector-entry")

    # ── Enable ZDR ──
    put_resp = await client.put(RETENTION_POLICY, json={"zdr_enabled": True}, headers=_bearer(jwt))
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["zdr_enabled"] is True
    assert put_resp.json()["zdr_enabled_at"] is not None

    sweeper = _make_sweeper(app, redis=redis_client, object_store=object_store)
    await sweeper.sweep_once()

    async def _count(table: str, tenant_id: str) -> int:
        r = await db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
            {"tid": tenant_id},
        )
        return int(r.scalar() or 0)

    assert await _count("artifacts", tid) == 0
    assert await _count("conversations", tid) == 0
    msg_count = await db_session.execute(
        text("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = :cid"),
        {"cid": conv.id},
    )
    assert msg_count.scalar() == 0, "messages must be gone via CASCADE"
    assert await _count("memories", tid) == 0

    assert s3_key in object_store.deleted, "ObjectStore.delete must be called for the s3 artifact"
    assert s3_key not in object_store.store

    remaining_keys = [k async for k in redis_client.scan_iter(match=f"resp-cache:{tid}:*")]
    remaining_keys += [k async for k in redis_client.scan_iter(match=f"vec-cache:{tid}:*")]
    assert remaining_keys == [], f"expected zero cache keys after ZDR purge, found {remaining_keys}"

    await asyncio.sleep(0.1)
    # M10: sweeper-driven purges are "system-level" (no actor) — the pre-existing
    # audit_missing_actor invariant (audit/domain/audit_event.py) forbids a tenant-scoped
    # AuditEvent with no actor, so tenant_id stays NULL on the row itself and the tenant
    # is instead named in metadata (still queryable/auditable per-tenant).
    audit_check = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM audit_events"
            " WHERE action = 'data.purge' AND metadata->>'tenant_id' = :tid"
        ),
        {"tid": tid},
    )
    assert (audit_check.scalar() or 0) >= 1, "each purged table must emit a data.purge audit event"

    # ids kept only to avoid unused-var lint noise in some configs
    assert inline_artifact.id is not None
    assert s3_artifact.id is not None
    assert mem.id is not None


# ===========================================================================
# M7 (design-for-failure) — ZDR purge survives a process restart mid-purge
# ===========================================================================


async def test_zdr_purge_survives_restart_mid_purge(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrResume", email="owner@zdrresume.io")
    key_info = await _create_key(client, jwt, name="k1")
    key_id = key_info["key_id"]

    mem_repo = MemoryRepository(db_session)
    ids = []
    for i in range(5):
        row = await mem_repo.create(
            tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content=f"m{i}", meta=None
        )
        ids.append(row.id)
    await db_session.commit()

    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)

    # Simulate "3 of 5 already purged by a crashed sweep" — condition is
    # tenant_id + zdr_enabled=true, NOT a one-shot job, so a partial prior purge is
    # simply a smaller remaining set for the next tick to finish.
    for row_id in ids[:3]:
        await db_session.execute(text("DELETE FROM memories WHERE id = :id"), {"id": row_id})
    await db_session.commit()

    sweeper_1 = _make_sweeper(app)
    result_1 = await sweeper_1.sweep_once()
    assert result_1.get("memories", 0) == 2, f"expected the 2 remaining rows purged, got {result_1}"

    count = await db_session.execute(
        text("SELECT COUNT(*) FROM memories WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert count.scalar() == 0

    # A SECOND independent sweeper instance ("process restart") re-runs cleanly —
    # idempotent, no error, zero remaining rows either way.
    sweeper_2 = _make_sweeper(app)
    result_2 = await sweeper_2.sweep_once()
    assert result_2.get("memories", 0) == 0


# ===========================================================================
# M7 (design-for-failure) — object-store outage defers, never crashes the sweep
# ===========================================================================


async def test_zdr_purge_object_store_outage_deferred(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrOutage", email="owner@zdroutage.io")
    key_info = await _create_key(client, jwt, name="k1")
    key_id = key_info["key_id"]

    object_store = FakeObjectStore()
    artifact_repo = ArtifactRepository(db_session)
    s3_key = f"artifacts/{tid}/unreachable"
    await object_store.put(s3_key, b"xyz", "text/plain")
    object_store.fail_keys.add(s3_key)  # simulate ObjectStoreUnavailableError on delete
    artifact = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="s3.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="s3",
        object_key=s3_key,
        content=None,
    )
    inline = await artifact_repo.create(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        key_id=uuid.UUID(key_id),
        name="inline.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_backend="inline",
        object_key=None,
        content=b"abc",
    )
    await db_session.commit()

    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)
    sweeper = _make_sweeper(app, object_store=object_store)

    # Must not raise — sweep_once() is always fail-open, even mid-table.
    result = await sweeper.sweep_once()
    assert isinstance(result, dict)

    s3_row = await db_session.execute(
        text("SELECT COUNT(*) FROM artifacts WHERE id = :id"), {"id": artifact.id}
    )
    assert s3_row.scalar() == 1, (
        "the s3 row whose object delete failed must be DEFERRED, not dropped, while its "
        "bytes may still be unreachable-but-present"
    )
    inline_row = await db_session.execute(
        text("SELECT COUNT(*) FROM artifacts WHERE id = :id"), {"id": inline.id}
    )
    assert inline_row.scalar() == 0, "the inline row (no object-store dependency) still purges"

    # The outage clears; the NEXT tick recovers the deferred row.
    object_store.fail_keys.discard(s3_key)
    await sweeper.sweep_once()
    s3_row_2 = await db_session.execute(
        text("SELECT COUNT(*) FROM artifacts WHERE id = :id"), {"id": artifact.id}
    )
    assert s3_row_2.scalar() == 0, (
        "once the store recovers, the deferred row purges on the next tick"
    )
    assert s3_key in object_store.deleted


# ===========================================================================
# R5 — cross-tenant retention-policy access is 404, never a leak
# ===========================================================================


async def test_cross_tenant_retention_policy_404(app: Any, client: httpx.AsyncClient) -> None:
    # No path/query param exists to name another tenant on this endpoint — the only
    # structurally possible way to probe "another tenant" is an identity whose own
    # tenant_id does not resolve to a real tenants row (e.g. orphaned/deleted).
    unknown_tenant_id = uuid.uuid4()
    token = _issue_role_token(
        app, tenant_id=str(unknown_tenant_id), role_str="owner", email="ghost@nowhere.io"
    )
    get_resp = await client.get(RETENTION_POLICY, headers=_bearer(token))
    assert_problem(get_resp, 404, "ERR_TENANT_NOT_FOUND")

    put_resp = await client.put(RETENTION_POLICY, json={"window_days": 10}, headers=_bearer(token))
    assert_problem(put_resp, 404, "ERR_TENANT_NOT_FOUND")


# ===========================================================================
# R4 — audit_events retention is never affected by any tenant action
# ===========================================================================


async def test_audit_events_never_affected_by_tenant_policy(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrAudit", email="owner@zdraudit.io")
    await _set_tenant_zdr(db_session, tid, zdr_enabled=True, retention_window_days=1)

    audit_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO audit_events (id, tenant_id, actor_user_id, actor_email, action,"
            " target_type, target_id, result, metadata, created_at)"
            " VALUES (:id, :tid, NULL, NULL, 'system.test', 'system', 'x', 'success', '{}', :ts)"
        ),
        {
            "id": audit_id,
            "tid": tid,
            "ts": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)).replace(
                tzinfo=None
            ),
        },
    )
    await db_session.commit()

    # A generous operator audit window (730d) means this 100d-old row must survive —
    # NO field/code path in this task ever touches audit_events regardless of the
    # tenant's window_days=1 or zdr_enabled=true.
    sweeper = _make_sweeper(app)
    sweeper._settings.retention_audit_events_days = 730  # type: ignore[attr-defined]
    sweeper._settings.retention_audit_floor_days = 365  # type: ignore[attr-defined]
    await sweeper.sweep_once()

    check = await db_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE id = :id"), {"id": audit_id}
    )
    assert check.scalar() == 1, "audit_events must be untouched by window_days/zdr_enabled"

    get_resp = await client.get(RETENTION_POLICY, headers=_bearer(jwt))
    assert "audit_events" not in get_resp.json()["effective_window_days"]


# ===========================================================================
# M7 (edge case) — disabling ZDR does not retroactively restore purged rows
# ===========================================================================


async def test_disable_zdr_does_not_restore_purged_rows(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrDisable", email="owner@zdrdis.io")
    key_info = await _create_key(client, jwt, name="k1")
    key_id = key_info["key_id"]

    mem_repo = MemoryRepository(db_session)
    row = await mem_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content="gone-soon", meta=None
    )
    await db_session.commit()

    await client.put(RETENTION_POLICY, json={"zdr_enabled": True}, headers=_bearer(jwt))
    sweeper = _make_sweeper(app)
    await sweeper.sweep_once()

    count = await db_session.execute(
        text("SELECT COUNT(*) FROM memories WHERE id = :id"), {"id": row.id}
    )
    assert count.scalar() == 0

    disable_resp = await client.put(
        RETENTION_POLICY, json={"zdr_enabled": False}, headers=_bearer(jwt)
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["zdr_enabled"] is False

    count_after = await db_session.execute(
        text("SELECT COUNT(*) FROM memories WHERE id = :id"), {"id": row.id}
    )
    assert count_after.scalar() == 0, "a real DELETE was never a soft-delete — nothing to restore"

    # New writes are accepted again once ZDR is off.
    new_row = await mem_repo.create(
        tenant_id=uuid.UUID(tid), key_id=uuid.UUID(key_id), content="fresh", meta=None
    )
    await db_session.commit()
    assert new_row.id is not None


# ===========================================================================
# M9 — is_zdr() read port
# ===========================================================================


async def test_is_zdr_read_port(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _jwt, tid = await _signup_owner(client, tenant_name="ZdrPort", email="owner@zdrport.io")

    assert await is_zdr(db_session, uuid.UUID(tid)) is False

    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)
    assert await is_zdr(db_session, uuid.UUID(tid)) is True

    # Unknown tenant_id — fail-open False (never raises), the fresh-read contract holds.
    assert await is_zdr(db_session, uuid.uuid4()) is False


# ===========================================================================
# VERIFY refute-read repro (added at verify, not build) — see TASK.md §6 FINDINGS.
# The ZDR gate lives inside ArtifactRepository.create() (raise_if_zdr), but the S3
# object PUT in artifacts/api/router.py:create_artifact happens BEFORE repo.create()
# is ever called (router.py:240-260, "write the OBJECT first, then commit the row").
# Under zdr_enabled=true this write-path ordering means the payload bytes are
# durably persisted to the object store even though the DB row (and therefore the
# sweeper's ZDR purge pass, which only iterates rows in `artifacts`) never exists to
# clean them up. This is a payload-bearing write path the §0 GROUND inventory did not
# examine (it inventoried STORES, not the ORDER OF OPERATIONS within a call chain).
# ===========================================================================


async def test_zdr_blocks_artifact_upload_but_s3_object_already_written(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    jwt, tid = await _signup_owner(client, tenant_name="ZdrS3Orphan", email="owner@zdrs3orphan.io")
    key_info = await _create_key(client, jwt, name="k1")
    await _set_tenant_zdr(db_session, tid, zdr_enabled=True)

    fake_store = FakeObjectStore()
    app.state.object_store = fake_store
    try:
        resp = await client.post(
            ARTIFACTS,
            json={"name": "f.txt", "content_type": "text/plain", "content_base64": "aGVsbG8="},
            headers=_bearer(key_info["key"]),
        )
        assert_problem(resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")

        post = await db_session.execute(
            text("SELECT COUNT(*) FROM artifacts WHERE tenant_id = :tid"), {"tid": tid}
        )
        assert post.scalar() == 0, "no ArtifactRow may be created — this half of R3 holds"

        # The actual defect: the payload bytes were already durably written to the
        # object store BEFORE raise_if_zdr() (inside repo.create()) ever ran, and
        # since no ArtifactRow exists, no sweeper pass will ever find/delete this
        # object — it is orphaned in the object store for as long as ZDR is enabled.
        assert fake_store.store == {}, (
            f"ZDR-BLOCKED artifact upload still wrote {len(fake_store.store)} object(s) "
            "to the object store before the gate fired (router.py store.put() precedes "
            "repo.create()'s raise_if_zdr) — a real payload-content persistence despite "
            "zdr_enabled=true, unreachable by the ZDR purge pass (no DB row to key off)."
        )
    finally:
        app.state.object_store = None
