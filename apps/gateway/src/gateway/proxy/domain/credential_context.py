"""Request-scoped contextvar for the resolved per-tenant provider credential.

§3 CONTRACT (credential-resolution-seam TASK.md) — FROZEN @ v1.

DESIGN RATIONALE:
- Domain module (not application or infrastructure) so BOTH layers can import
  without a layering cycle: the application use-case (sets it) and the
  infrastructure adapters (read it) share this single definition.
- The contextvar payload is the fully resolved ``ProviderCredential`` so adapters
  remain store-agnostic — they never call the resolver directly.
- ``reset_provider_credential(token)`` MUST be called in a ``finally`` block after
  every request so the credential does not leak across requests or async tasks.

SECURITY INVARIANTS:
- The contextvar holds a ``ProviderCredential`` whose secret fields are typed as
  ``pydantic.SecretStr`` — masked in repr/str/logs, exposed only via
  ``.get_secret_value()`` at the call site that builds the HTTP header.
- A missing token (contextvar not set, default=None) causes adapters to raise
  ``ProviderKeyMissing`` rather than emitting an unauthenticated request.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from gateway.proxy.domain.provider_credentials import ProviderCredential

# ---------------------------------------------------------------------------
# Request-scoped contextvar — default None (not set)
# ---------------------------------------------------------------------------

current_provider_credential: ContextVar[ProviderCredential | None] = ContextVar(
    "current_provider_credential",
    default=None,
)

# The Hydroa tenant that OWNS the currently-set provider credential. Companion to
# current_provider_credential, mirroring guardrail_tenant_context. Read by the BYOK
# token-provider caches (vertex_ad / azure_ad) to scope cached bearer tokens per
# (hydroa_tenant, identity) — WITHOUT it, a tenant reusing another tenant's provider
# identity (e.g. a GCP service-account email+project) would be served the victim's
# cached token, a cross-tenant confused-deputy (vertex-adapter M4 CR-2, SECURITY).
current_credential_tenant: ContextVar[object | None] = ContextVar(
    "current_credential_tenant",
    default=None,
)

# Whether the credential bound to THIS request was served by the platform-tenant
# fallback (platform-key-default milestone) rather than the requesting tenant's own
# BYOK key. Default False. Set True by ``mark_platform_fallback()`` inside the
# credential-resolution seam when (and only when) a platform-fallback credential is
# served; read by the usage recorder (``fallback-usage-marker`` sibling task) to stamp
# ``credential_source=platform``. Reset to False by ``reset_provider_credential()`` so
# it never leaks across requests/tasks. One credential is resolved per request, so a
# plain False-reset (rather than a saved Token) is correct for every real scope.
current_served_via_platform_fallback: ContextVar[bool] = ContextVar(
    "current_served_via_platform_fallback",
    default=False,
)


# ---------------------------------------------------------------------------
# Public helper API — thin wrappers around the contextvar
# ---------------------------------------------------------------------------


class _CredentialScope:
    """Paired reset handle for a credential + its owning-tenant contextvars.

    Returned by ``set_provider_credential`` when a ``tenant_id`` is supplied, so a
    single ``reset_provider_credential(scope)`` restores BOTH contextvars in one call
    (callers keep their existing single-token ``finally`` idiom unchanged).
    """

    __slots__ = ("cred_token", "tenant_token")

    def __init__(
        self,
        cred_token: Token[ProviderCredential | None],
        tenant_token: Token[object | None],
    ) -> None:
        self.cred_token = cred_token
        self.tenant_token = tenant_token


def set_provider_credential(
    cred: ProviderCredential, tenant_id: object | None = None
) -> Token[ProviderCredential | None] | _CredentialScope:
    """Set the resolved credential (and, when given, its owning Hydroa tenant) for the
    current request/task scope.

    Returns a reset handle the caller MUST pass to ``reset_provider_credential()`` in a
    ``finally`` block to restore the previous value(s) and prevent cross-request leaks.
    When ``tenant_id`` is supplied the handle is a ``_CredentialScope`` covering both
    contextvars; otherwise it is the bare credential ``Token`` (backward-compatible for
    call sites that re-set only the credential, e.g. guardrail/cost-recovery flows).

    The tenant is what scopes the BYOK token caches per (hydroa_tenant, identity) —
    passing it here (at the single credential-resolution seam) is the SECURITY fix for
    the cross-tenant token-cache confused-deputy (vertex-adapter M4 CR-2).

    Usage::

        scope = set_provider_credential(resolved_cred, tenant_id)
        try:
            await upstream_call()
        finally:
            reset_provider_credential(scope)
    """
    cred_token = current_provider_credential.set(cred)
    if tenant_id is None:
        return cred_token
    tenant_token = current_credential_tenant.set(tenant_id)
    return _CredentialScope(cred_token, tenant_token)


def get_provider_credential() -> ProviderCredential | None:
    """Return the credential currently bound to this request scope, or ``None``.

    Adapters call this in ``_auth_headers()`` to obtain the resolved secret.
    A ``None`` return means the contextvar was never set for this request —
    callers MUST raise ``ProviderKeyMissing`` rather than emitting a request
    with no or an empty credential.
    """
    return current_provider_credential.get()


def get_credential_tenant() -> object | None:
    """Return the Hydroa tenant that owns the request-scoped credential, or ``None``.

    The BYOK token caches read this to key cached bearer tokens per (hydroa_tenant,
    identity). ``None`` (contextvar never set for this request) means the cache MUST
    NOT share an entry across an unknown owner — degrade to per-call construction.
    """
    return current_credential_tenant.get()


def reset_provider_credential(
    handle: Token[ProviderCredential | None] | _CredentialScope,
) -> None:
    """Reset the credential (and owning-tenant) contextvar(s) to their previous value(s).

    Accepts either the bare credential ``Token`` or the ``_CredentialScope`` returned by
    ``set_provider_credential()``. Call this in a ``finally`` block — never omit it, even
    on exception paths. Resetting to the previous value (rather than to None explicitly)
    correctly handles nested scopes.
    """
    if isinstance(handle, _CredentialScope):
        current_credential_tenant.reset(handle.tenant_token)
        current_provider_credential.reset(handle.cred_token)
    else:
        current_provider_credential.reset(handle)
    # Clear the platform-fallback signal for this scope (default False). Reset alongside
    # the credential contextvars so a served-fallback marker never leaks to the next request.
    current_served_via_platform_fallback.set(False)


def mark_platform_fallback() -> None:
    """Flag the current request as served by the platform-tenant fallback credential.

    Called by the credential-resolution seam ONLY when a platform-fallback credential is
    actually served (never on an own-key or no-fallback request). Read via
    ``served_via_platform_fallback()``; cleared by ``reset_provider_credential()``.
    """
    current_served_via_platform_fallback.set(True)


def served_via_platform_fallback() -> bool:
    """Return True iff this request was served by the platform-tenant fallback credential."""
    return current_served_via_platform_fallback.get()


__all__ = [
    "current_credential_tenant",
    "current_provider_credential",
    "current_served_via_platform_fallback",
    "get_credential_tenant",
    "get_provider_credential",
    "mark_platform_fallback",
    "reset_provider_credential",
    "served_via_platform_fallback",
    "set_provider_credential",
]
