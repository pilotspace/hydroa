"""Use-cases for member invite acceptance (member-invite-acceptance TASK.md §3).

Provides:
  - PreviewInviteUseCase: GET /invites/{token} — read-only preview of a pending invite.
  - AcceptInviteUseCase: POST /invites/{token}/accept — provisions exactly ONE new user
    and flips the invite to accepted. THIS is MILESTONE.md's own named "one clean
    provisioning choke point" call site — the SECOND, alongside
    tenants/infrastructure/repository.py::get_or_provision_oidc_user — for a future
    seat-cap hook.

Sibling to invite_use_cases.py — mirrors its CONCRETE-import style (imports
``InviteRepository`` directly rather than a ``typing.Protocol`` port). This is a
deliberate, reasoned departure from backend-architect.md's "Default Requirement" (every
new use-case capability as a Protocol port + adapter pair): the EXISTING, shipped
``invite_use_cases.py`` already imports the concrete ``InviteRepository`` class
(``application/invite_use_cases.py:34``), and ``domain/ports.py`` has ZERO ``Invite*``
port today. Two near-identical sibling use-case files over the SAME bounded context
should not diverge on dependency style — see TASK.md §5 Persona note.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH, InvitePreview
from gateway.tenants.domain.errors import WeakPasswordError
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.invite_repository import InviteRepository

# Same opaque-token-hashed-at-rest scheme member-invite-issuance already uses
# (invite_use_cases.py's own module-level ``_hasher``) — the plaintext token from the
# out-of-band link is hashed here, then matched via the DB's UNIQUE token_hash index.
_token_hasher = Sha256SecretHasher()

# Passwords (users.password_hash) REMAIN argon2 (GLOSSARY) — the SAME hasher SignupUseCase
# uses, so an accepted invite's user row is indistinguishable from a signed-up owner's.
_password_hasher = Argon2PasswordHasher()


class PreviewInviteUseCase:
    """Read-only preview of a pending invite — zero side effect (M1)."""

    def __init__(self, repo: InviteRepository) -> None:
        self._repo = repo

    async def execute(self, *, token: str, now: datetime) -> InvitePreview:
        token_hash = _token_hasher.hash(token)
        return await self._repo.get_preview_by_token_hash(token_hash=token_hash, now=now)


class AcceptInviteUseCase:
    """Provision exactly ONE new user bound to the invite's tenant_id + role, and flip
    the invite to accepted (M2/M3/M5/M6/M8) — server-side-only identity resolution: the
    caller's token is the ONLY input that resolves tenant_id/email/role; nothing here
    ever reads a client-supplied identity field (that guard lives at the router/schema
    boundary — see invite_accept_router.py's InviteAcceptRequest, extra="ignore").
    """

    def __init__(self, repo: InviteRepository) -> None:
        self._repo = repo

    async def execute(
        self, *, token: str, password: str, now: datetime
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """Returns (tenant_id, user_id, email) of the newly provisioned user.

        Raises:
            WeakPasswordError: len(password) < MIN_PASSWORD_LENGTH — checked BEFORE any
                hashing or repository call (cheap, no-DB-IO checks first, §3 Access pattern).
            InviteNotFoundError / InviteNotPendingError / InviteExpiredError:
                propagated from InviteRepository.accept (token resolution/validity).
            EmailAlreadyRegisteredError: propagated from InviteRepository.accept (M6).
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError

        token_hash = _token_hasher.hash(token)
        password_hash = _password_hasher.hash(password)
        return await self._repo.accept(token_hash=token_hash, password_hash=password_hash, now=now)
