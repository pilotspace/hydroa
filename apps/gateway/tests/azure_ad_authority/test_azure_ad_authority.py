"""Red suite for azure-ad-authority-config (v22 task 2).

Makes GATEWAY_AZURE_AD_AUTHORITY env-configurable so sovereign/government clouds can
drive AAD token acquisition. The v21 carried follow-up: AzureADConfig.authority and
AzureADTokenProvider._token_url() already CONSUME the authority.

CONTRACT (FROZEN @ v1): azure-ad-authority-config TASK.md §3.
  - Settings.azure_ad_authority: str = "" (env GATEWAY_AZURE_AD_AUTHORITY).
  - AzureADConfig.authority respected by AzureADTokenProvider._token_url().

v25 task-3 amendment: resolve_azure_ad_config is DELETED (env-secret removal §6).
test_resolve_carries_authority_from_settings, test_resolve_defaults_authority_when_unset,
and test_authority_alone_does_not_enable_aad are REMOVED (they tested the deleted function).
test_token_url_uses_configured_authority and test_settings_binds_env_var are KEPT.

asyncio_mode=auto; pure sync tests here (no I/O).
"""

from __future__ import annotations

from gateway.proxy.infrastructure.azure_ad import (
    DEFAULT_AUTHORITY,
    AzureADConfig,
    AzureADTokenProvider,
)

_US_AUTHORITY = "https://login.microsoftonline.us"


# resolve_azure_ad_config tests REMOVED — v25 task-3 §6: function is DELETED.
# test_resolve_carries_authority_from_settings, test_resolve_defaults_authority_when_unset,
# and test_authority_alone_does_not_enable_aad are retired per deliverable C.


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


def test_settings_binds_env_var(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Settings.azure_ad_authority field must exist and bind GATEWAY_AZURE_AD_AUTHORITY.
    from gateway.core.config import Settings

    monkeypatch.setenv("GATEWAY_AZURE_AD_AUTHORITY", _US_AUTHORITY)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.azure_ad_authority == _US_AUTHORITY
