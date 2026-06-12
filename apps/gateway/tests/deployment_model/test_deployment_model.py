"""Red-first suite for the v8 deployment-model task.

Freezes the Deployment config data shape (TASK.md §3 FROZEN @ v1):
  - GATEWAY_MODEL_GROUPS accepts string-or-object members per group.
  - A bare string coerces to Deployment(model_id, weight=1, tpm_limit=None, rpm_limit=None).
  - settings.deployments exposes the normalized list (NEW); settings.model_groups
    stays the bare-string view (v6 byte-identical; RA1/RA8 + consumers read it).
  - Startup validation rejects bad weight/limit/model_id/duplicate, and preserves the
    v6 rules (EMPTY_CANDIDATE_LIST / ALIAS_COLLIDES_WITH_CANDIDATE / TOO_MANY_CANDIDATES).

These run RED before build:
  - `from gateway.core.config import Deployment` → ImportError (type not built yet).
  - `settings.deployments` → AttributeError (view not built yet).
  - the new validators do not yet fire → ValidationError not raised.

The /admin/routing byte-identical guarantee (object member → bare-string response,
exact key sets) is enforced by the EXISTING frozen routing_admin suite (RA1/RA8),
which must stay green after build; DM4 here asserts the string-view invariant that
backs it without spinning up the app.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

_DB = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
_JWT = "test-secret-not-for-production-0123456789"
_REDIS = "redis://localhost:6380/9"


def _settings(**overrides: object):
    """Construct a test Settings with the required fields + overrides."""
    from gateway.core.config import Settings

    base: dict[str, object] = {
        "database_url": _DB,
        "jwt_secret": _JWT,
        "redis_url": _REDIS,
        "environment": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DM1 / DM2 — normalization (coercion + parse + order)
# ---------------------------------------------------------------------------


def test_dm1_bare_string_coerces_weight1_nolimit() -> None:
    from gateway.core.config import Deployment

    s = _settings(model_groups={"fast": ["vendor/a", "vendor/b"]})

    assert s.deployments["fast"] == [
        Deployment(model_id="vendor/a", weight=1, tpm_limit=None, rpm_limit=None),
        Deployment(model_id="vendor/b", weight=1, tpm_limit=None, rpm_limit=None),
    ]
    # bare-string view stays v6 byte-identical
    assert s.model_groups == {"fast": ["vendor/a", "vendor/b"]}


def test_dm2_object_members_parse_and_preserve_order() -> None:
    from gateway.core.config import Deployment

    s = _settings(
        model_groups={
            "fast": [
                {"model_id": "vendor/a", "weight": 3, "tpm_limit": 100000, "rpm_limit": 600},
                "vendor/b",
            ]
        }
    )

    assert s.deployments["fast"][0] == Deployment(
        model_id="vendor/a", weight=3, tpm_limit=100000, rpm_limit=600
    )
    assert s.deployments["fast"][1] == Deployment(
        model_id="vendor/b", weight=1, tpm_limit=None, rpm_limit=None
    )
    # string view: original declared order, bare ids only
    assert s.model_groups["fast"] == ["vendor/a", "vendor/b"]


def test_dm10_empty_config_feature_off() -> None:
    s = _settings()
    assert s.deployments == {}
    assert s.model_groups == {}


# ---------------------------------------------------------------------------
# DM3 — router exposes both views; behavior byte-identical to v6
# ---------------------------------------------------------------------------


def test_dm3_router_exposes_deployments_and_string_view() -> None:
    """FallbackModelRouter gains a .deployments view; .model_groups stays strings.

    Red before build: the router has no `deployments` constructor param / property.
    """
    from gateway.core.config import Deployment
    from gateway.proxy.application.fallback_router import FallbackModelRouter

    deployments = {
        "fast": [
            Deployment(model_id="vendor/a", weight=2, tpm_limit=None, rpm_limit=None),
            Deployment(model_id="vendor/b", weight=1, tpm_limit=None, rpm_limit=None),
        ]
    }
    router = FallbackModelRouter(
        upstream_factory=None,  # type: ignore[arg-type]
        model_groups={"fast": ["vendor/a", "vendor/b"]},
        deployments=deployments,
    )
    # string view unchanged (RA1/RA8 + alias-aware check read this)
    assert router.model_groups == {"fast": ["vendor/a", "vendor/b"]}
    # new normalized view, model_id order preserved
    assert [d.model_id for d in router.deployments["fast"]] == ["vendor/a", "vendor/b"]


def test_dm4_string_view_backs_admin_routing_byte_identity() -> None:
    """An object-member group still produces a bare-string model_groups view.

    This is the invariant the frozen /admin/routing RA1/RA8 tests depend on
    (body["model_groups"] == {"fast": ["vendor/a"]}, exact string lists).
    """
    s = _settings(model_groups={"fast": [{"model_id": "vendor/a", "weight": 3}]})
    assert s.model_groups == {"fast": ["vendor/a"]}


# ---------------------------------------------------------------------------
# DM5–DM8 — new validators (fail-closed at startup)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weight", [0, -1])
def test_dm5_weight_nonpositive_rejected(weight: int) -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": [{"model_id": "m", "weight": weight}]})
    assert "INVALID_DEPLOYMENT_WEIGHT" in str(exc.value)


@pytest.mark.parametrize("field", ["tpm_limit", "rpm_limit"])
def test_dm6_limit_nonpositive_rejected(field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": [{"model_id": "m", field: 0}]})
    assert "INVALID_DEPLOYMENT_LIMIT" in str(exc.value)


def test_dm7_missing_model_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": [{"weight": 2}]})
    assert "DEPLOYMENT_MODEL_ID_REQUIRED" in str(exc.value)


def test_dm8_duplicate_model_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": ["m", "m"]})
    assert "DUPLICATE_DEPLOYMENT" in str(exc.value)


# ---------------------------------------------------------------------------
# DM9 — v6 validators preserved (over the model_id view)
# ---------------------------------------------------------------------------


def test_dm9a_empty_candidate_list_preserved() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": []})
    assert "EMPTY_CANDIDATE_LIST" in str(exc.value)


def test_dm9b_alias_collision_preserved() -> None:
    # alias "m" also appears as a candidate id in the same group
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"m": ["m", "n"]})
    assert "ALIAS_COLLIDES_WITH_CANDIDATE" in str(exc.value)


def test_dm9c_too_many_candidates_preserved() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(model_groups={"g": [f"vendor/model-{i}" for i in range(6)]})
    assert "TOO_MANY_CANDIDATES" in str(exc.value)
