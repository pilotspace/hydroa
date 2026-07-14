"""Domain errors for agent OAuth — each carries its §1 SPECIFY error code string."""

from __future__ import annotations

from typing import ClassVar, Literal

AgentOAuthErrorCode = Literal[
    "device_code_collision",
    "authorization_expired",
    "authorization_not_pending",
    "authorization_not_approved",
    "authorization_already_consumed",
    "agent_principal_not_found",
    "agent_token_not_found",
    "agent_token_already_attached",
    "agent_principal_name_conflict",
    "agent_principal_killed",
]


class AgentOAuthError(Exception):
    """Base for agent-OAuth domain errors; `.code` is the stable wire/error string."""

    code: ClassVar[AgentOAuthErrorCode]

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class DeviceCodeCollisionError(AgentOAuthError):
    code: ClassVar[AgentOAuthErrorCode] = "device_code_collision"


class AuthorizationExpiredError(AgentOAuthError):
    code: ClassVar[AgentOAuthErrorCode] = "authorization_expired"


class AuthorizationNotPendingError(AgentOAuthError):
    code: ClassVar[AgentOAuthErrorCode] = "authorization_not_pending"


class AuthorizationNotApprovedError(AgentOAuthError):
    code: ClassVar[AgentOAuthErrorCode] = "authorization_not_approved"


class AuthorizationAlreadyConsumedError(AgentOAuthError):
    code: ClassVar[AgentOAuthErrorCode] = "authorization_already_consumed"


# ---------------------------------------------------------------------------
# agent-identity-governance TASK.md §3 (FROZEN @ v1) — agent principal errors
# ---------------------------------------------------------------------------


class AgentPrincipalNotFoundError(AgentOAuthError):
    """Unknown principal id, or belongs to another tenant (byte-identical — no leak)."""

    code: ClassVar[AgentOAuthErrorCode] = "agent_principal_not_found"


class AgentTokenNotFoundError(AgentOAuthError):
    """Unknown agent_tokens id, or belongs to another tenant (byte-identical — no leak)."""

    code: ClassVar[AgentOAuthErrorCode] = "agent_token_not_found"


class AgentTokenAlreadyAttachedError(AgentOAuthError):
    """The target token is already attached to a (possibly different) principal."""

    code: ClassVar[AgentOAuthErrorCode] = "agent_token_already_attached"


class AgentPrincipalNameConflictError(AgentOAuthError):
    """Another principal in the same tenant already holds this name."""

    code: ClassVar[AgentOAuthErrorCode] = "agent_principal_name_conflict"


class AgentPrincipalKilledError(AgentOAuthError):
    """A fresh token cannot be attached to an already-killed principal."""

    code: ClassVar[AgentOAuthErrorCode] = "agent_principal_killed"
