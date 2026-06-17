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


# ---------------------------------------------------------------------------
# Public helper API — thin wrappers around the contextvar
# ---------------------------------------------------------------------------


def set_provider_credential(cred: ProviderCredential) -> Token[ProviderCredential | None]:
    """Set the resolved credential for the current request/task scope.

    Returns the ``Token`` produced by ``ContextVar.set()`` — the caller MUST
    pass this token to ``reset_provider_credential()`` in a ``finally`` block
    to restore the previous value and prevent cross-request credential leaks.

    Usage::

        token = set_provider_credential(resolved_cred)
        try:
            await upstream_call()
        finally:
            reset_provider_credential(token)
    """
    return current_provider_credential.set(cred)


def get_provider_credential() -> ProviderCredential | None:
    """Return the credential currently bound to this request scope, or ``None``.

    Adapters call this in ``_auth_headers()`` to obtain the resolved secret.
    A ``None`` return means the contextvar was never set for this request —
    callers MUST raise ``ProviderKeyMissing`` rather than emitting a request
    with no or an empty credential.
    """
    return current_provider_credential.get()


def reset_provider_credential(token: Token[ProviderCredential | None]) -> None:
    """Reset the contextvar to its previous value.

    Pass the token returned by ``set_provider_credential()``.
    Call this in a ``finally`` block — never omit it, even on exception paths.
    Resetting to the previous value (rather than to None explicitly) correctly
    handles nested scopes if they arise in future.
    """
    current_provider_credential.reset(token)


__all__ = [
    "current_provider_credential",
    "get_provider_credential",
    "reset_provider_credential",
    "set_provider_credential",
]
