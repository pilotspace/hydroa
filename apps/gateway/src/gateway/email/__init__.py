"""The email bounded context: an ancillary, fire-and-forget outbound-email seam
(transactional-email TASK.md §3 — FROZEN @ v1).

Layout mirrors every other bounded context in this codebase (backend-architect
convention): domain/ (entities + Protocol port + errors, zero infra imports),
application/ (use-case-shaped orchestration: send_email + render_invite_email),
infrastructure/ (ConsoleEmailSender / SmtpEmailSender adapters).

Wired at the composition root via gateway.main.build_email_sender(settings) ->
app.state.email_sender. Consumed this milestone by exactly one caller
(POST /admin/invites's accept-link email); designed to be reused unchanged by a
future alerts/invoices caller (out of this milestone's scope).
"""

from __future__ import annotations
