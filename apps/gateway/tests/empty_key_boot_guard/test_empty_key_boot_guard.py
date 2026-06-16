"""Red suite for empty-key-boot-guard (v12): fail fast on a configured-yet-empty key.

A configured-yet-EMPTY upstream key env var must fail boot with a clear, secret-free
error; an ABSENT var leaves the provider cleanly disabled. The distinction is only
observable at the raw-environment level (Settings collapses unset and set-empty to "").

Contract: empty-key-boot-guard TASK.md §3 (FROZEN @ v1).
"""

from __future__ import annotations

import pytest

from gateway.core.config import (
    EmptyUpstreamKeyError,
    validate_upstream_keys,
)


def test_present_empty_key_raises() -> None:
    # Credential-resolution-seam BUILD: Bearer vars removed from guard list.
    # Guard now covers Bedrock/Azure env-path vars (staged to task 3).
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_BEDROCK_ACCESS_KEY_ID": ""})
    assert "GATEWAY_BEDROCK_ACCESS_KEY_ID" in str(exc.value)


def test_whitespace_only_key_raises() -> None:
    # Credential-resolution-seam BUILD: use a still-guarded Bedrock var.
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_BEDROCK_SECRET_ACCESS_KEY": "   "})
    assert "GATEWAY_BEDROCK_SECRET_ACCESS_KEY" in str(exc.value)


def test_absent_key_is_allowed() -> None:
    # No upstream key vars present → provider disabled, no raise.
    assert validate_upstream_keys({"SOME_OTHER_VAR": "x"}) is None


def test_nonempty_keys_pass() -> None:
    # Credential-resolution-seam BUILD: use still-guarded Bedrock/Azure vars.
    env = {
        "GATEWAY_BEDROCK_ACCESS_KEY_ID": "AKIDTEST",
        "GATEWAY_AZURE_API_KEY": "az-key",
    }
    assert validate_upstream_keys(env) is None


def test_error_message_has_fix_hint_and_no_value() -> None:
    # Credential-resolution-seam BUILD: use a still-guarded Azure var.
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_AZURE_CLIENT_SECRET": "  "})
    msg = str(exc.value)
    assert "GATEWAY_AZURE_CLIENT_SECRET" in msg
    assert "unset" in msg.lower()
    # the whitespace value must not be echoed beyond the var name + hint
    assert "  " not in msg.replace("GATEWAY_AZURE_CLIENT_SECRET", "")


def test_create_app_fails_fast_on_empty_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Credential-resolution-seam BUILD: GATEWAY_OPENROUTER_API_KEY is no longer
    # guarded (Bearer vars removed). Use a still-guarded Bedrock var instead.
    monkeypatch.setenv("GATEWAY_BEDROCK_ACCESS_KEY_ID", "")
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    with pytest.raises(EmptyUpstreamKeyError):
        create_app(settings)


def test_create_app_ok_when_keys_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure none of the remaining guarded upstream key vars are present.
    # Bearer vars (openrouter/openai/anthropic/google) removed from guard list by BUILD.
    for name in (
        "GATEWAY_BEDROCK_ACCESS_KEY_ID",
        "GATEWAY_BEDROCK_SECRET_ACCESS_KEY",
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
