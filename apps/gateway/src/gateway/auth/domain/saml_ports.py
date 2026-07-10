"""SAML domain ports (protocols).

Mirrors auth/domain/ports.py's OIDC port shapes — a distinct file per the §1
Framings decision (new files only, zero edits to OIDC files).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from gateway.auth.domain.saml_entities import PendingSamlRequest, SamlProviderConfig


class SamlConfigResolver(Protocol):
    """Port: resolve per-tenant SAML IdP configuration.

    Tests inject a fake via app.state.saml_config_resolver.
    """

    async def resolve(self, domain: str | None) -> SamlProviderConfig | None:
        """Return per-tenant config for the given email domain, or None if no
        enabled row matches (→ ERR_SAML_NOT_CONFIGURED, M1)."""
        ...

    async def resolve_by_tenant_id(self, tenant_id: str) -> SamlProviderConfig | None:
        """Return per-tenant config by tenant_id, or None if no enabled row
        matches. Used at /acs after the pending-request lookup resolves the
        pinned tenant (M3)."""
        ...


class SamlRequestStore(Protocol):
    """Port: server-side pending-request store — the tenant-binding mechanism
    that supersedes the OIDC cookie pattern (Ground R1, M3).

    Tests inject a fake via app.state.saml_request_store.
    """

    async def put(self, request_id: str, record: PendingSamlRequest, *, ttl_seconds: int) -> None:
        """Write saml:pending:{request_id} with the given TTL (M1)."""
        ...

    async def get_and_delete(self, request_id: str) -> PendingSamlRequest | None:
        """Atomically GET + DELETE the pending record for request_id (M3).

        Returns None when request_id was never written (includes genuinely
        IdP-initiated/unsolicited responses — M6; caller maps to
        SamlRequestNotFoundError).

        Raises SamlRequestAlreadyUsedError when request_id WAS previously
        consumed by an earlier call (M3 single-use / concurrent-double-submit
        safety — GETDEL alone cannot distinguish "never existed" from
        "already deleted", so the adapter tracks a short-lived tombstone to
        tell the two apart for the caller's error-code selection; the
        SECURITY property — at most one caller ever receives the record —
        holds regardless of the tombstone).

        Raises SamlStoreUnavailableError if Redis is unreachable (M12,
        fail-CLOSED — never silently treated as "not replayed").
        """
        ...


class SamlReplayCache(Protocol):
    """Port: independent second replay-defense layer, keyed by assertion @ID
    (M5.6) — distinct from SamlRequestStore's per-request-id consumption.

    Tests inject a fake via app.state.saml_replay_cache.
    """

    async def mark_consumed_if_new(self, assertion_id: str, *, ttl_seconds: int) -> bool:
        """Atomically SETNX saml:consumed:{assertion_id}; TTL bounded per M5.6.

        Returns True iff this is the FIRST time assertion_id has been seen
        (the key did not already exist) — False signals a replay.

        Raises SamlStoreUnavailableError if Redis is unreachable (M12).
        """
        ...
