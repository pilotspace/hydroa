"""render_invite_email — the invite-accept email template (transactional-email TASK.md
§3 CONTRACT, FROZEN @ v1). Pure function, no IO.

Body is inviter-neutral (role + link only, no tenant display name) — CreateInviteUseCase/
Invite carry no tenant name today (§0 GROUND Issue #3 / §1 assumption #1, ACCEPTED at
freeze); a follow-up may enrich this once a tenant-name lookup exists, additive only.
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_invite_email(*, to: str, role: str, token: str, origin: str) -> EmailMessage:
    """Build the invite-accept EmailMessage.

    link = f"{origin}/invite/{token}" if origin else f"/invite/{token}" — matches the
    dashboard's own client-side ${window.location.origin}/invite/${token} construction
    exactly when origin is the real dashboard_public_origin.
    """
    link = f"{origin}/invite/{token}" if origin else f"/invite/{token}"
    subject = "You've been invited to join Hydroa"
    text_body = f"You've been invited to join as {role}.\n\n{link}\n"
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
