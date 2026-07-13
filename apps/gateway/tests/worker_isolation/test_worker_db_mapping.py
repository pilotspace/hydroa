"""Red/green TDD for the per-xdist-worker isolation source of truth.

ci-pytest-xdist-parallel: under `pytest -n N`, each worker MUST own a private Redis
logical db and a private Postgres database, else concurrently-running workers destroy
each other's state (the per-test Postgres drop_all/create_all and the autouse Redis
full-db clear). These tests pin the mapping contract of tests/_redis_env.py:

  - non-xdist run  → legacy db 9 + the base gateway_test database (back-compat)
  - worker gwN     → a distinct db in [1, 15] (never db 0) + gateway_test_gwN
  - across the max supported worker fleet the dbs and database URLs are all distinct
"""

from __future__ import annotations

import pytest
from tests import _redis_env



@pytest.fixture(autouse=True)
def _clear_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each case controls PYTEST_XDIST_WORKER explicitly; start from a clean slate."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def test_non_xdist_uses_legacy_db_and_base_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # No worker env → the historical single-process values, unchanged.
    assert _redis_env.redis_db() == 9
    assert _redis_env.redis_url().endswith("/9")
    assert _redis_env.database_url().endswith("/gateway_test")


def test_master_id_is_treated_as_non_xdist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "master")
    assert _redis_env.redis_db() == 9
    assert _redis_env.database_url().endswith("/gateway_test")


@pytest.mark.parametrize(
    ("worker", "expected_db", "expected_db_suffix"),
    [
        ("gw0", 1, "/gateway_test_gw0"),
        ("gw1", 2, "/gateway_test_gw1"),
        ("gw3", 4, "/gateway_test_gw3"),
        ("gw11", 12, "/gateway_test_gw11"),
    ],
)
def test_worker_maps_to_private_db_and_database(
    monkeypatch: pytest.MonkeyPatch,
    worker: str,
    expected_db: int,
    expected_db_suffix: str,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    assert _redis_env.redis_db() == expected_db
    assert _redis_env.redis_url().endswith(f"/{expected_db}")
    assert _redis_env.database_url().endswith(expected_db_suffix)


def test_worker_db_never_collides_with_reserved_db_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    for n in range(15):
        monkeypatch.setenv("PYTEST_XDIST_WORKER", f"gw{n}")
        assert _redis_env.redis_db() != 0
        assert 1 <= _redis_env.redis_db() <= 15


def test_fleet_dbs_and_databases_are_all_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    dbs: set[int] = set()
    urls: set[str] = set()
    for n in range(12):  # a -n 12 fleet on this 12-core host
        monkeypatch.setenv("PYTEST_XDIST_WORKER", f"gw{n}")
        dbs.add(_redis_env.redis_db())
        urls.add(_redis_env.database_url())
    assert len(dbs) == 12, "each worker must get its own Redis db"
    assert len(urls) == 12, "each worker must get its own Postgres database"
