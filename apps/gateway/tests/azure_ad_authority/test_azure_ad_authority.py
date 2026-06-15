"""Red suite for azure-ad-authority-config (v22 task 2).

Makes GATEWAY_AZURE_AD_AUTHORITY env-configurable so sovereign/government clouds can
drive AAD token acquisition. The v21 carried follow-up: AzureADConfig.authority and
AzureADTokenProvider._token_url() already CONSUME the authority, but resolve_azure_ad_config
never read it from settings — so it was hardcoded to DEFAULT_AUTHORITY. This wires the
Setting → resolve → config → token URL, defaulting to the public cloud when unset.

CONTRACT (FROZEN @ v1): azure-ad-authority-config TASK.md §3.
  - Settings.azure_ad_authority: str = "" (env GATEWAY_AZURE_AD_AUTHORITY).
  - resolve_azure_ad_config: authority = getattr(settings, "azure_ad_authority", "") or DEFAULT_AUTHORITY.
  - AAD opt-in gating (tenant+client+secret) UNCHANGED; default path byte-identical.

RED today: resolve omits authority (→ always DEFAULT) and Settings has no such field.
asyncio_mode=auto; pure sync tests here (no I/O).
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway.proxy.infrastructure.azure_ad import (
    DEFAULT_AUTHORITY,
    AzureADConfig,
    AzureADTokenProvider,
    resolve_azure_ad_config,
)

_US_AUTHORITY = "https://login.microsoftonline.us"


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "azure_tenant_id": "tenant-1",
        "azure_client_id": "client-1",
        "azure_client_secret": "secret-1",
        "azure_ad_scope": "",
        "azure_ad_authority": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_carries_authority_from_settings() -> None:
    # RED: resolve_azure_ad_config currently omits authority → falls to DEFAULT_AUTHORITY.
    cfg = resolve_azure_ad_config(_settings(azure_ad_authority=_US_AUTHORITY))
    assert cfg is not None
    assert cfg.authority == _US_AUTHORITY


def test_resolve_defaults_authority_when_unset() -> None:
    # Behavior-preserving: unset → public-cloud default.
    cfg = resolve_azure_ad_config(_settings(azure_ad_authority=""))
    assert cfg is not None
    assert cfg.authority == DEFAULT_AUTHORITY


def test_token_url_uses_configured_authority() -> None:
    # End-to-end: the carried authority reaches the minted-token URL.
    config = AzureADConfig(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="secret-1",
        authority=_US_AUTHORITY,
    )
    provider = AzureADTokenProvider(config=config)
    assert provider._token_url() == f"{_US_AUTHORITY}/tenant-1/oauth2/v2.0/token"


def test_authority_alone_does_not_enable_aad() -> None:
    # Gating unchanged: authority set but no client_secret → AAD disabled (None).
    cfg = resolve_azure_ad_config(
        _settings(azure_client_secret="", azure_ad_authority=_US_AUTHORITY)
    )
    assert cfg is None


def test_settings_binds_env_var(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # RED: Settings has no azure_ad_authority field yet (extra="ignore" → AttributeError).
    from gateway.core.config import Settings

    monkeypatch.setenv("GATEWAY_AZURE_AD_AUTHORITY", _US_AUTHORITY)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.azure_ad_authority == _US_AUTHORITY
