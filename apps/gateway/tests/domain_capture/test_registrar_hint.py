"""Red->green suite for the registrar-hint endpoint (registrar-hint TASK.md §3/§4 — FROZEN
@ v1). One test per §4 test_plan bullet (12 of them, one per §2 scenario). Asserts
observable behavior only: HTTP status, response body fields, resolver-spy call counts,
DB-statement counts — never internal implementation details.

Coverage target: 90% (mirrors this component's existing domain_capture suite convention).

`FakeNsResolver` is THIS suite's own test double (mirrors `FakeDnsResolver` in conftest.py
exactly, for the NEW `DnsNsResolver` port) — installed at `app.state.dns_ns_resolver`, the
test-injection seam `get_dns_ns_resolver` (api/deps.py) checks first.
"""

from __future__ import annotations

import inspect
import time
import uuid
from typing import Any

import httpx
import pytest
from redis.exceptions import RedisError
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.domain_capture.domain.errors import (
    DnsLookupFailedError,
    DomainCaptureError,
    NsLookupFailedError,
)
from gateway.domain_capture.domain.ports import DnsTxtResolver
from gateway.domain_capture.domain.registrar_map import REGISTRAR_SUFFIX_MAP
from gateway.domain_capture.infrastructure.dns_resolver import (
    DnsPythonNsResolver,
    DnsPythonTxtResolver,
)
from gateway.domain_capture.infrastructure.rate_limiter import DomainClaimRateLimiter
from gateway.tenants.domain.entities import Role

from .conftest import DOMAIN_CLAIMS, bearer, issue_token, signup_and_login

pytestmark = pytest.mark.asyncio

REGISTRAR_HINT = f"{DOMAIN_CLAIMS}/registrar-hint"

_CLOUDFLARE_URL = next(
    hint.deep_link_url for suffix, hint in REGISTRAR_SUFFIX_MAP if suffix == "cloudflare.com"
)


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    return body


async def _claim_row_count(db: AsyncSession) -> int:
    return int(
        (await db.execute(text("SELECT count(*) FROM tenant_domain_claims"))).scalar_one()
    )


class FakeNsResolver:
    """Deterministic DnsNsResolver test double — no real network IO (mirrors
    `FakeDnsResolver` in conftest.py exactly, for the NEW NS-record port).

    `set_nameservers(name, [...])` — the next lookup for `name` returns that list.
    `fail(name)` — the next lookup for `name` raises NsLookupFailedError.
    `sleep(name, seconds)` — the next lookup for `name` sleeps `seconds` before returning
    (or raising, if also `fail`-configured) — used to exercise M3's bounded-timeout path.
    Any unconfigured name returns `[]` (simulates a clean NXDOMAIN/empty answer -> a miss).
    `calls` records every `name` this resolver was invoked with, in order — lets a test
    assert the resolver was (or was not) invoked at all.
    """

    def __init__(self) -> None:
        self._records: dict[str, list[str]] = {}
        self._fail_names: set[str] = set()
        self._sleep_seconds: dict[str, float] = {}
        self.calls: list[str] = []

    def set_nameservers(self, name: str, nameservers: list[str]) -> None:
        self._records[name] = nameservers

    def fail(self, name: str) -> None:
        self._fail_names.add(name)

    def sleep(self, name: str, seconds: float) -> None:
        self._sleep_seconds[name] = seconds

    async def lookup_ns(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — mirrors the real DnsNsResolver Protocol's own signature
        import asyncio

        self.calls.append(name)
        if name in self._sleep_seconds:
            await asyncio.sleep(self._sleep_seconds[name])
        if name in self._fail_names:
            raise NsLookupFailedError(f"stub: simulated NS lookup failure for {name!r}")
        return list(self._records.get(name, []))


class _ExplodingRedis:
    """Async Redis double whose INCR always raises — exercises DomainClaimRateLimiter's
    own fail-open path (mirrors tests/keys/test_mint_rate_limiter.py's own precedent)."""

    async def incr(self, key: str) -> int:
        raise RedisError("redis down")

    async def expire(self, key: str, seconds: int) -> None:  # pragma: no cover - never reached
        raise RedisError("redis down")


@pytest.fixture
def fake_ns(app: Any) -> FakeNsResolver:
    resolver = FakeNsResolver()
    app.state.dns_ns_resolver = resolver
    return resolver


# ===========================================================================
# 1. NS lookup matches a known registrar  (M1, M5)
# ===========================================================================


async def test_registrar_hint_matches_known_registrar(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-match.io"
    )
    before = await _claim_row_count(db_session)
    fake_ns.set_nameservers(
        "example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    )

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "example.com"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "domain": "example.com",
        "registrar": "Cloudflare",
        "deep_link_url": _CLOUDFLARE_URL,
        "fallback": False,
    }
    assert await _claim_row_count(db_session) == before, (
        "no domain_claims row was created, read, or mutated"
    )


# ===========================================================================
# 2. Domain validated before any DNS IO  (M2, R1)
# ===========================================================================


async def test_registrar_hint_rejects_invalid_domain_before_dns_io(
    client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-invalid.io"
    )

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "not_a_domain"}, headers=bearer(token)
    )

    _assert_problem(resp, 400, "ERR_DOMAIN_INVALID")
    assert fake_ns.calls == [], "the resolver must never be invoked on an invalid domain"


# ===========================================================================
# 3. NS lookup is bounded by a timeout  (M3)
# ===========================================================================


async def test_registrar_hint_lookup_bounded_by_timeout(
    make_second_app_client: Any,
) -> None:
    application, client = make_second_app_client(registrar_hint_dns_timeout_seconds=0.2)
    fake_ns = FakeNsResolver()
    fake_ns.sleep("slow-ns.example", seconds=5.0)
    application.state.dns_ns_resolver = fake_ns
    tenant_id = uuid.uuid4()
    token = issue_token(application, role=Role.OWNER, tenant_id=tenant_id)

    try:
        async with client:
            started = time.monotonic()
            resp = await client.get(
                REGISTRAR_HINT, params={"domain": "slow-ns.example"}, headers=bearer(token)
            )
            elapsed = time.monotonic() - started
    finally:
        await application.state.engine.dispose()

    # TIME BUDGET: good path ~0.2s (registrar_hint_dns_timeout_seconds), bad path 5.0s
    # (FakeNsResolver's injected sleep). 1.5s sits between them, 7.5x over the good path.
    assert elapsed < 1.5, f"lookup was not bounded by the timeout knob: took {elapsed}s"
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "domain": "slow-ns.example",
        "registrar": None,
        "deep_link_url": None,
        "fallback": True,
    }


# ===========================================================================
# 4. Fail-open on NS lookup failure  (M4, REQUIRED)
# ===========================================================================


async def test_registrar_hint_fails_open_on_lookup_failure(
    client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-failopen.io"
    )
    fake_ns.fail("broken.example")

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "broken.example"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "domain": "broken.example",
        "registrar": None,
        "deep_link_url": None,
        "fallback": True,
    }


# ===========================================================================
# 5. NS lookup succeeds with no known-registrar match  (M6)
# ===========================================================================


async def test_registrar_hint_fails_open_on_no_match(
    client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-nomatch.io"
    )
    fake_ns.set_nameservers("obscure-host.example", ["ns1.some-unlisted-host.net"])

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "obscure-host.example"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "domain": "obscure-host.example",
        "registrar": None,
        "deep_link_url": None,
        "fallback": True,
    }, "a clean miss must be byte-identical in shape to the lookup-failure case"


# ===========================================================================
# 6. Rate limit enforced  (M7, R3)
# ===========================================================================


async def test_registrar_hint_rate_limited(make_second_app_client: Any) -> None:
    application, client = make_second_app_client(domain_claim_registrar_hint_rpm=1)
    fake_ns = FakeNsResolver()
    fake_ns.set_nameservers("ratelimit-hint.example", ["ns1.cloudflare.com"])
    application.state.dns_ns_resolver = fake_ns
    # SAME token/tenant for both calls -> same fixed-window rate-limit bucket.
    token = issue_token(application, role=Role.OWNER, tenant_id=uuid.uuid4())

    try:
        async with client:
            first = await client.get(
                REGISTRAR_HINT,
                params={"domain": "ratelimit-hint.example"},
                headers=bearer(token),
            )
            assert first.status_code == 200, first.text

            second = await client.get(
                REGISTRAR_HINT,
                params={"domain": "ratelimit-hint.example"},
                headers=bearer(token),
            )
    finally:
        await application.state.engine.dispose()

    _assert_problem(second, 429, "ERR_RATE_LIMITED")
    assert second.headers.get("Retry-After") is not None
    assert fake_ns.calls == ["ratelimit-hint.example"], (
        "the resolver must NOT be invoked for the rate-limited call"
    )


# ===========================================================================
# 7. Rate limiter fails open on backend outage  (M7 elaboration)
# ===========================================================================


async def test_registrar_hint_rate_limiter_fails_open_on_redis_outage(
    app: Any, client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-redis-outage.io"
    )
    app.state.domain_claim_rate_limiter = DomainClaimRateLimiter(redis=_ExplodingRedis())
    fake_ns.set_nameservers("redis-outage.example", ["ns1.cloudflare.com"])

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "redis-outage.example"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "domain": "redis-outage.example",
        "registrar": "Cloudflare",
        "deep_link_url": _CLOUDFLARE_URL,
        "fallback": False,
    }, "a Redis outage must NOT block the request; normal match rules still apply"


# ===========================================================================
# 8. Owner-only authorization enforced  (M8, R2)
# ===========================================================================


async def test_registrar_hint_requires_owner_role(
    app: Any, client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    tenant_id, _owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-authz.io"
    )
    admin_token = issue_token(app, role=Role.ADMIN, tenant_id=tenant_id)

    forbidden = await client.get(
        REGISTRAR_HINT, params={"domain": "authz-check.example"}, headers=bearer(admin_token)
    )
    _assert_problem(forbidden, 403, "ERR_AUTH_FORBIDDEN")

    unauthenticated = await client.get(
        REGISTRAR_HINT, params={"domain": "authz-check.example"}
    )
    _assert_problem(unauthenticated, 401, "ERR_AUTH_INVALID_TOKEN")

    assert fake_ns.calls == [], "the resolver must never be invoked for a rejected caller"


# ===========================================================================
# 9. deep_link_url is never derived from the DNS answer  (M9)
# ===========================================================================


async def test_registrar_hint_deep_link_never_derived_from_ns_answer(
    client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-ssrf.io"
    )
    fake_ns.set_nameservers("ssrf-check.example", ["ns1.cloudflare.com"])

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "ssrf-check.example"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deep_link_url"] == _CLOUDFLARE_URL, "must be the curated literal, byte-identical"
    assert "ns1.cloudflare.com" not in body["deep_link_url"], (
        "the resolved nameserver hostname must never be embedded in the response URL"
    )


# ===========================================================================
# 10. New NS-lookup chain is additive, TXT-verify chain is unaffected  (M10)
# ===========================================================================


async def test_registrar_hint_additive_existing_verify_suite_untouched() -> None:
    """Structural half of M10: the existing DnsTxtResolver Protocol / DnsPythonTxtResolver
    adapter / DnsLookupFailedError class are byte-unchanged (same signatures, same class
    hierarchy) and the new NsLookupFailedError is a deliberately DISTINCT class, never a
    subclass relationship with DnsLookupFailedError. The BEHAVIORAL half — every existing
    test_domain_capture.py test still green — is proven by running that suite unmodified
    after this build lands (not re-invoked here to avoid a pytest-within-pytest call)."""
    assert list(inspect.signature(DnsTxtResolver.lookup_txt).parameters) == [
        "self",
        "name",
        "timeout",
    ]
    assert list(inspect.signature(DnsPythonTxtResolver.lookup_txt).parameters) == [
        "self",
        "name",
        "timeout",
    ]
    assert DnsLookupFailedError.__bases__ == (DomainCaptureError,)
    assert not issubclass(NsLookupFailedError, DnsLookupFailedError)
    assert not issubclass(DnsLookupFailedError, NsLookupFailedError)
    # The new NS port/adapter exist alongside, with their OWN distinct error type.
    assert list(inspect.signature(DnsPythonNsResolver.lookup_ns).parameters) == [
        "self",
        "name",
        "timeout",
    ]


# ===========================================================================
# 11. Deterministic first-match on multiple NS records  (M11, edge case)
# ===========================================================================


async def test_registrar_hint_first_match_wins_on_multiple_ns(
    client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-firstmatch.io"
    )
    fake_ns.set_nameservers(
        "mixed.example", ["ns1.unknown-host.net", "ns2.cloudflare.com"]
    )

    resp = await client.get(
        REGISTRAR_HINT, params={"domain": "mixed.example"}, headers=bearer(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registrar"] == "Cloudflare"
    assert body["fallback"] is False


# ===========================================================================
# 12. Zero database IO  (M12, edge case)
# ===========================================================================


async def test_registrar_hint_zero_db_io(
    app: Any, client: httpx.AsyncClient, fake_ns: FakeNsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@registrar-hint-zerodb.io"
    )
    fake_ns.set_nameservers("zero-db-match.example", ["ns1.cloudflare.com"])
    fake_ns.set_nameservers("zero-db-miss.example", ["ns1.unlisted-host.net"])
    fake_ns.fail("zero-db-fail.example")

    statements: list[str] = []

    def _listener(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    sync_engine = app.state.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _listener)
    try:
        match_resp = await client.get(
            REGISTRAR_HINT,
            params={"domain": "zero-db-match.example"},
            headers=bearer(token),
        )
        miss_resp = await client.get(
            REGISTRAR_HINT,
            params={"domain": "zero-db-miss.example"},
            headers=bearer(token),
        )
        fail_resp = await client.get(
            REGISTRAR_HINT,
            params={"domain": "zero-db-fail.example"},
            headers=bearer(token),
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _listener)

    assert match_resp.status_code == 200, match_resp.text
    assert miss_resp.status_code == 200, miss_resp.text
    assert fail_resp.status_code == 200, fail_resp.text
    assert statements == [], f"expected ZERO DB statements issued, got: {statements}"
