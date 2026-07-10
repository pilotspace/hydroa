"""Request/response schemas for /admin/domain-claims (domain-capture TASK.md §3 — FROZEN @ v1).

Each endpoint gets its OWN response shape (the frozen contract is NOT one shared schema —
POST create returns the DNS record to publish, GET list omits dns_record_type, POST verify
returns only the minimal post-verify fields).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from gateway.domain_capture.domain.entities import DomainClaim


class DomainClaimCreateRequest(BaseModel):
    domain: str


class DomainClaimCreateResponse(BaseModel):
    claim_id: uuid.UUID
    domain: str
    status: str
    dns_record_type: str
    dns_record_name: str
    dns_record_value: str
    expires_at: datetime


class DomainClaimListItem(BaseModel):
    claim_id: uuid.UUID
    domain: str
    status: str
    dns_record_name: str
    dns_record_value: str
    expires_at: datetime
    verified_at: datetime | None


class DomainClaimListResponse(BaseModel):
    claims: list[DomainClaimListItem]


class DomainClaimVerifyResponse(BaseModel):
    claim_id: uuid.UUID
    domain: str
    status: str
    verified_at: datetime


def _dns_record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


def _dns_record_value(token: str) -> str:
    return f"ai-proxy-domain-verification={token}"


def to_create_response(claim: DomainClaim) -> DomainClaimCreateResponse:
    return DomainClaimCreateResponse(
        claim_id=claim.id,
        domain=claim.domain,
        status=claim.status.value,
        dns_record_type="TXT",
        dns_record_name=_dns_record_name(claim.domain),
        dns_record_value=_dns_record_value(claim.verification_token),
        expires_at=claim.expires_at,
    )


def to_list_item(claim: DomainClaim) -> DomainClaimListItem:
    return DomainClaimListItem(
        claim_id=claim.id,
        domain=claim.domain,
        status=claim.status.value,
        dns_record_name=_dns_record_name(claim.domain),
        dns_record_value=_dns_record_value(claim.verification_token),
        expires_at=claim.expires_at,
        verified_at=claim.verified_at,
    )


def to_verify_response(claim: DomainClaim) -> DomainClaimVerifyResponse:
    assert claim.verified_at is not None
    return DomainClaimVerifyResponse(
        claim_id=claim.id,
        domain=claim.domain,
        status=claim.status.value,
        verified_at=claim.verified_at,
    )
