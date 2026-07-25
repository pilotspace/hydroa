"""McpCallUseCase — the /v1/mcp/call orchestration (mcp-connector-passthrough TASK.md
§3/§5, FROZEN @ v2; §3 amended -> v3 by the mcp-budget-governance CR, 2026-07-14).

Safety rule (§5, verbatim): the allow-list membership check (M5) and the fresh
`EgressPolicy.check()` (M6) MUST both complete, in that order, strictly BEFORE any
`McpDialer.dial()` call is constructed — no code path may construct the outbound HTTP
request object before both gates pass. This use case enforces the FIRST half of that
ordering (M5 before `self._dialer.dial()` is ever called); `HttpxMcpDialer` itself
enforces the second half (`egress_policy.check()` strictly before the httpx request is
built — see infrastructure/httpx_dialer.py).

mcp-budget-governance CR (2026-07-14, Tin-approved): before this CR, /v1/mcp/call ran
NO budget governance at all — every MCP tool call silently bypassed the agent-principal/
per-key/tenant/team budget caps chat and non-chat calls already enforce. A
budget-governance gate (NonChatGovernance.check_budgets — the SAME per-key/team/agent-
principal Redis-counter checks + RedisBudgetGuard.check() every other pipeline uses) now
runs FIRST, strictly before the allow-list check, so a budget-refused call costs ZERO
work of any kind (no allow-list DB read, no egress check, no dial, no usage_records row,
no tool-call meter). RPM/TPM and tenant-credit holds are explicitly OUT of scope for this
CR (see NonChatGovernance.check_budgets docstring) — documented follow-ups, not silently
covered.

Order: budget-governance gate (fail-closed, zero dials) -> allow-list check (fail-closed,
zero dials) -> McpDialer.dial() (egress-checked) -> GuardrailEvaluator.evaluate_pre on the
tool-result content (M9) -> PayloadCapturePort.capture() fire-and-forget (M10) ->
ToolCallObserver.record() fire-and-forget, success only (M11) -> response.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.egress_policy import EgressDeniedError, EgressPolicy
from gateway.core.error_catalog import (
    MCP_EGRESS_DENIED,
    MCP_SERVER_NOT_ALLOWED,
    MCP_UPSTREAM_REDIRECT_REJECTED,
    MCP_UPSTREAM_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.mcp_connector.domain.errors import McpDialTimeoutError, McpRedirectRejectedError
from gateway.mcp_connector.domain.ports import McpDialer, ToolCallObserver
from gateway.mcp_connector.infrastructure.breaker_registry import McpCircuitBreakerRegistry
from gateway.mcp_connector.infrastructure.repository import McpServerPolicyRepository
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import GuardrailEvaluator, PayloadCapturePort

_TOOL_RESULT_BLOCKED_CODE = -32050


@dataclass(frozen=True, slots=True)
class McpCallOutcome:
    """What the router relays to the caller — always HTTP 200 (M9's in-band block is
    a JSON-RPC-level error object, never an HTTP-level 4xx)."""

    body: dict[str, Any]


def _extract_tool_name(message: dict[str, Any]) -> str:
    params = message.get("params")
    if isinstance(params, dict):
        name = params.get("name")
        if isinstance(name, str) and name:
            return name
    return "unknown"


def _extract_result_content_blocks(body: dict[str, Any]) -> list[Any] | None:
    result = body.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            return content
    return None


def _whole_body_scan_text(body: dict[str, Any]) -> str:
    """The ENTIRE relayed body serialized as one JSON string — for the round-3 post-mask
    verification scan. Unlike `_extract_result_text` this NEVER prefers per-block text:
    it always dumps the whole body so STRUCTURAL fields (a resource `uri`/`mimeType`,
    any field carried through byte-identical by block substitution) are included in the
    scan. That is exactly the surface the per-block mask scan is blind to."""
    try:
        return json.dumps(body)
    except Exception:
        return ""


def _extract_result_text(body: dict[str, Any]) -> str:
    """Best-effort text extraction for guardrail scanning — the recognized MCP
    `result.content[*].text` shape, else a whole-body scan (never misses a substring
    match just because the shape is unrecognized)."""
    blocks = _extract_result_content_blocks(body)
    if blocks:
        texts = [
            b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    try:
        return json.dumps(body)
    except Exception:
        return ""


def _get_block_text(b: Any) -> str | None:
    """The maskable text carried by a single content block, if any recognized shape.

    Recognized shapes (MCP content-block spec):
      - TextContent: top-level {"text": <str>, "type": "text", ...}
      - EmbeddedResource: {"type": "resource", "resource": {"text": <str>, "uri": ...,
        "mimeType": ..., ...}} — the text is NESTED under `resource.text`, never at
        top level. A resource block whose resource carries `blob` (binary) instead of
        `text` has no maskable text and is treated the same as an image block: passed
        through unchanged, never scanned/substituted.

    Round-2 fix (fix/mcp-pii-mask-resource-blocks): the round-1 fix only recognized
    top-level `text`, so an EmbeddedResource block's PII was invisible to
    `_text_block_indices` and relayed VERBATIM whenever it coexisted with any
    top-level text block (confirmed HARD-STOP leak, conf 0.85).
    """
    if not isinstance(b, dict):
        return None
    if isinstance(b.get("text"), str):
        return b["text"]
    resource = b.get("resource")
    if isinstance(resource, dict) and isinstance(resource.get("text"), str):
        return resource["text"]
    return None


def _text_block_indices(blocks: list[Any]) -> list[int]:
    """Indices of blocks carrying recognized maskable text (top-level `text` OR
    `resource.text`) within a content-block list, in order."""
    return [i for i, b in enumerate(blocks) if _get_block_text(b) is not None]


def _build_scan_messages(body: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    """Build the guardrail-scan messages for a tool-call result body.

    ONE scan message PER recognized text-bearing content block (top-level `text` OR
    EmbeddedResource `resource.text`) — scanned and, in mask mode, masked
    independently — never a single "\\n"-joined blob. Joining then splitting is the
    exact ambiguity that let a 2nd/3rd+ block's PII survive unmasked (the original
    defect): `_apply_masked_text` could only map a masked JOINED string back onto
    exactly one recognized block, so it silently gave up (returned the UNMASKED body)
    for any other block count. Per-block scan/mask has no join to unambiguously
    reverse.

    Returns (scan_messages, text_block_indices) where text_block_indices[i] is the index
    into `result.content` that scan_messages[i] came from. An EMPTY text_block_indices
    means the best-effort whole-body-JSON fallback scan was used (no recognized
    text-bearing content-block shape) — the caller MUST NOT silently relay the
    original (possibly PII-carrying) body in that case: if the fallback scan detects
    PII there is no block structure to substitute into, so the caller fails the relay
    CLOSED (round-2 fix; see execute()'s `mask_unresolved` handling) rather than
    leaking it verbatim, which is what the pre-fix code silently did.
    """
    blocks = _extract_result_content_blocks(body)
    if blocks:
        indices = _text_block_indices(blocks)
        if indices:
            return [
                {"role": "tool", "content": _get_block_text(blocks[i]) or ""} for i in indices
            ], indices
    return [{"role": "tool", "content": _extract_result_text(body)}], []


def _apply_masked_blocks(
    body: dict[str, Any],
    text_block_indices: list[int],
    masked_scan_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild `result.content`, substituting EVERY recognized text-bearing block's text
    with its independently-masked counterpart (positional 1:1 with `text_block_indices`,
    produced by `_build_scan_messages`). A top-level `text` block has its `text` field
    replaced; an EmbeddedResource block has ONLY its `resource.text` field replaced —
    every other field (`uri`, `mimeType`, and any other resource/block field) is carried
    through BYTE-IDENTICAL. Every other block — including non-text blocks (image) and any
    block that wasn't recognized — is carried through unchanged, in place, in order. A
    no-op (returns `body` unmodified) when there is no known block structure to
    substitute into."""
    blocks = _extract_result_content_blocks(body)
    if not blocks or not text_block_indices:
        return body
    new_blocks = list(blocks)
    for pos, idx in enumerate(text_block_indices):
        if pos >= len(masked_scan_messages):
            break
        masked_content = masked_scan_messages[pos].get("content")
        block = new_blocks[idx]
        if not isinstance(masked_content, str) or not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            new_blocks[idx] = {**block, "text": masked_content}
            continue
        resource = block.get("resource")
        if isinstance(resource, dict) and isinstance(resource.get("text"), str):
            new_blocks[idx] = {**block, "resource": {**resource, "text": masked_content}}
    new_result = {**body["result"], "content": new_blocks}
    return {**body, "result": new_result}


class McpCallUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        audit_session_factory: async_sessionmaker[AsyncSession],
        repo: McpServerPolicyRepository,
        dialer: McpDialer,
        egress_policy: EgressPolicy,
        guardrail_evaluator: GuardrailEvaluator,
        payload_capture: PayloadCapturePort | None,
        tool_call_observer: ToolCallObserver,
        breaker_registry: McpCircuitBreakerRegistry,
        governance: NonChatGovernance,
    ) -> None:
        self._session = session
        self._audit_session_factory = audit_session_factory
        self._repo = repo
        self._dialer = dialer
        self._egress_policy = egress_policy
        self._guardrail_evaluator = guardrail_evaluator
        self._payload_capture = payload_capture
        self._tool_call_observer = tool_call_observer
        self._breakers = breaker_registry
        # mcp-budget-governance CR (mcp-connector-passthrough TASK.md §3 -> v3):
        # reuses NonChatGovernance.check_budgets() — see execute() below.
        self._governance = governance

    async def _resolve_effective_servers(self, authz: AuthzResult) -> list[str]:
        """Key > tenant > default-deny (M4/M14). AuthzResult.mcp_allowed_servers is
        populated ONLY for the sk- key path (the existing LEFT JOIN, zero extra
        query — M4). None here means the caller's AuthzResult didn't carry it (the
        agent-token path — CompositeKeyAuthenticator stays UNCHANGED per M14) — fall
        back to a tenant-only lookup, fail-closed to [] on any error (M12)."""
        if authz.mcp_allowed_servers is not None:
            return authz.mcp_allowed_servers
        return await self._repo.get_tenant_allowed_urls(self._session, authz.tenant_id)

    def _audit(self, *, authz: AuthzResult, server_host: str, tool_name: str, result: str) -> None:
        """Fire-and-forget, fail-open audit emit. NEVER carries upstream_headers /
        the agent's own upstream credential (M13) — only server_host/tool_name/result."""
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                self._audit_session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=authz.tenant_id,
                    actor_user_id=None,
                    actor_email=None,
                    actor_key_id=authz.key_id,
                    action="mcp_call",
                    target_type="mcp_server",
                    target_id=server_host,
                    result=result,
                    metadata={"server_host": server_host, "tool_name": tool_name, "result": result},
                ),
            )
        )

    def _capture(
        self,
        *,
        authz: AuthzResult,
        server_host: str,
        tool_name: str,
        message: dict[str, Any],
        response_body: dict[str, Any] | None,
        status: int,
        latency_ms: int,
    ) -> None:
        """Fire-and-forget trace write via the EXISTING SqlAlchemyPayloadCapture seam
        (M10) — never carries upstream_headers (M13); request/response are the
        JSON-RPC message envelopes only."""
        if self._payload_capture is None:
            return
        asyncio.ensure_future(  # noqa: RUF006
            self._payload_capture.capture(
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=f"mcp::{server_host}::{tool_name}",
                request_body={"messages": [{"role": "user", "content": json.dumps(message)}]},
                response_body=(
                    {"messages": [{"role": "assistant", "content": json.dumps(response_body)}]}
                    if response_body is not None
                    else None
                ),
                status=status,
                stream=False,
                cached=False,
                guardrail_configs=authz.guardrail_configs,
                latency_ms=latency_ms,
            )
        )

    async def execute(
        self,
        *,
        authz: AuthzResult,
        server_url: str,
        message: dict[str, Any],
        upstream_headers: dict[str, str],
    ) -> McpCallOutcome:
        started = time.monotonic()
        tool_name = _extract_tool_name(message)
        server_host = urlsplit(server_url).hostname or "unknown"

        # --- mcp-budget-governance CR: budget-governance gate — FIRST, strictly
        # before the allow-list check / egress check / dial, so a refused call
        # costs ZERO work of any kind (mirrors M5's "allow-list check FIRST"
        # discipline, extended one gate earlier). Reuses NonChatGovernance.
        # check_budgets() — the SAME per-key/team/agent-principal/tenant budget
        # ladder chat/non-chat calls already enforce. A breach raises
        # ProblemError(BUDGET_EXCEEDED) — the SAME 402 ErrorSpec every other
        # pipeline raises, caught by the app-wide exception handler. ---
        try:
            await self._governance.check_budgets(authz)
        except ProblemError as exc:
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="refused")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=exc.status,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise

        effective_servers = await self._resolve_effective_servers(authz)

        # --- M5: allow-list membership check — FIRST, strictly before any dial. ---
        if server_url not in effective_servers:
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="refused")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=403,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise MCP_SERVER_NOT_ALLOWED.exc()

        # CR-1 (contract v2): call_id generated EXACTLY ONCE per tool invocation at
        # admission (the call has passed the allow-list gate) — strictly BEFORE the
        # dial. This task never retries a dial (M8), so "exactly once per invocation"
        # falls out of generating it here, at this single call site, unconditionally.
        call_id = uuid.uuid4()

        breaker = self._breakers.get(authz.tenant_id, server_host)
        if not breaker.call_allowed():
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="error")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=503,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise MCP_UPSTREAM_UNAVAILABLE.exc()

        try:
            dial_result = await self._dialer.dial(
                server_url=server_url,
                message=message,
                upstream_headers=upstream_headers,
                egress_policy=self._egress_policy,
            )
        except EgressDeniedError:
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="refused")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=403,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise MCP_EGRESS_DENIED.exc() from None
        except McpRedirectRejectedError:
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="error")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=502,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise MCP_UPSTREAM_REDIRECT_REJECTED.exc() from None
        except McpDialTimeoutError:
            breaker.on_upstream_error()
            self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result="error")
            self._capture(
                authz=authz,
                server_host=server_host,
                tool_name=tool_name,
                message=message,
                response_body=None,
                status=503,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise MCP_UPSTREAM_UNAVAILABLE.exc() from None

        breaker.record_success()

        # --- M9: scan the tool-call RESULT via the existing prompt_injection/pii_mask
        # guardrails — one scan message PER recognized text content block (see
        # _build_scan_messages) so a mask-mode match masks ALL blocks that carry PII, not
        # just a single recognized block. ---
        scan_messages, text_block_indices = _build_scan_messages(dial_result.body)
        guardrail_result = await self._guardrail_evaluator.evaluate_pre(
            scan_messages, authz.guardrail_configs
        )

        # Round-2 fix (fix/mcp-pii-mask-resource-blocks): mode=mask detected PII (
        # guardrail_result.masked_messages is not None) but `_build_scan_messages`
        # found NO block structure to substitute into (text_block_indices == []) —
        # the whole-body-JSON fallback path was scanned/masked, but `_apply_masked_blocks`
        # is a no-op for an empty `text_block_indices` and would silently relay the
        # ORIGINAL, UNMASKED body. There is no structurally-safe way to substitute a
        # masked whole-body-JSON string back into an unrecognized/arbitrary result
        # shape without risking corrupting non-text structural fields, so this fails
        # the relay CLOSED — mirroring M9's in-band JSON-RPC block idiom — rather than
        # ever leaking the detected PII verbatim (confirmed HARD-STOP leak, conf 0.8).
        mask_unresolved = guardrail_result.masked_messages is not None and not text_block_indices

        latency_ms = int((time.monotonic() - started) * 1000)
        relayed_body: dict[str, Any]
        trace_status: int
        audit_result: Literal["success", "blocked"]
        # Bound ONLY on the allowed path below. `None` therefore means "no body was
        # ever built for relay", which is not a relayable state — the guard at the
        # relay decision treats it as blocked. `McpDialResult.body` is a non-optional
        # dict (default {}), so None can never be a legitimate upstream body and this
        # sentinel cannot collide with one.
        candidate_body: dict[str, Any] | None = None
        block_relay = guardrail_result.blocked or mask_unresolved
        if not block_relay:
            candidate_body = dial_result.body
            if guardrail_result.masked_messages:
                candidate_body = _apply_masked_blocks(
                    dial_result.body, text_block_indices, guardrail_result.masked_messages
                )
            # Round-3 fix (post-mask verification scan): the per-block scan above only
            # sees RECOGNIZED text (top-level `text` / `resource.text`). PII embedded in
            # a STRUCTURAL field — a resource `uri`/`mimeType`, or any field the block
            # substitution carries through byte-identical — is invisible to it, so when
            # such a field co-exists with a recognized text block (text_block_indices
            # non-empty ⇒ mask_unresolved False) the structural PII would relay VERBATIM.
            # The same PII is caught fail-closed when it is ALONE (whole-body fallback) —
            # an asymmetric leak (confirmed HARD-STOP, security). Re-scan the FINAL body
            # we are about to relay as ONE whole-body-JSON message (NOT per-block — that
            # would re-introduce the same blind spot); if the guardrail would still block
            # OR mask anything, residual PII remains somewhere structural, so fail the
            # relay CLOSED. Already-masked prose carries redaction placeholders that no
            # longer match, so a clean structural field never false-trips. Runs only on
            # the success path; audit-mode guardrails never block/mask, preserving
            # pass-through. See test_pii_mask_pii_in_resource_uri_alongside_text_block_*.
            verify_result = await self._guardrail_evaluator.evaluate_pre(
                [{"role": "tool", "content": _whole_body_scan_text(candidate_body)}],
                authz.guardrail_configs,
            )
            if verify_result.blocked or verify_result.masked_messages is not None:
                block_relay = True
        # FAIL CLOSED BY CONSTRUCTION: relaying requires a body that was actually
        # built on the allowed path. `candidate_body is None` is unreachable today
        # (it is bound iff block_relay is False), but stating it here means any future
        # edit that breaks that coupling BLOCKS rather than relays an unvetted body —
        # the safe direction for a path whose comments record a confirmed HARD-STOP.
        # Pinned by tests/mcp_connector/test_relay_failclosed_characterization.py.
        if block_relay or candidate_body is None:
            relayed_body = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": _TOOL_RESULT_BLOCKED_CODE,
                    "message": "ERR_MCP_TOOL_RESULT_BLOCKED",
                },
            }
            trace_status = 200
            audit_result = "blocked"
        else:
            relayed_body = candidate_body
            trace_status = dial_result.status
            audit_result = "success"

        self._capture(
            authz=authz,
            server_host=server_host,
            tool_name=tool_name,
            message=message,
            response_body=relayed_body,
            status=trace_status,
            latency_ms=latency_ms,
        )
        self._audit(authz=authz, server_host=server_host, tool_name=tool_name, result=audit_result)

        if audit_result == "success":
            asyncio.ensure_future(  # noqa: RUF006
                self._tool_call_observer.record(
                    call_id=call_id,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    server_host=server_host,
                    tool_name=tool_name,
                    status="success",
                    latency_ms=latency_ms,
                    # audit-remediation (HIGH): agent-principal MCP metering was
                    # unfunded — authz.agent_principal_id was never forwarded, so an
                    # agent-token-authenticated tool call never incremented that
                    # principal's usage:spend:agent_principal:{id}:{yyyymm} counter.
                    agent_principal_id=authz.agent_principal_id,
                )
            )

        return McpCallOutcome(body=relayed_body)


__all__ = ["McpCallOutcome", "McpCallUseCase"]
