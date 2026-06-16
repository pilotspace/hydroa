"""Red suite for the openrouter_base_url Settings knob (v6-live-verify §4).

Covers the three gateway-source changes introduced by v6-live-verify:
  BU1 — Settings.openrouter_base_url default pin ("https://openrouter.ai/api/v1")
  BU2 — Settings.openrouter_base_url env override via constructor kwarg
  BU3 — OpenRouterCompletionUpstream.__init__ accepts base_url; stores as _base_url
  BU4 — create_app() threads settings.openrouter_base_url into completion_upstream
  BU5 — create_app() with default settings wires production URL (no regression)

Harness artifacts (scripts/v6_fault_stub.py, scripts/live_v6_verify.py,
infra/docker-compose.e2e.v6.yml) are NOT unit-tested; their evidence is the live
double-pass run (live_v5 precedent; stated in §3 of TASK.md).

RED reason for all five tests before BUILD:
  BU1/BU2: Settings has no openrouter_base_url field → AttributeError on attribute access.
  BU3:     OpenRouterCompletionUpstream.__init__ has no base_url parameter → TypeError.
  BU4/BU5: Either AttributeError (field absent from Settings) or TypeError (constructor
           rejects base_url kwarg) propagates through create_app().

Zero skips. All FAILED for the right reason.
"""

from __future__ import annotations

import pytest

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PRODUCTION_BASE_URL = "https://openrouter.ai/api/v1"
_STUB_BASE_URL = "http://stub.local:9920/api/v1"


def _make_settings(**kwargs: object) -> Settings:
    """Build a minimal Settings for unit tests (no live DB/Redis needed).

    Mirrors the helper in tests/retry_policy_wiring/ — same pattern.
    """
    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BU1 — Settings.openrouter_base_url default pin
# ---------------------------------------------------------------------------


def test_bu1_settings_default_base_url() -> None:
    """Settings() with no override must default to the production OpenRouter URL.

    RED reason: Settings has no openrouter_base_url field → AttributeError.
    This test will FAIL (not skip) until config.py gains the field.
    """
    settings = _make_settings()
    # This line must raise AttributeError before the field is added.
    actual = settings.openrouter_base_url  # type: ignore[attr-defined]
    assert actual == _PRODUCTION_BASE_URL, (
        f"Expected openrouter_base_url=={_PRODUCTION_BASE_URL!r}, got {actual!r}. "
        "The default must be byte-identical to the prior _BASE_URL module constant."
    )


# ---------------------------------------------------------------------------
# BU2 — Settings.openrouter_base_url env override
# ---------------------------------------------------------------------------


def test_bu2_settings_env_override() -> None:
    """Settings constructed with an explicit override must reflect the override.

    RED reason: Settings has no openrouter_base_url field → ValidationError or
    the field is silently ignored (extra="ignore" in model_config). Either way the
    assertion below will fail because accessing the attribute raises AttributeError.
    """
    settings = _make_settings(openrouter_base_url=_STUB_BASE_URL)
    actual = settings.openrouter_base_url  # type: ignore[attr-defined]
    assert actual == _STUB_BASE_URL, (
        f"Expected openrouter_base_url=={_STUB_BASE_URL!r}, got {actual!r}. "
        "The env override must propagate through Settings."
    )


# ---------------------------------------------------------------------------
# BU3 — OpenRouterCompletionUpstream constructor stores base_url as _base_url
# ---------------------------------------------------------------------------


def test_bu3_upstream_constructor_stores_base_url() -> None:
    """OpenRouterCompletionUpstream(base_url=...) must store _base_url.

    Credential-resolution-seam BUILD: api_key removed from __init__; credential
    is now supplied via the request-scoped contextvar at call time.
    """
    upstream = OpenRouterCompletionUpstream(
        base_url=_STUB_BASE_URL,
    )
    # _base_url must be stored as an instance attribute
    assert hasattr(upstream, "_base_url"), (
        "OpenRouterCompletionUpstream must store base_url as self._base_url"
    )
    assert upstream._base_url == _STUB_BASE_URL, (  # type: ignore[attr-defined]
        f"Expected _base_url=={_STUB_BASE_URL!r}, got {upstream._base_url!r}"  # type: ignore[attr-defined]
    )


def test_bu3b_upstream_default_base_url() -> None:
    """OpenRouterCompletionUpstream with no base_url must default to the production URL.

    Credential-resolution-seam BUILD: api_key removed from __init__.
    """
    upstream = OpenRouterCompletionUpstream()
    assert hasattr(upstream, "_base_url"), (
        "OpenRouterCompletionUpstream must expose _base_url even with default"
    )
    assert upstream._base_url == _PRODUCTION_BASE_URL, (  # type: ignore[attr-defined]
        f"Expected default _base_url=={_PRODUCTION_BASE_URL!r}, got {upstream._base_url!r}"  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# BU4 — create_app() threads custom base_url through to completion_upstream
# ---------------------------------------------------------------------------


def test_bu4_create_app_wires_custom_base_url() -> None:
    """create_app() with openrouter_base_url override must thread it into completion_upstream.

    RED reason: Either:
      (a) Settings has no openrouter_base_url field → AttributeError in _make_settings
          or AttributeError when create_app reads it.
      (b) OpenRouterCompletionUpstream.__init__ rejects the base_url kwarg → TypeError.
      (c) The kwarg is not passed by create_app → _base_url is absent or wrong value.
    All three are FAIL outcomes, not skips.
    """
    settings = _make_settings(openrouter_base_url=_STUB_BASE_URL)
    app = create_app(settings)

    # v9: the raw OpenRouter adapter relocated to app.state.openrouter_completion_upstream
    # (app.state.completion_upstream is now the provider-dispatch wrapper). base_url threading
    # is unchanged; assert it against the raw adapter.
    upstream = app.state.openrouter_completion_upstream
    assert isinstance(upstream, OpenRouterCompletionUpstream), (
        f"Expected openrouter_completion_upstream to be OpenRouterCompletionUpstream, got {type(upstream)}"
    )
    assert hasattr(upstream, "_base_url"), (
        "completion_upstream must expose _base_url after create_app()"
    )
    assert upstream._base_url == _STUB_BASE_URL, (  # type: ignore[attr-defined]
        f"Expected completion_upstream._base_url=={_STUB_BASE_URL!r}, got {upstream._base_url!r}"  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# BU5 — create_app() with default settings wires production URL (no regression)
# ---------------------------------------------------------------------------


def test_bu5_create_app_default_base_url_no_regression() -> None:
    """create_app() with all defaults must wire the production OpenRouter URL.

    This is the non-regression guard: if the default changes, this test catches it
    before it reaches a production deployment.

    RED reason: AttributeError on upstream._base_url (attribute absent before BUILD).
    After BUILD: must be green with value == "https://openrouter.ai/api/v1".
    """
    settings = _make_settings()  # no openrouter_base_url override
    app = create_app(settings)

    # v9: raw OpenRouter adapter relocated to app.state.openrouter_completion_upstream.
    upstream = app.state.openrouter_completion_upstream
    assert isinstance(upstream, OpenRouterCompletionUpstream), (
        f"Expected openrouter_completion_upstream to be OpenRouterCompletionUpstream, got {type(upstream)}"
    )
    assert hasattr(upstream, "_base_url"), (
        "completion_upstream must expose _base_url after create_app() with defaults"
    )
    assert upstream._base_url == _PRODUCTION_BASE_URL, (  # type: ignore[attr-defined]
        f"Non-regression guard: expected _base_url=={_PRODUCTION_BASE_URL!r} "
        f"(default must be byte-identical to prior _BASE_URL constant), "
        f"got {upstream._base_url!r}"  # type: ignore[attr-defined]
    )
