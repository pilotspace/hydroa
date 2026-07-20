"""render_domain_verified_email — the "it's live" auto-verify notification email
(domain-verify-notify TASK.md §3 CONTRACT — FROZEN @ v1). Pure function, no IO.

Sent EXACTLY ONCE per claim by DomainVerifyNotifyScheduler after the background DNS-TXT
re-check (reusing VerifyDomainClaimUseCase verbatim, fail-closed) confirms the domain is
verified — mirrors render_invite_email's pure-function shape and relative/absolute-link
convention exactly (link is absolute only when `origin` is set).
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_domain_verified_email(*, to: str, domain: str, origin: str) -> EmailMessage:
    """Build the domain-verified EmailMessage.

    link = f"{origin}/admin/domain-claims" if origin else "/admin/domain-claims" — matches
    render_invite_email's own origin-optional link construction exactly.
    """
    link = f"{origin}/admin/domain-claims" if origin else "/admin/domain-claims"
    subject = f"{domain} is verified"
    text_body = f"Good news — {domain} is now verified and live.\n\n{link}\n"
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
