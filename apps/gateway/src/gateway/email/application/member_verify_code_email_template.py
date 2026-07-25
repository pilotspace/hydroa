"""render_member_verify_code_email — the 6-digit mailbox-confirmation code email
(member-verified-recognition TASK.md §3 CONTRACT — FROZEN @ v1, SECURITY). Pure function,
no IO.

Emailed to the business admin's OWN signup / account address (server-derived, never
request-supplied — R-sec-3). The text_body carries the 6-digit code verbatim; mirrors
render_domain_verified_email's pure-function shape + origin-optional link convention.
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_member_verify_code_email(
    *, to: str, domain: str, code: str, origin: str
) -> EmailMessage:
    """Build the member-verify code EmailMessage.

    link = f"{origin}/admin/domain-claims" if origin else "/admin/domain-claims" — matches
    render_domain_verified_email / render_invite_email's origin-optional construction.
    """
    link = f"{origin}/admin/domain-claims" if origin else "/admin/domain-claims"
    subject = f"Your {domain} verification code"
    text_body = (
        f"Your verification code for {domain} is {code}.\n\n"
        "Enter it to confirm you control this mailbox. "
        "It expires in about 15 minutes.\n\n"
        f"{link}\n"
    )
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
