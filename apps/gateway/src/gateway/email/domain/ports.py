"""The EmailSender port — the seam any caller needing ancillary outbound email depends
on (transactional-email TASK.md §3 CONTRACT, FROZEN @ v1). Mirrors
gateway.objectstore.port.ObjectStore's own shape: a minimal, adapter-agnostic Protocol
with zero infra imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gateway.email.domain.entities import EmailMessage


@runtime_checkable
class EmailSender(Protocol):
    """Fire-and-forget outbound email. Exactly one method — zero invite-specific
    fields — reusable by a future alerts/invoices caller without a signature change.
    """

    async def send(self, message: EmailMessage) -> None:
        """Send `message`.

        Raises EmailSendError on failure — NEVER caught here; the caller
        (gateway.email.application.email_dispatch.send_email) is the fail-open boundary.
        """
        ...
