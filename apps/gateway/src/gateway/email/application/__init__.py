"""Application layer for the email seam: send_email (fire-and-forget dispatch wrapper)
and render_invite_email (the invite-accept template). Pure/thin — no IO of their own.
"""

from __future__ import annotations
