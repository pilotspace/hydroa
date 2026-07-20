"""DomainVerifyNotifyScheduler (domain-verify-notify TASK.md §3 — FROZEN @ v1, SECURITY).

Mirrors CatalogRefreshScheduler's shape EXACTLY (fail-open refresh_once + run_forever) —
the repo's own dominant precedent for periodic in-process work; NO celery, pure asyncio
(mirrors the catalog-refresh change-request: celery/kombu's redis transport caps redis<6.5,
and the repo runs redis 8.x).

The SECURITY spine (never re-implemented, never weakened — each is a binding §3 SAFETY RULE):
  1. PROOF REUSED VERBATIM — each candidate is re-verified via the SAME frozen
     VerifyDomainClaimUseCase.execute(claim_id, tenant_id) the human POST .../verify path
     runs (fail-closed DNS-TXT check; a DnsLookupFailedError/DomainVerificationFailedError/
     DomainClaimExpiredError leaves the claim completely untouched — status stays
     'pending', never a shortcut, self-heals next tick).
  2. RECIPIENT SERVER-DERIVED — the notify email's `to` is ALWAYS resolved from the
     verified claim's own created_by_user_id via UserRoleRepository, tenant-scoped; never
     any request-supplied value — this scheduler has no HTTP request at all to read one
     from.
  3. EXACTLY-ONCE EMAIL — repository.mark_notified() is an atomic conditional UPDATE
     (... WHERE notified_at IS NULL RETURNING id); only the caller that flips the row
     (returns True) proceeds to dispatch. mark_notified runs BEFORE send_email
     (under-send on an SMTP failure is safer than over-send/spam) — safe under
     overlapping ticks/replicas regardless of replica count (§1 assumption, freeze note).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.domain_capture.application.verify_claim_use_case import VerifyDomainClaimUseCase
from gateway.domain_capture.domain.errors import (
    DnsLookupFailedError,
    DomainAlreadyVerifiedError,
    DomainClaimExpiredError,
    DomainClaimNotFoundError,
    DomainVerificationFailedError,
)
from gateway.domain_capture.domain.ports import DnsTxtResolver
from gateway.domain_capture.infrastructure.repository import SqlAlchemyDomainClaimRepository
from gateway.email.application.domain_verified_email_template import render_domain_verified_email
from gateway.email.application.email_dispatch import send_email
from gateway.email.domain.ports import EmailSender
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

_log = logging.getLogger(__name__)


def should_start_domain_verify_notify(interval_seconds: int) -> bool:
    """The interval>0 start gate (mirrors should_start_catalog_refresh).

    A zero (or negative) interval disables the scheduler entirely (opt-OUT, no task
    started) — an opted-in claim simply stays pending until an owner re-verifies by hand.
    """
    return interval_seconds > 0


class DomainVerifyNotifyScheduler:
    """Periodically re-checks opted-in pending claims and auto-verifies + notifies on
    the first live DNS-TXT match. Reuses VerifyDomainClaimUseCase verbatim (the SAME
    class the manual POST .../verify route runs) — no verification logic is duplicated
    here; this scheduler only owns the loop, the opt-in candidate scan, and the
    exactly-once email dispatch.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        dns_resolver: DnsTxtResolver,
        email_sender: EmailSender,
        dns_timeout_seconds: float,
        origin: str,
    ) -> None:
        self._session_factory = session_factory
        self._dns_resolver = dns_resolver
        self._email_sender = email_sender
        self._dns_timeout_seconds = dns_timeout_seconds
        self._origin = origin

    async def refresh_once(self) -> int:
        """One tick: re-verify + notify every opted-in candidate.

        Returns the count of emails actually dispatched. NEVER raises — any top-level
        failure (DB outage, etc.) is logged and swallowed, returning 0 so the background
        loop keeps ticking (fail-open, self-heals next tick).
        """
        try:
            return await self._refresh_once_unsafe()
        except Exception as exc:
            _log.warning(
                "domain_verify_notify: refresh_once failed (swallowed): %s", exc, exc_info=exc
            )
            return 0

    async def _refresh_once_unsafe(self) -> int:
        notified = 0
        async with self._session_factory() as session:
            repository = SqlAlchemyDomainClaimRepository(session)
            verify_use_case = VerifyDomainClaimUseCase(
                repository, self._dns_resolver, dns_timeout_seconds=self._dns_timeout_seconds
            )
            user_repository = UserRoleRepository(session)

            candidates = await repository.list_notify_candidates(datetime.now(UTC))
            for claim in candidates:
                try:
                    notified += await self._process_one(
                        claim_id=claim.id,
                        tenant_id=claim.tenant_id,
                        repository=repository,
                        verify_use_case=verify_use_case,
                        user_repository=user_repository,
                    )
                except Exception as exc:
                    # Defence in depth — one bad candidate must never abort the rest of
                    # this tick's batch (R-sec-4, bounded + resilient).
                    _log.warning(
                        "domain_verify_notify: candidate %s failed (swallowed): %s",
                        claim.id,
                        exc,
                        exc_info=exc,
                    )
        return notified

    async def _process_one(
        self,
        *,
        claim_id: uuid.UUID,
        tenant_id: uuid.UUID,
        repository: SqlAlchemyDomainClaimRepository,
        verify_use_case: VerifyDomainClaimUseCase,
        user_repository: UserRoleRepository,
    ) -> int:
        # (1) PROOF REUSED VERBATIM — fail-closed; a miss/timeout/expiry/race leaves the
        # claim completely untouched (self-heals next tick, M7).
        try:
            verified_claim = await verify_use_case.execute(claim_id=claim_id, tenant_id=tenant_id)
        except (
            DnsLookupFailedError,
            DomainVerificationFailedError,
            DomainClaimExpiredError,
            DomainClaimNotFoundError,
            DomainAlreadyVerifiedError,
        ):
            return 0

        # (3) EXACTLY-ONCE EMAIL — atomic conditional claim BEFORE dispatch.
        won = await repository.mark_notified(claim_id=verified_claim.id)
        if not won:
            return 0

        # (2) RECIPIENT SERVER-DERIVED — resolved from created_by_user_id, tenant-scoped;
        # never any caller/request-supplied value (this scheduler has no HTTP request).
        owner = await user_repository.get_by_id_and_tenant(
            user_id=verified_claim.created_by_user_id, tenant_id=verified_claim.tenant_id
        )
        if owner is None:
            # Defensive only — created_by_user_id is FK-constrained to users.id, so this
            # should be unreachable; never crash the loop over it.
            _log.warning(
                "domain_verify_notify: claim %s owner user_id %s not found (FK should"
                " forbid this) — email not sent",
                verified_claim.id,
                verified_claim.created_by_user_id,
            )
            return 0

        message = render_domain_verified_email(
            to=owner.email, domain=verified_claim.domain, origin=self._origin
        )
        await send_email(self._email_sender, message)
        return 1

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: refresh_once() every interval_seconds.

        Swallows all non-CancelledError exceptions; propagates CancelledError for clean
        task cancellation from the lifespan shutdown. _sleep is injectable for tests.
        Mirrors CatalogRefreshScheduler.run_forever (work-then-sleep).
        """
        while True:
            try:
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "domain_verify_notify: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await _sleep(interval_seconds)
