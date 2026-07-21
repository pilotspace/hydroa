"""access_requests API schemas (signup-refusal-router TASK.md §3 — FROZEN @ v1)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class AccessRequestCreate(BaseModel):
    email: EmailStr


class AccessRequestCreateResponse(BaseModel):
    """§3, M5/R3 (verbatim): ALWAYS this exact shape for ANY syntactically-valid email —
    the response NEVER carries anything that could vary by what the server does or
    doesn't already know about the email or its domain."""

    ok: bool = True
