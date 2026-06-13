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
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_OPENROUTER_API_KEY": ""})
    assert "GATEWAY_OPENROUTER_API_KEY" in str(exc.value)


def test_whitespace_only_key_raises() -> None:
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_GOOGLE_API_KEY": "   "})
    assert "GATEWAY_GOOGLE_API_KEY" in str(exc.value)


def test_absent_key_is_allowed() -> None:
    # No upstream key vars present → provider disabled, no raise.
    assert validate_upstream_keys({"SOME_OTHER_VAR": "x"}) is None


def test_nonempty_keys_pass() -> None:
    env = {
        "GATEWAY_OPENROUTER_API_KEY": "or-key",
        "GATEWAY_GOOGLE_API_KEY": "g-key",
    }
    assert validate_upstream_keys(env) is None


def test_error_message_has_fix_hint_and_no_value() -> None:
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_ANTHROPIC_API_KEY": "  "})
    msg = str(exc.value)
    assert "GATEWAY_ANTHROPIC_API_KEY" in msg
    assert "unset" in msg.lower()
    # the whitespace value must not be echoed beyond the var name + hint
    assert "  " not in msg.replace("GATEWAY_ANTHROPIC_API_KEY", "")


def test_create_app_fails_fast_on_empty_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_OPENROUTER_API_KEY", "")
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
    # Ensure none of the upstream key vars are present → create_app builds normally.
    for name in (
        "GATEWAY_OPENROUTER_API_KEY",
        "GATEWAY_OPENAI_API_KEY",
        "GATEWAY_ANTHROPIC_API_KEY",
        "GATEWAY_GOOGLE_API_KEY",
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
