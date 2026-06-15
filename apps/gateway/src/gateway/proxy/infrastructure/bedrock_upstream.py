"""Infrastructure adapter: BedrockCompletionUpstream.

Translates OpenAI chat-completions ⇄ AWS Bedrock Converse API for non-streaming
(complete) path. Streaming is implemented in v20 task 3.

Wire protocol:
  POST https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/converse
  Headers: Authorization (SigV4), x-amz-date, x-amz-content-sha256, content-type
  Body: Bedrock Converse request JSON (raw bytes to preserve the signed payload hash)

Resilience mirrors AnthropicCompletionUpstream:
  - httpx.AsyncClient with per-timeout knobs
  - Per-instance CircuitBreaker (5 consecutive failures → 30 s open)
  - 5xx / transport errors → UpstreamUnavailableError (gateway → 502 / fallback)
  - 4xx → pass through as OpenAI-shaped error body; no exception raised

Security:
  The AWS secret_access_key is a SECRET — NEVER logged, echoed, committed, or placed in
  metric labels / span attributes / exception messages. The signed x-amz-content-sha256
  MUST match the exact POSTed bytes (use content=body_bytes, never json= which re-encodes).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials, sign_request
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# Pure translation helpers (module-level, no I/O — unit-tested directly)
# ---------------------------------------------------------------------------


def _map_finish_reason(stop_reason: str | None) -> str:
    """Map Bedrock stopReason → OpenAI finish_reason.

    end_turn            → "stop"
    max_tokens          → "length"
    stop_sequence       → "stop"
    tool_use            → "tool_calls"
    content_filtered    → "content_filter"
    guardrail_intervened → "content_filter"
    None / unknown      → "stop"
    """
    mapping: dict[str, str] = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "content_filtered": "content_filter",
        "guardrail_intervened": "content_filter",
    }
    return mapping.get(stop_reason or "", "stop")


def _openai_to_converse_request(
    payload: dict[str, Any],
    *,
    default_max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Translate an OpenAI chat-completions request body → Bedrock Converse body.

    - role=="system" messages are lifted to top-level ``system``:[{text}] list.
      Multiple system messages are each added as a separate {text} entry.
      When no system messages are present, the ``system`` key is OMITTED entirely.
    - user/assistant messages map to Bedrock messages format:
      [{role, content:[{text}]}]
    - inferenceConfig: maxTokens (payload.get("max_tokens", default_max_tokens)),
      temperature/topP(top_p)/stopSequences(stop) when present in payload.
      stop: str → [str]; stop: list → as-is.

    Returns (model_id, converse_body).
    """
    model_id: str = payload["model"]
    messages: list[dict[str, Any]] = payload.get("messages", [])

    system_blocks: list[dict[str, str]] = []
    converse_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_blocks.append({"text": str(content)})
        else:
            converse_messages.append(
                {
                    "role": role,
                    "content": [{"text": str(content)}],
                }
            )

    # inferenceConfig — required keys + optional pass-throughs
    inference_config: dict[str, Any] = {
        "maxTokens": payload.get("max_tokens", default_max_tokens),
    }
    if "temperature" in payload:
        inference_config["temperature"] = payload["temperature"]
    if "top_p" in payload:
        inference_config["topP"] = payload["top_p"]

    stop = payload.get("stop")
    if stop is not None:
        inference_config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)

    body: dict[str, Any] = {
        "messages": converse_messages,
        "inferenceConfig": inference_config,
    }

    # Only include "system" key when there are system messages
    if system_blocks:
        body["system"] = system_blocks

    return model_id, body


def _converse_to_openai(resp_json: dict[str, Any], *, model_id: str) -> dict[str, Any]:
    """Translate a Bedrock Converse 200 response → OpenAI chat.completion body.

    Content: concatenate all output.message.content[].text fields.
    Usage: inputTokens→prompt_tokens, outputTokens→completion_tokens,
           totalTokens→total_tokens (falls back to input+output when absent).
    Defensive: missing output/usage → empty content "" / zero usage, never raise.
    """
    # Extract concatenated text content from output.message.content blocks
    output: dict[str, Any] = resp_json.get("output", {})
    msg: dict[str, Any] = output.get("message", {})
    content_blocks: list[dict[str, Any]] = msg.get("content", [])
    content_text = "".join(b.get("text", "") for b in content_blocks)

    # Usage mapping
    usage_raw: dict[str, Any] = resp_json.get("usage", {})
    prompt_tokens: int = usage_raw.get("inputTokens", 0)
    completion_tokens: int = usage_raw.get("outputTokens", 0)
    total_tokens_raw = usage_raw.get("totalTokens")
    total_tokens: int = (
        int(total_tokens_raw)
        if total_tokens_raw is not None
        else (prompt_tokens + completion_tokens)
    )

    return {
        "id": resp_json.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content_text,
                },
                "finish_reason": _map_finish_reason(resp_json.get("stopReason")),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def _bedrock_error_to_openai(resp_json: dict[str, Any], status: int) -> dict[str, Any]:
    """Translate a Bedrock error body → OpenAI error envelope.

    Bedrock error shape: {"message": "<text>", "__type": "<ExceptionType>"}
    OpenAI shape: {"error": {"message": <str>, "type": "bedrock_error", "code": <status>}}
    """
    message: str = str(resp_json.get("message", ""))
    return {
        "error": {
            "message": message,
            "type": "bedrock_error",
            "code": status,
        }
    }


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class BedrockCompletionUpstream:
    """Forwards chat completions to the AWS Bedrock Converse API.

    Implements the CompletionUpstream Protocol (complete + stream).
    Translates OpenAI chat-completions ⇄ Bedrock Converse API shapes.
    Signs every request with AWS Signature Version 4 via bedrock_sigv4.

    A single instance is shared for the lifetime of the application.
    The circuit breaker state is per-instance (per-replica).

    SECURITY: the AWS secret_access_key is NEVER logged, echoed, or placed in any
    metric label / span attribute / exception message.
    """

    def __init__(
        self,
        *,
        credentials: AwsCredentials,
        region: str,
        endpoint_url: str | None = None,
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # Credentials stored privately — never exposed in logs/errors/metrics
        self._credentials = credentials
        self._region = region
        self._endpoint = (endpoint_url or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip(
            "/"
        )
        self._default_max_tokens = default_max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry

        self._breaker = CircuitBreaker()
        # NOTE: No base_url set — the signer needs the full absolute URL to compute
        # the canonical host header and canonical URI correctly.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            )
        )

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the Bedrock Converse API.

        Returns (status_code, openai_shaped_body).
        - 200  → translated OpenAI chat.completion
        - 4xx  → OpenAI error body (no exception; gateway forwards the status)
        - 5xx / 429 / 408 / connect error / pool timeout → retried up to max_retries;
          exhausted → UpstreamUnavailableError

        Request translation is pure and runs ONCE, outside the retry loop.
        The body is serialized ONCE to bytes; the SigV4 x-amz-content-sha256 is
        computed from those same bytes — NEVER re-encoded by httpx (content= not json=).
        """
        model_id, converse_body = _openai_to_converse_request(
            payload, default_max_tokens=self._default_max_tokens
        )

        # The model_id is placed RAW into the path (it carries a ':' version suffix, e.g.
        # ...-v2:0). This matches botocore exactly: the wire request-target keeps the literal
        # ':' (a valid RFC-3986 pchar), and AWS canonicalizes it to %3A for signature
        # verification — the SAME single-encode our signer applies via quote(path, safe="/~").
        # The IDENTICAL url string is handed to BOTH sign_request and client.post so the
        # signed canonical URI and the wire target stay in lock-step. Verified against
        # botocore SigV4Auth: canonical '/model/...v2%3A0/converse', wire '/model/...v2:0/...'.
        # (Double-encoding would route to a non-existent model '...v2%3A0' and break signing.)
        url = f"{self._endpoint}/model/{model_id}/converse"

        body_bytes = json.dumps(converse_body, separators=(",", ":")).encode("utf-8")

        async def _do_request() -> httpx.Response:
            sig_headers = sign_request(
                method="POST",
                url=url,
                body=body_bytes,
                service="bedrock",
                region=self._region,
                credentials=self._credentials,
                timestamp=datetime.now(UTC),
            )
            headers = {**sig_headers, "content-type": "application/json"}
            # Use content=body_bytes (raw bytes) so the signed x-amz-content-sha256
            # matches the wire body EXACTLY — httpx must NOT re-encode the body.
            return await self._client.post(url, content=body_bytes, headers=headers)

        def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
            if resp.status_code >= 400:
                return resp.status_code, _bedrock_error_to_openai(resp.json(), resp.status_code)
            return 200, _converse_to_openai(resp.json(), model_id=model_id)

        return await execute_with_retry(
            _do_request,
            _render,
            breaker=self._breaker,
            provider="bedrock",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Bedrock streaming arrives in v20 task 3.

        Raises NotImplementedError immediately — do NOT iterate the result.
        """
        raise NotImplementedError("Bedrock streaming arrives in v20 task 3")
