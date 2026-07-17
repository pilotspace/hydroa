"""Domain layer for the email seam — entities + Protocol port + errors. Zero infra
imports (backend-architect convention): no smtplib, no FastAPI, no SQLAlchemy here.
"""

from __future__ import annotations
