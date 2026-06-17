"""Shared test double for the BYOK per-tenant credential resolver.

credential-resolution-seam §3: with full-replace BYOK every upstream call resolves a
per-tenant credential via ``app.state.tenant_credential_resolver`` (a real
``CachedTenantCredentialResolver`` over ``DbTenantProviderKeyStore``, which needs a Fernet
encryption key + a seeded per-tenant key). The feature suites here (billing, teams, caching,
guardrails, PII, spans, …) exercise OTHER behavior through a FAKE upstream, so they should
not each seed a real encrypted key. ``install_stub_resolver(app)`` swaps in this stub — it
returns a fixed MASKED Bearer credential for any (tenant, provider), the same way the faked
upstream returns canned responses. It is consulted only for the converted Bearer providers
(the use-case gates on ``BYOK_BEARER_PROVIDERS``).

The REAL resolver path (DbTenantProviderKeyStore + Fernet + ProviderKeyMissing→402) is
covered by the ``credential_resolution_seam`` + ``provider_credential_store`` suites, which
use their own fixtures/fakes and do NOT call ``install_stub_resolver``.
"""

from __future__ import annotations

from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderCredential


class StubCredentialResolver:
    """Structural fake for TenantCredentialResolver — always resolves a masked Bearer cred."""

    async def resolve(self, tenant_id: object, provider: str) -> ProviderCredential:
        return BearerCredential(secret="test-stub-credential-not-a-real-key")  # noqa: S106


def install_stub_resolver(app: object) -> None:
    """Override ``app.state.tenant_credential_resolver`` with the stub.

    Call right after ``create_app(settings)`` in any test ``app`` fixture whose suite drives
    completions through a faked upstream but does not seed real per-tenant provider keys.
    """
    app.state.tenant_credential_resolver = StubCredentialResolver()  # type: ignore[attr-defined]
