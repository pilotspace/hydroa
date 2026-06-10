"""Fail-fast guard: the dev JWT secret must never boot a non-dev environment."""

import pytest

from gateway.core.config import Settings


def test_production_refuses_dev_jwt_secret() -> None:
    with pytest.raises(ValueError, match="GATEWAY_JWT_SECRET"):
        Settings(environment="production")


def test_production_boots_with_real_secret() -> None:
    s = Settings(environment="production", jwt_secret="a" * 48)
    assert s.environment == "production"
