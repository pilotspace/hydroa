"""FinetuneProviderPort + FinetuneCompletionListener protocols, provider derivation,
and the OpenAI 6-state job-status vocabulary (finetune-broker PLAN.md §3, FROZEN @ v1).

SECURITY: the provider for a create is ALWAYS derived server-side from ``model`` via
``FINETUNE_CAPABLE_PROVIDERS`` — never read from a client-supplied field (T1 threat
model: a client-named provider would let caller A pick which of ITS OWN credentials
signs the call, but never lets it name ANOTHER tenant's credential — the resolver is
still keyed by (authz.tenant_id, provider) only. Deriving server-side additionally
closes the door on a client asking for a provider that has nothing to do with the
requested base model).
"""

from __future__ import annotations

import re
from typing import Any, Literal, Protocol, runtime_checkable

#: v1 scope (Tin-frozen, PLAN.md §3 flag): fine-tuning is brokered to OpenAI only.
#: azure/vertex BYOK tenants get ERR_FINETUNE_MODEL_UNSUPPORTED until an additive
#: provider adapter lands — this frozenset + the port are the additive escape hatch,
#: no re-freeze of this contract required to extend it.
FINETUNE_CAPABLE_PROVIDERS: frozenset[str] = frozenset({"openai"})

#: Base-model name prefixes OpenAI supports for fine-tuning (v1: the gpt-4o family +
#: the classic completions models) — used ONLY to derive the provider server-side,
#: never to validate the model string beyond "does this look fine-tunable at all".
_OPENAI_FINETUNE_MODEL_PREFIXES: tuple[str, ...] = (
    "gpt-",
    "o1-",
    "o3-",
    "o4-",
    "davinci-",
    "babbage-",
    "ft:",
)

#: OpenAI's exact 6-state fine_tuning.job status vocabulary (PLAN.md §3 Schema).
FinetuneJobStatus = Literal[
    "validating_files", "queued", "running", "succeeded", "failed", "cancelled"
]

#: Terminal statuses — a job here never transitions again (M4 cancel-conflict guard,
#: M7 CAS "never re-polled or re-fired" guard).
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

_PROVIDER_JOB_ID_RE = re.compile(r"^[A-Za-z0-9:_.-]+$")


def resolve_finetune_provider(model: str) -> str | None:
    """Derive the finetune-capable provider for ``model``, or None if unsupported.

    Server-side ONLY (never a client-supplied field — T1). v1: any model prefix
    OpenAI fine-tunes maps to "openai"; everything else (e.g. an Anthropic/Google
    model id) is unsupported -> the caller raises ERR_FINETUNE_MODEL_UNSUPPORTED.
    """
    if any(model.startswith(prefix) for prefix in _OPENAI_FINETUNE_MODEL_PREFIXES):
        return "openai"
    return None


def fold_provider_status(raw_status: str) -> FinetuneJobStatus:
    """Fold a provider-reported status string onto the closed 6-state vocabulary.

    Unknown/unexpected provider strings fold to "running" (T5: a hostile or novel
    provider status can never crash the broker nor smuggle an out-of-vocabulary
    value onto the wire) — never raises.
    """
    if raw_status in TERMINAL_STATUSES or raw_status in ("validating_files", "queued", "running"):
        return raw_status  # type: ignore[return-value]
    return "running"


def unwrap_credential_secret(credential: Any) -> Any:
    """Extract the plain secret value from a resolved ``ProviderCredential`` before it
    crosses the ``FinetuneProviderPort`` boundary.

    ``CachedTenantCredentialResolver.resolve`` returns a domain credential (e.g.
    ``BearerCredential``) whose ``.secret`` is a ``pydantic.SecretStr`` — masked by
    ``str()`` (never logged/persisted, T4). The port itself only needs the raw
    secret value to build its own Authorization header; unwrapping it HERE (once,
    at the one seam every call crosses) means neither the fake test double nor the
    real ``OpenAIFinetuneClient`` needs to know about ``SecretStr`` at all. Anything
    without a ``.secret.get_secret_value()`` shape (e.g. an already-plain string, or
    a test double's own credential stand-in) passes through unchanged.
    """
    secret_field = getattr(credential, "secret", None)
    get_secret_value = getattr(secret_field, "get_secret_value", None)
    if callable(get_secret_value):
        return get_secret_value()
    return credential


def is_valid_provider_job_id(provider_job_id: str) -> bool:
    """Shape-validate a provider-returned job id BEFORE it may ever be interpolated
    into a subsequent outbound poll/cancel URL path (T5: a hostile provider response
    cannot redirect a later call by returning a path-traversal/injection payload as
    the job id)."""
    return bool(provider_job_id) and bool(_PROVIDER_JOB_ID_RE.match(provider_job_id))


@runtime_checkable
class FinetuneProviderPort(Protocol):
    """Port the broker submits/polls/cancels a fine-tune job through.

    Real impl: ``OpenAIFinetuneClient`` (infrastructure/openai_client.py). Test double:
    ``FakeFinetuneProvider`` (tests/finetune_broker/test_finetune_broker.py) — the
    signature below is the one the fake structurally satisfies.

    HEAL (D1, mirrors the moderations CR-1 per-tenant breaker defect): every method
    takes ``tenant_id`` FIRST — the authenticated caller's tenant, threaded through by
    ``FinetuneBrokerService`` at all 3 call sites — so the implementation can key its
    outbound-IO circuit breaker PER TENANT. Before this, no method carried a tenant
    key at all, so a real implementation had no choice but a single process-wide
    breaker: one tenant's 5 consecutive provider failures would trip EVERY other
    tenant's fine-tuning calls closed too (the exact R4 moderations CR-1 defect).
    """

    async def submit(self, tenant_id: Any, credential: Any, request: dict[str, Any]) -> str:
        """Submit a new job; return the provider's own job id (unvalidated shape —
        the caller shape-validates via ``is_valid_provider_job_id`` before storing)."""
        ...

    async def poll(self, tenant_id: Any, credential: Any, provider_job_id: str) -> dict[str, Any]:
        """Return ``{"status": <provider-status-string>, "fine_tuned_model": str|None}``."""
        ...

    async def cancel(self, tenant_id: Any, credential: Any, provider_job_id: str) -> None:
        """Cancel a job at the provider. Raises on failure (caller maps to 502)."""
        ...


@runtime_checkable
class FinetuneCompletionListener(Protocol):
    """The finetune-model-registry extension point (PLAN.md §3) — consulted at
    ``app.state.finetune_completion_listener`` (default None -> byte-identical).
    Invoked EXACTLY ONCE, only on the winning CAS transition to "succeeded"."""

    async def on_succeeded(self, job: Any) -> None: ...
