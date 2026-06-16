"""Domain services for the keys sub-domain.

Re-exports JwtTokenService from the tenants infrastructure layer so that
callers can import it from the canonical domain path
  gateway.keys.domain.services.JwtTokenService
without breaking the existing tenants infrastructure location.

The authoritative implementation lives in
  gateway.tenants.infrastructure.jwt_service.JwtTokenService
"""

from __future__ import annotations

from gateway.tenants.infrastructure.jwt_service import JwtTokenService

__all__ = ["JwtTokenService"]
