"""Retirement + BYOK env-secret-absence invariants (retire-empty-key-guard TASK.md).

The empty-upstream-key boot guard (EmptyUpstreamKeyError / _UPSTREAM_KEY_ENV_VARS /
validate_upstream_keys) became a no-op once v25 task-3 emptied the guard tuple — every
provider credential is now resolved per-tenant at request time (BYOK). This task retires
the dead guard.

What remains here are the invariants the old guard's assertions weakly approximated, now
pinned against a live surface (Settings.model_fields) rather than the deleted constant:
  - no provider api_key/secret field exists on Settings, and
  - create_app boots cleanly with no provider env secrets.

test_boot_guard_symbols_retired runs RED until BUILD deletes the three symbols + the main.py call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.core.config import Settings


def test_boot_guard_symbols_retired() -> None:
    """The dead boot-guard symbols are gone from config, and main.py no longer calls the guard."""
    import gateway.core.config as config_module

    for name in ("validate_upstream_keys", "_UPSTREAM_KEY_ENV_VARS", "EmptyUpstreamKeyError"):
        assert not hasattr(config_module, name), (
            f"gateway.core.config.{name} must be retired — it is a no-op dead-code remnant of the "
            "pre-BYOK env-key boot guard (pre-BUILD state: still present)."
        )

    main_src = Path(__file__).resolve().parents[2] / "src" / "gateway" / "main.py"
    text = main_src.read_text()
    assert "validate_upstream_keys" not in text, (
        "main.py must neither import nor call validate_upstream_keys (pre-BUILD: still present)."
    )


def test_bedrock_secret_fields_absent_from_settings() -> None:
    """BYOK invariant: bedrock secret fields are gone from Settings (no env-secret path)."""
    settings_fields = set(Settings.model_fields.keys())
    bedrock_settings_fields = {
        "bedrock_access_key_id",
        "bedrock_secret_access_key",
        "bedrock_session_token",
    }
    still_present = bedrock_settings_fields & settings_fields
    assert not still_present, (
        f"These bedrock secret Settings fields must not exist (BYOK): {still_present!r}."
    )


def test_azure_secret_fields_absent_from_settings() -> None:
    """BYOK invariant: azure secret fields are gone from Settings (no env-secret path)."""
    settings_fields = set(Settings.model_fields.keys())
    azure_settings_fields = {"azure_api_key", "azure_client_secret"}
    still_present = azure_settings_fields & settings_fields
    assert not still_present, (
        f"These azure secret Settings fields must not exist (BYOK): {still_present!r}."
    )


def test_create_app_ok_when_secret_keys_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway boots cleanly when the provider secret env vars are absent from the environment."""
    for name in (
        "GATEWAY_BEDROCK_ACCESS_KEY_ID",
        "GATEWAY_BEDROCK_SECRET_ACCESS_KEY",
        "GATEWAY_BEDROCK_SESSION_TOKEN",
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert app is not None
