"""Infrastructure layer for the email seam: ConsoleEmailSender (default, logs the
rendered mail) and SmtpEmailSender (config-gated, real outbound IO).
"""

from __future__ import annotations
