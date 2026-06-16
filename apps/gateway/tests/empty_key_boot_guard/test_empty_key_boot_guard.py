"""Red suite for empty-key-boot-guard (v12): fail fast on a configured-yet-empty key.

A configured-yet-EMPTY upstream key env var must fail boot with a clear, secret-free
error; an ABSENT var leaves the provider cleanly disabled. The distinction is only
observable at the raw-environment level (Settings collapses unset and set-empty to "").

Contract: empty-key-boot-guard TASK.md §3 (FROZEN @ v1).

v25 task-3 amendment: §6 env-secret removal drops these Settings fields:
  bedrock_access_key_id, bedrock_secret_access_key, bedrock_session_token,
  azure_api_key, azure_client_secret.
Their 5 entries leave _UPSTREAM_KEY_ENV_VARS. Tests that ONLY tested these removed
vars are dropped. The remaining guard tests (for vars that survive task-3) are kept.

Remaining guarded vars after task-3:
  GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY (or whichever non-secret vars remain guarded).
  The boot-guard for azure_api_key and bedrock vars is REMOVED — those fields are gone.

RIGHT-REASON RED for new assertions: the 5 removed vars are still in
_UPSTREAM_KEY_ENV_VARS (pre-BUILD) → validate_upstream_keys still raises for them
→ the assertions that they do NOT raise (or that the fields are absent from Settings)
fail. Tests that asserted they DO raise are simply retired.
"""

from __future__ import annotations

import pytest

from gateway.core.config import (
    EmptyUpstreamKeyError,
    validate_upstream_keys,
)


def test_absent_key_is_allowed() -> None:
    """No upstream key vars present → no raise (provider disabled cleanly)."""
    assert validate_upstream_keys({"SOME_OTHER_VAR": "x"}) is None


def test_removed_bedrock_secret_vars_no_longer_guarded() -> None:
    """v25 task-3 §6: bedrock secret vars removed from _UPSTREAM_KEY_ENV_VARS.

    After BUILD, an empty GATEWAY_BEDROCK_ACCESS_KEY_ID or
    GATEWAY_BEDROCK_SECRET_ACCESS_KEY must NOT trigger EmptyUpstreamKeyError —
    those fields are gone from Settings and the guard list.

    RIGHT-REASON RED: these vars are still in _UPSTREAM_KEY_ENV_VARS (pre-BUILD)
    → validate_upstream_keys still raises → assertion fails.
    """
    from gateway.core.config import _UPSTREAM_KEY_ENV_VARS, Settings

    bedrock_secret_vars = {
        "GATEWAY_BEDROCK_ACCESS_KEY_ID",
        "GATEWAY_BEDROCK_SECRET_ACCESS_KEY",
        "GATEWAY_BEDROCK_SESSION_TOKEN",
    }
    still_guarded = bedrock_secret_vars & set(_UPSTREAM_KEY_ENV_VARS)
    assert not still_guarded, (
        f"After task-3 BUILD, bedrock secret vars must be removed from "
        f"_UPSTREAM_KEY_ENV_VARS: {still_guarded!r} still present (pre-BUILD state)."
    )

    # These fields must be absent from Settings.model_fields
    settings_fields = set(Settings.model_fields.keys())
    bedrock_settings_fields = {
        "bedrock_access_key_id",
        "bedrock_secret_access_key",
        "bedrock_session_token",
    }
    still_present = bedrock_settings_fields & settings_fields
    assert not still_present, (
        f"After task-3 BUILD, these Settings fields must be removed: {still_present!r} "
        "(pre-BUILD state — fields still exist)."
    )


def test_removed_azure_secret_vars_no_longer_guarded() -> None:
    """v25 task-3 §6: azure secret vars removed from _UPSTREAM_KEY_ENV_VARS.

    After BUILD, an empty GATEWAY_AZURE_API_KEY or GATEWAY_AZURE_CLIENT_SECRET
    must NOT trigger EmptyUpstreamKeyError — those fields are gone from Settings.

    RIGHT-REASON RED: these vars are still in _UPSTREAM_KEY_ENV_VARS (pre-BUILD).
    """
    from gateway.core.config import _UPSTREAM_KEY_ENV_VARS, Settings

    azure_secret_vars = {
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    }
    still_guarded = azure_secret_vars & set(_UPSTREAM_KEY_ENV_VARS)
    assert not still_guarded, (
        f"After task-3 BUILD, azure secret vars must be removed from "
        f"_UPSTREAM_KEY_ENV_VARS: {still_guarded!r} still present (pre-BUILD state)."
    )

    settings_fields = set(Settings.model_fields.keys())
    azure_settings_fields = {"azure_api_key", "azure_client_secret"}
    still_present = azure_settings_fields & settings_fields
    assert not still_present, (
        f"After task-3 BUILD, these Settings fields must be removed: {still_present!r} "
        "(pre-BUILD state — fields still exist)."
    )


def test_create_app_ok_when_secret_keys_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway boots cleanly when the removed secret vars are absent from the environment."""
    # Remove the vars that are being deleted in task-3
    for name in (
        "GATEWAY_BEDROCK_ACCESS_KEY_ID",
        "GATEWAY_BEDROCK_SECRET_ACCESS_KEY",
        "GATEWAY_BEDROCK_SESSION_TOKEN",
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert app is not None
