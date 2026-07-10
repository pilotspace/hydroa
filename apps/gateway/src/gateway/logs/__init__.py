"""logs — opt-in, PII-scrubbed request/response payload capture store.

Bounded context (payload-capture-store TASK.md §3, FROZEN @ v1). Clean-architecture
layers mirror usage/: domain/ (zero framework) <- application/ (use cases) <-
infrastructure/ (SQLAlchemy adapters) <- api/ (routers).
"""

from __future__ import annotations
