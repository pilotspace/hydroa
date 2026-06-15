"""Red suite for azure-auth-routing (v21 task 1): Azure config + deployment URL routing.

A pure, IO-free config/routing seam consumed by the later Azure adapters. All
azure_config imports are RED until the module is implemented (ModuleNotFoundError).

CONTRACT (FROZEN @ v1): azure-auth-routing TASK.md §3
  - AzureConfig (frozen dataclass): api_key (repr-hidden SECRET), endpoint,
    api_version, deployment_map; methods resolve_deployment(model), build_url(deployment, op).
  - resolve_azure_config(settings) -> AzureConfig | None  (None unless api_key AND endpoint).
  - GATEWAY_AZURE_API_KEY added to the boot-guard tuple (validate_upstream_keys).

Each test maps 1:1 to a §2 scenario; behavior is asserted, not internals.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# RED: gateway.proxy.infrastructure.azure_config does not exist yet → ModuleNotFoundError.
from gateway.proxy.infrastructure.azure_config import (
    AzureConfig,
    resolve_azure_config,
)
from gateway.core.config import EmptyUpstreamKeyError, validate_upstream_keys


def _settings(**over: object) -> SimpleNamespace:
    """A minimal settings stub mirroring the Settings attrs resolve_azure_config reads."""
    base: dict[str, object] = {
        "azure_api_key": "",
        "azure_endpoint": "",
        "azure_api_version": "2024-10-21",
        "azure_deployment_map": {},
    }
    base.update(over)
    return SimpleNamespace(**base)


# ── resolve_azure_config ───────────────────────────────────────────────────


def test_resolve_config_present() -> None:
    cfg = resolve_azure_config(
        _settings(azure_api_key="sk-az", azure_endpoint="https://r.openai.azure.com")
    )
    assert cfg is not None
    assert cfg.endpoint == "https://r.openai.azure.com"
    assert cfg.api_version == "2024-10-21"
    assert dict(cfg.deployment_map) == {}


def test_resolve_config_absent_returns_none() -> None:
    assert resolve_azure_config(_settings(azure_api_key="", azure_endpoint="")) is None


def test_partial_config_returns_none() -> None:
    # api_key set but endpoint empty → silent disable (opt-in partial), no raise.
    assert resolve_azure_config(_settings(azure_api_key="sk-az", azure_endpoint="")) is None
    # and the inverse
    assert (
        resolve_azure_config(
            _settings(azure_api_key="", azure_endpoint="https://r.openai.azure.com")
        )
        is None
    )


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


# ── boot-guard ─────────────────────────────────────────────────────────────


def test_empty_azure_key_boot_fail() -> None:
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_AZURE_API_KEY": ""})
    msg = str(exc.value)
    assert "GATEWAY_AZURE_API_KEY" in msg
    # no key value echoed (there is none here, but assert the var-only invariant holds)
    assert "unset" in msg.lower()
