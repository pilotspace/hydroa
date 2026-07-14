"""API router for the Claude gateway protocol's best-effort probe surface
(claude-gateway-protocol-compat TASK.md §3 M9): HEAD / and the /inference-profiles
probe 404.

GET /v1/models (§3 M1) does NOT live here — a genuine path collision was discovered
at build time: `catalog/api/router.py::catalog_router` already serves a DIFFERENT,
pre-existing, JWT-authed GET /v1/models (the dashboard's tenant-priced model list).
Since Claude Code hardcodes the /v1/models path (it cannot be moved), M1's discovery
shape was added as an ADDITIVE credential-type branch inside that EXISTING handler
(sk-/agent-token credential -> new Claude-discovery shape; Bearer JWT -> the ORIGINAL
byte-identical dashboard shape) rather than duplicated here — see
catalog/api/router.py::list_models for the merged implementation + the dispatch
rationale, and the build report's disclosed Ground-gap finding.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

discovery_router = APIRouter(tags=["proxy"])


@discovery_router.head("/")
async def root_probe() -> Response:
    """HEAD / — Claude Code's best-effort startup connectivity probe (§3 M9).

    A clean, fast 404 — never a 500, a hang, or a redirect — so an unmodified
    Claude Code client's connectivity probe never itself becomes a diagnostic
    red herring (external protocol anchor: "a gateway also sees... it can reject
    without breaking anything").
    """
    return Response(status_code=404)


@discovery_router.get("/inference-profiles")
async def inference_profiles_probe() -> Response:
    """GET /inference-profiles — a misconfigured Bedrock-format client's probe (§3 M9).

    Same clean-404 treatment as the root HEAD probe — never a 500, hang, or redirect.
    """
    return Response(status_code=404)
