"""EmailMessage — the value object every EmailSender adapter sends (transactional-email
TASK.md §3 CONTRACT, FROZEN @ v1). Deliberately zero invite-specific fields, so the same
message shape is reusable by a future alerts/invoices caller without a signature change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None  # optional; adapters may ignore
