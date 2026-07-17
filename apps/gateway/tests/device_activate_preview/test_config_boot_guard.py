"""Config boot-guard RED suite (device-activate-page §1 M8/M9, §3, R-C).

Pure Settings instantiation — no DB, no async. The verification_uri gains a dev default
and a prod boot-guard mirroring the line-988 jwt_secret guard: production refuses to boot
on an empty / localhost / non-https verification_uri. agent_oauth_preview_rpm is a new
positive-knob (default 30).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from gateway.core.config import Settings


def _base_kwargs(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://u:p@localhost:5433/x",
        "jwt_secret": "prod-secret-not-the-dev-default-0123456789",
    }
    kw.update(over)
    return kw


def test_dev_boots_with_default_uri() -> None:
    s = Settings(**_base_kwargs(environment="dev"))
    assert s.agent_oauth_verification_uri == "http://localhost:3000/activate"


@pytest.mark.parametrize(
    "bad_uri",
    [
        "",
        "http://localhost:3000/activate",
        "http://127.0.0.1:3000/activate",
        "http://app.example/activate",  # non-https
        "https://localhost/activate",  # localhost host even over https
    ],
)
def test_prod_refuses_unsafe_uri(bad_uri: str) -> None:
    with pytest.raises(ValueError, match="INVALID_AGENT_OAUTH_VERIFICATION_URI"):
        Settings(**_base_kwargs(environment="production", agent_oauth_verification_uri=bad_uri))


def test_prod_boots_with_https_uri() -> None:
    s = Settings(
        **_base_kwargs(
            environment="production",
            agent_oauth_verification_uri="https://app.example/activate",
        )
    )
    assert s.agent_oauth_verification_uri == "https://app.example/activate"


def test_preview_rpm_positive_knob() -> None:
    with pytest.raises(ValueError, match="INVALID_AGENT_OAUTH_KNOB"):
        Settings(**_base_kwargs(environment="dev", agent_oauth_preview_rpm=0))


def test_preview_rpm_default_is_30() -> None:
    s = Settings(**_base_kwargs(environment="dev"))
    assert s.agent_oauth_preview_rpm == 30
    assert s.agent_oauth_default_budget_usd == Decimal("100.00")
