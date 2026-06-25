"""Domain errors for agent OAuth — each carries its §1 SPECIFY error code string."""

from __future__ import annotations

from typing import ClassVar, Literal

AgentOAuthErrorCode = Literal[
    "device_code_collision",
    "authorization_expired",
    "authorization_not_pending",
    "authorization_not_approved",
    "authorization_already_consumed",
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
