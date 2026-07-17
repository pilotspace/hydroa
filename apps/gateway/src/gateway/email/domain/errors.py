"""Typed errors for the email seam.

EmailSendError — an EmailSender adapter's send() failed (transport, auth, circuit-open).
Raised by adapters, NEVER caught inside them — the caller (send_email, the fail-open
application-layer boundary) is the one place that catches it, mirroring
record_audit/AuditRepository's own division of responsibility.
"""

from __future__ import annotations


class EmailSendError(Exception):
    """An EmailSender adapter failed to send a message (transport/auth/circuit-open)."""
