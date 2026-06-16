"""Red suite for azure-auth-routing (v21 task 1): Azure config + deployment URL routing.

A pure, IO-free config/routing seam consumed by the later Azure adapters. All
azure_config imports are RED until the module is implemented (ModuleNotFoundError).

CONTRACT (FROZEN @ v1): azure-auth-routing TASK.md §3
  - AzureConfig (frozen dataclass): api_key (repr-hidden SECRET), endpoint,
    api_version, deployment_map; methods resolve_deployment(model), build_url(deployment, op).
  - GATEWAY_AZURE_API_KEY in the boot-guard tuple (validate_upstream_keys).

v25 task-3 amendment: resolve_azure_config is DELETED (env-secret removal §6).
test_resolve_config_present, test_resolve_config_absent_returns_none, and
test_partial_config_returns_none are REMOVED. AzureConfig and URL-building tests kept.

Each remaining test maps 1:1 to a §2 scenario; behavior is asserted, not internals.
"""

from __future__ import annotations

import pytest

# azure_config still exists (AzureConfig remains; resolve_azure_config deleted).
from gateway.proxy.infrastructure.azure_config import AzureConfig

# EmptyUpstreamKeyError / validate_upstream_keys no longer imported:
# the GATEWAY_AZURE_API_KEY boot-guard test was deleted (v25 task-3 §6).


# resolve_azure_config tests REMOVED — v25 task-3 §6: function is DELETED.
# test_resolve_config_present, test_resolve_config_absent_returns_none,
# test_partial_config_returns_none are retired per deliverable C.

# ── AzureConfig: secret + routing + URL building ───────────────────────────


def test_api_key_not_in_repr() -> None:
    cfg = AzureConfig(
        api_key="super-secret-value",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    assert "super-secret-value" not in repr(cfg)


def test_resolve_deployment_identity() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    assert cfg.resolve_deployment("gpt-4o") == "gpt-4o"


def test_resolve_deployment_mapped() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={"gpt-4o": "prod-4o"},
    )
    assert cfg.resolve_deployment("gpt-4o") == "prod-4o"


def test_build_url_shape() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    assert cfg.build_url("prod-4o", "chat/completions") == (
        "https://r.openai.azure.com/openai/deployments/prod-4o/chat/completions"
        "?api-version=2024-10-21"
    )


def test_build_url_idempotent_trailing_slash() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com/",
        api_version="2024-10-21",
        deployment_map={},
    )
    url = cfg.build_url("prod-4o", "embeddings")
    assert "/openai/deployments/prod-4o/embeddings" in url
    assert "//openai" not in url  # no doubled slash from the trailing-slash endpoint


def test_build_url_quotes_deployment() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    url = cfg.build_url("my deploy", "chat/completions")
    assert "/openai/deployments/my%20deploy/chat/completions" in url


def test_build_url_empty_deployment_raises() -> None:
    cfg = AzureConfig(
        api_key="sk",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    with pytest.raises(ValueError, match="AZURE_DEPLOYMENT_REQUIRED"):
        cfg.build_url("", "chat/completions")
    with pytest.raises(ValueError, match="AZURE_DEPLOYMENT_REQUIRED"):
        cfg.build_url("   ", "chat/completions")


# test_empty_azure_key_boot_fail DELETED — v25 task-3 §6: GATEWAY_AZURE_API_KEY
# is removed from _UPSTREAM_KEY_ENV_VARS (and azure_api_key field removed from
# Settings). The removal is asserted by test_removed_azure_secret_vars_no_longer_guarded
# in tests/empty_key_boot_guard/.
