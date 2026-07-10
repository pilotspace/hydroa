"""Suite-local fixtures for per-key-guardrail-policies (TASK.md §4).

Two test styles, matching the two halves of the contract:

  HTTP-level (real Postgres, httpx.ASGITransport) — CRUD + inheritance scenarios
  (M1, M2, M4-M9, R1-R4). Mirrors tests/key_governance/ and tests/guardrails/
  conventions: signup_and_login / create_key / assert_problem / member-JWT-via-pyjwt.

  Fakes-only CompletionUseCase level (no DB/Redis) — the 3 cache-hit scenarios
  (M10). Mirrors tests/cache_alias_billing/ + tests/vector_cache/ conventions:
  FakeResponseCache with REAL pointer semantics for a genuine semantic hit,
  FakeVectorCache for a vector hit, and the REAL RegexGuardrailEvaluator (only the
  cache/authenticator/upstream are fakes) so masking is exercised for real.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator

from tests.stream_usage_completeness.conftest import MarkerSpyRecorder  # noqa: F401
from tests.vector_cache.conftest import FakeModelChecker, FakeVectorCache  # noqa: F401

# ---------------------------------------------------------------------------
# HTTP-level route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_GUARDRAILS = "/admin/guardrails"


def key_guardrails_path(key_id: str) -> str:
    return f"/admin/keys/{key_id}/guardrails"


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
    password: str = "correct horse battery",
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
    name: str = "test-key",
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body."""
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def set_tenant_guardrails(
    client: httpx.AsyncClient, jwt: str, config: dict[str, Any]
) -> dict[str, Any]:
    """PUT /admin/guardrails (tenant-level); assert 200; return echoed config."""
    resp = await client.put(ADMIN_GUARDRAILS, json=config, headers=auth_jwt(jwt))
    assert resp.status_code == 200, f"PUT /admin/guardrails failed: {resp.text}"
    return resp.json()


def member_token_for(owner_token: str, *, email: str) -> str:
    """Build a member-role JWT sharing the owner's tenant (mirrors
    tests/key_governance/test_key_governance.py's test_member_cannot_rotate precedent)."""
    import jwt as pyjwt

    from tests.conftest import TEST_JWT_SECRET

    owner_claims = pyjwt.decode(
        owner_token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": owner_claims["tenant_id"],
        "email": email,
        "role": "member",
        "iss": "ai-proxy",
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    token: str = pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")
    return token


def completion_payload(model: str, content: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": content}]}


class FakeCompletionUpstream:
    """Non-streaming fake — reused shape from tests/guardrails/test_guardrails_core.py.

    Tracks received_messages so pre-call PII masking (applied to the OUTGOING
    request body before it reaches the upstream) is inspectable.
    """

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body or {
            "id": "gen-pkgp-1",
            "choices": [{"message": {"role": "assistant", "content": "hello from upstream"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.calls = 0
        self.received_messages: list[list[dict[str, Any]]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        self.received_messages.append(payload.get("messages", []))
        return self.status, self.body


# ---------------------------------------------------------------------------
# Fakes-only CompletionUseCase level (M10 — 3 cache-hit branches)
# ---------------------------------------------------------------------------
TENANT_A = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
KEY_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
MODEL = "openai/gpt-4o-mini"

# A cached response body carrying PII in its content — the payload every M10 test
# masks (or fails to mask) per the KEY's resolved override.
PII_CACHED_BODY: dict[str, Any] = {
    "id": "resp-cached-pii",
    "model": MODEL,
    "choices": [
        {"message": {"role": "assistant", "content": "Your email is user@example.com — noted."}}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


class FakeAuthenticatorWithGuardrails:
    """KeyAuthenticator stub whose AuthzResult carries a caller-supplied
    guardrail_configs — simulates what SqlAlchemyApiKeyRepository.get_by_id()
    would have ALREADY resolved (key > tenant) before CompletionUseCase.complete()
    ever runs. This suite's HTTP-level tests independently verify that resolution
    step itself (M1-M3); these fakes-only tests verify only that the RESOLVED
    value, once in AuthzResult, is honored identically on all 3 cache-hit paths
    with ZERO proxy-layer code changes (M10)."""

    def __init__(
        self,
        *,
        guardrail_configs: dict[str, Any],
        cache_enabled: bool = True,
        semantic_cache_enabled: bool = False,
    ) -> None:
        self._guardrail_configs = guardrail_configs
        self._cache_enabled = cache_enabled
        self._semantic = semantic_cache_enabled

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return AuthzResult(
            tenant_id=TENANT_A,
            key_id=KEY_ID,
            cache_enabled=self._cache_enabled,
            semantic_cache_enabled=self._semantic,
            guardrail_configs=self._guardrail_configs,
            policy_source="key",
        )


class FakePointerResponseCache:
    """Exact + semantic-pointer cache with REAL in-memory pointer semantics —
    verbatim shape from tests/cache_alias_billing/conftest.py's FakeResponseCache
    (that suite's own docstring explains why: tests.vector_cache.conftest's fake
    permanently stubs get_pointer/set_pointer to None, which cannot drive a
    genuine semantic HIT)."""

    def __init__(
        self,
        exact: dict[str, dict[str, Any]] | None = None,
        pointers: dict[str, str] | None = None,
    ) -> None:
        self.store: dict[str, dict[str, Any]] = dict(exact or {})
        self.pointers: dict[str, str] = dict(pointers or {})

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        return self.store.get(cache_key)

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.store[cache_key] = body

    async def get_pointer(self, sem_key: str) -> str | None:
        return self.pointers.get(sem_key)

    async def set_pointer(self, sem_key: str, exact_key: str, ttl_seconds: int) -> None:
        self.pointers[sem_key] = exact_key


def make_router() -> FallbackModelRouter:
    """No aliases — MODEL routes directly to itself."""
    return FallbackModelRouter(model_groups={})


def make_use_case(
    *,
    guardrail_configs: dict[str, Any],
    response_cache: Any,
    vector_cache: Any = None,
    semantic_cache_enabled: bool = False,
) -> CompletionUseCase:
    return CompletionUseCase(
        FakeAuthenticatorWithGuardrails(
            guardrail_configs=guardrail_configs,
            semantic_cache_enabled=semantic_cache_enabled,
        ),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        response_cache=response_cache,
        guardrail_evaluator=RegexGuardrailEvaluator(),
        vector_cache=vector_cache,
    )


async def run_complete(
    uc: CompletionUseCase,
    upstream: Any,
    recorder: Any,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any], str | None]:
    return await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=upstream,
        usage_recorder=recorder,
        model_router=make_router(),
    )
