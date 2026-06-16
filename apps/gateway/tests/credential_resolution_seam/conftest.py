"""Fixtures and fakes for the credential_resolution_seam test suite.

Fakes defined here:
  - CountingFakeTenantProviderKeyStore — in-memory TenantProviderKeyStore whose
    .get() records call counts; can be programmed to return a credential, None,
    or sleep past the resolver timeout to trigger fail-closed behaviour.
  - FakeCredentialResolver — thin wrapper over the store; used in adapter-level
    tests that only need the contextvar set, not the full CachedTenantCredentialResolver.

All fakes are structural (satisfy the Protocol without inheriting from it) following
the established pattern in tests/provider_credential_store/test_provider_credentials.py
and tests/provider_seam/conftest.py.

No DB, no Redis, no network.  asyncio_mode = "auto" (pyproject.toml) so async fixtures
and async tests run without explicit @pytest.mark.asyncio.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Re-export the domain types we need; these EXIST (task-1 already shipped).
# ---------------------------------------------------------------------------
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderCredential

# ---------------------------------------------------------------------------
# CountingFakeTenantProviderKeyStore
# ---------------------------------------------------------------------------


class CountingFakeTenantProviderKeyStore:
    """In-memory TenantProviderKeyStore fake that counts .get() calls.

    Programmed via three modes (set via constructor / set_mode):
      - "return_cred": always returns the stored credential (default)
      - "return_none": .get() returns None (unconfigured / disabled row)
      - "sleep_forever": .get() sleeps longer than the resolver timeout
        so CachedTenantCredentialResolver's asyncio.timeout fires.

    Satisfies the TenantProviderKeyStore Protocol structurally (no inheritance).
    """

    def __init__(
        self,
        credential: ProviderCredential | None = None,
        *,
        mode: str = "return_cred",
        sleep_duration: float = 10.0,
    ) -> None:
        self._credential = credential
        self._mode = mode
        self._sleep_duration = sleep_duration
        self.get_call_count = 0
        self._store: dict[tuple[uuid.UUID, str], ProviderCredential | None] = {}

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def store_credential(
        self, tenant_id: uuid.UUID, provider: str, credential: ProviderCredential | None
    ) -> None:
        self._store[(tenant_id, provider)] = credential

    async def get(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential | None:
        self.get_call_count += 1
        if self._mode == "sleep_forever":
            await asyncio.sleep(self._sleep_duration)
        if self._mode == "return_none":
            return None
        # "return_cred" — look up per-key store first, fall back to default credential.
        stored = self._store.get((tenant_id, provider))
        if stored is not None:
            return stored
        return self._credential

    # Satisfy the full Protocol (upsert / list / delete) so isinstance checks pass.

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        credential: ProviderCredential,
        *,
        enabled: bool = True,
    ) -> None:
        self._store[(tenant_id, provider)] = credential if enabled else None

    async def list(self, tenant_id: uuid.UUID) -> list[Any]:
        return []

    async def delete(self, tenant_id: uuid.UUID, provider: str) -> bool:
        key = (tenant_id, provider)
        if key in self._store:
            del self._store[key]
            return True
        return False


# ---------------------------------------------------------------------------
# SleepyFakeTenantProviderKeyStore — for the timeout test
# ---------------------------------------------------------------------------


class SleepyFakeTenantProviderKeyStore(CountingFakeTenantProviderKeyStore):
    """Store whose .get() always sleeps past the resolver timeout."""

    def __init__(self, sleep_duration: float = 10.0) -> None:
        super().__init__(mode="sleep_forever", sleep_duration=sleep_duration)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id() -> uuid.UUID:
    """A stable tenant UUID for all tests in this suite."""
    return uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture
def openrouter_secret() -> str:
    """The raw secret that T's openrouter BearerCredential holds."""
    return "sk-or-tenant-T-secret-12345"


@pytest.fixture
def bearer_credential(openrouter_secret: str) -> BearerCredential:
    """A BearerCredential for use in tests."""
    return BearerCredential(secret=openrouter_secret)


@pytest.fixture
def counting_store(bearer_credential: BearerCredential) -> CountingFakeTenantProviderKeyStore:
    """Fake store pre-loaded with T's openrouter credential."""
    store = CountingFakeTenantProviderKeyStore(credential=bearer_credential)
    return store


@pytest.fixture
def none_returning_store() -> CountingFakeTenantProviderKeyStore:
    """Fake store that always returns None (unconfigured / disabled)."""
    return CountingFakeTenantProviderKeyStore(mode="return_none")


@pytest.fixture
def sleeping_store() -> SleepyFakeTenantProviderKeyStore:
    """Fake store whose .get() sleeps past any reasonable timeout."""
    return SleepyFakeTenantProviderKeyStore(sleep_duration=10.0)


@pytest.fixture
def min_settings() -> Any:
    """Minimal Settings with NO Bearer API key env vars set.

    Used for test_bearer_env_removed_boots_clean.
    The Settings fields (openrouter_api_key / openai_api_key / etc.) are removed
    by the BUILD phase; this fixture asserts the shape the post-BUILD Settings accepts.
    """
    from gateway.core.config import Settings

    return Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )


@pytest.fixture
def bedrock_settings() -> Any:
    """Settings with Bedrock env credentials configured (Bedrock env path stays for task 3)."""
    from gateway.core.config import Settings

    return Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        bedrock_access_key_id="AKIAEXAMPLE123",
        bedrock_secret_access_key="bedrock-secret-key-value",  # noqa: S106
        bedrock_region="us-east-1",
    )


# ---------------------------------------------------------------------------
# Structlog capture helper (mirrors cooldown_circuit/conftest.py pattern)
# ---------------------------------------------------------------------------


class CapturingProcessor:
    """structlog processor that captures ALL log events for secret-leak assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.raw_text: list[str] = []

    def __call__(self, logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        self.events.append(dict(event_dict))
        # Capture the full rendered text of the event dict so secret-leak checks
        # can scan a single string rather than inspecting each key.
        self.raw_text.append(str(event_dict))
        return event_dict

    def all_text(self) -> str:
        """Return all captured log output concatenated for substring search."""
        return "\n".join(self.raw_text)

    def contains(self, substring: str) -> bool:
        return substring in self.all_text()

    def _capture(self, method: str, event: str, **kw: Any) -> None:
        """Directly inject a log event into the processor (mirrors CapturingLogger._capture).

        Used by tests that want to assert on the captured log content without
        going through structlog.get_logger() — e.g. to verify that repr(cred)
        does not leak secrets into structured log output.
        """
        self(self, method, {"event": event, **kw})


@pytest.fixture
def log_capture(monkeypatch: pytest.MonkeyPatch) -> CapturingProcessor:
    """Capture ALL structlog events emitted during a test.

    Mirrors the pattern in tests/cooldown_circuit/conftest.py.
    """
    import structlog

    processor = CapturingProcessor()
    original_get_logger = structlog.get_logger

    class CapturingLogger:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._inner = original_get_logger(*args, **kwargs)

        def _capture(self, method: str, event: str, **kw: Any) -> None:
            processor(self, method, {"event": event, **kw})

        def debug(self, event: str, **kw: Any) -> None:
            self._capture("debug", event, **kw)

        def info(self, event: str, **kw: Any) -> None:
            self._capture("info", event, **kw)

        def warning(self, event: str, **kw: Any) -> None:
            self._capture("warning", event, **kw)

        def warn(self, event: str, **kw: Any) -> None:
            self._capture("warn", event, **kw)

        def error(self, event: str, **kw: Any) -> None:
            self._capture("error", event, **kw)

        def critical(self, event: str, **kw: Any) -> None:
            self._capture("critical", event, **kw)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    monkeypatch.setattr(structlog, "get_logger", lambda *a, **kw: CapturingLogger(*a, **kw))
    return processor


# ---------------------------------------------------------------------------
# Minimal async app fixture — no DB schema creation (no DB needed for unit tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_no_db(min_settings: Any) -> Any:
    """create_app() using min_settings — no DB schema bootstrap.

    Used by test_bearer_env_removed_boots_clean and test_error_maps_to_402.
    Tests override app.state.* fakes after creation.
    """
    from gateway.main import create_app

    return create_app(min_settings)


@pytest.fixture
async def client_no_db(app_no_db: Any) -> AsyncIterator[Any]:
    """httpx.AsyncClient over app_no_db (no DB, no lifespan)."""
    import httpx

    transport = httpx.ASGITransport(app=app_no_db)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
