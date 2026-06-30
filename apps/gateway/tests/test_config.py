"""Fail-fast guard: the dev JWT secret must never boot a non-dev environment."""

from decimal import Decimal

import pytest

from gateway.core.config import Settings
from gateway.usage.application.drift_checker import should_start_drift_checker


def test_production_refuses_dev_jwt_secret() -> None:
    with pytest.raises(ValueError, match="GATEWAY_JWT_SECRET"):
        Settings(environment="production")


def test_production_boots_with_real_secret() -> None:
    s = Settings(environment="production", jwt_secret="a" * 48)
    assert s.environment == "production"


# ── v30 drift-threshold-validation: fail loud on a nonsense drift threshold ──


def test_default_drift_threshold_is_zero_and_off() -> None:
    """Unset threshold boots to the OFF sentinel — byte-identical to today."""
    s = Settings()
    assert s.reconciliation_drift_threshold == Decimal("0")
    assert should_start_drift_checker(s.reconciliation_drift_threshold, 60) is False


def test_positive_drift_threshold_accepted() -> None:
    s = Settings(reconciliation_drift_threshold="2.50")
    assert s.reconciliation_drift_threshold == Decimal("2.50")
    assert should_start_drift_checker(s.reconciliation_drift_threshold, 60) is True


def test_infinite_drift_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="INVALID_RECONCILIATION_DRIFT_THRESHOLD"):
        Settings(reconciliation_drift_threshold="inf")


def test_nan_drift_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="INVALID_RECONCILIATION_DRIFT_THRESHOLD"):
        Settings(reconciliation_drift_threshold="nan")


def test_negative_drift_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="INVALID_RECONCILIATION_DRIFT_THRESHOLD"):
        Settings(reconciliation_drift_threshold="-1")


# ── v33 drift-threshold-validation: fail loud on a negative check-interval ──


def test_default_check_interval_is_zero_and_off() -> None:
    """Unset interval boots to the OFF sentinel — byte-identical to today."""
    s = Settings()
    assert s.reconciliation_check_interval_seconds == 0
    assert (
        should_start_drift_checker(Decimal("1"), s.reconciliation_check_interval_seconds) is False
    )


def test_positive_check_interval_accepted() -> None:
    s = Settings(reconciliation_check_interval_seconds="60")
    assert s.reconciliation_check_interval_seconds == 60


def test_negative_check_interval_rejected() -> None:
    with pytest.raises(ValueError, match="INVALID_RECONCILIATION_CHECK_INTERVAL"):
        Settings(reconciliation_check_interval_seconds="-1")
