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
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BedrockCredential, ProviderKeyMissing
from gateway.proxy.domain.tool_translation import dump_tool_arguments, load_tool_arguments
from gateway.proxy.infrastructure.bedrock_eventstream import aiter_event_stream
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


def _tools_to_converse(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI tools → Bedrock Converse toolConfig.tools (toolSpec shape).

    Each {type:function, function:{name, description?, parameters?}} becomes:
    {"toolSpec": {"name": fn["name"],
                  "description": fn["description"],  (omitted when absent)
                  "inputSchema": {"json": fn.get("parameters", {})}}}
    """
    out: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        ts: dict[str, Any] = {"name": fn["name"]}
        if "description" in fn:
            ts["description"] = fn["description"]
        ts["inputSchema"] = {"json": fn.get("parameters", {})}
        out.append({"toolSpec": ts})
    return out


def _tool_choice_to_converse(choice: Any) -> dict[str, Any] | None:
    """Translate OpenAI tool_choice → Bedrock Converse toolChoice.

    "auto"      -> {"auto": {}}
    "required"  -> {"any": {}}
    {"type":"function","function":{"name":n}} -> {"tool": {"name": n}}
    "none" / None / anything else -> None  (caller omits the toolChoice key)
    """
    if choice == "auto":
        return {"auto": {}}
    if choice == "required":
        return {"any": {}}
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = choice.get("function", {}).get("name", "")
        return {"tool": {"name": name}}
    return None


def _assistant_tool_calls_to_content(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a Bedrock Converse assistant content-block list from an OpenAI assistant message.

    Any text ``content`` becomes a leading ``{"text": text}`` block; each entry in
    ``tool_calls`` becomes a ``{"toolUse": {"toolUseId", "name", "input"}}`` block
    (``arguments`` JSON string → ``input`` dict via load_tool_arguments).
    """
    blocks: list[dict[str, Any]] = []
    text = msg.get("content")
    if isinstance(text, str) and text:
        blocks.append({"text": text})
    for call in msg.get("tool_calls", []):
        fn = call.get("function", {})
        blocks.append(
            {
                "toolUse": {
                    "toolUseId": call.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": load_tool_arguments(fn.get("arguments", "")),
                }
            }
        )
    return blocks


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

    # Buffer for consecutive role:"tool" messages — flushed into ONE user message.
    pending_tool_results: list[dict[str, Any]] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            converse_messages.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            _flush_tool_results()
            system_blocks.append({"text": str(content)})
        elif role == "tool":
            # Accumulate into pending buffer; flush happens on next non-tool message.
            pending_tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": msg.get("tool_call_id", ""),
                        "content": [{"text": str(msg.get("content", ""))}],
                        "status": "success",
                    }
                }
            )
        elif role == "assistant" and msg.get("tool_calls"):
            _flush_tool_results()
            converse_messages.append(
                {"role": "assistant", "content": _assistant_tool_calls_to_content(msg)}
            )
        else:
            _flush_tool_results()
            converse_messages.append(
                {
                    "role": role,
                    "content": [{"text": str(content)}],
                }
            )

    # Flush any trailing tool results that ended the message list.
    _flush_tool_results()

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

    # toolConfig — only when payload carries tools; toolChoice omitted for "none"/None.
    if payload.get("tools"):
        tool_config: dict[str, Any] = {"tools": _tools_to_converse(payload["tools"])}
        tc = _tool_choice_to_converse(payload.get("tool_choice"))
        if tc is not None:
            tool_config["toolChoice"] = tc
        body["toolConfig"] = tool_config

    return model_id, body


def _converse_to_openai(resp_json: dict[str, Any], *, model_id: str) -> dict[str, Any]:
    """Translate a Bedrock Converse 200 response → OpenAI chat.completion body.

    Content: concatenate all output.message.content[].text fields.
    Usage: inputTokens→prompt_tokens, outputTokens→completion_tokens,
           totalTokens→total_tokens (falls back to input+output when absent).
    Defensive: missing output/usage → empty content "" / zero usage, never raise.
    """
    # Walk output.message.content[] once: accumulate text and toolUse blocks.
    output: dict[str, Any] = resp_json.get("output", {})
    msg: dict[str, Any] = output.get("message", {})
    content_blocks: list[dict[str, Any]] = msg.get("content", [])

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(
                {
                    "id": tu.get("toolUseId", ""),
                    "type": "function",
                    "function": {
                        "name": tu.get("name", ""),
                        "arguments": dump_tool_arguments(tu.get("input", {})),
                    },
                }
            )

    content_text = "".join(text_parts)

    # content: text string when present; None when only tool calls; "" otherwise.
    content: str | None = content_text if content_text else (None if tool_calls else "")
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls

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
                "message": message,
                "finish_reason": _map_finish_reason(resp_json.get("stopReason")),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


class _BedrockSSEStepper:
    """Stateful ConverseStream → OpenAI-SSE translator, fed one event at a time.

    ``step(event_type, payload)`` yields 0+ OpenAI frames for that event (messageStart
    → role, contentBlockDelta → content) and captures stop_reason / usage from
    messageStop / metadata. ``finish()`` ALWAYS emits the terminal frame
    (finish_reason + usage) then ``[DONE]`` — exactly once, after the last event.

    This single core drives BOTH the buffered wrapper (``_converse_stream_to_openai_sse``)
    and the live streaming adapter, so each frame flows as its source event arrives,
    byte-identical to the buffered output. ``created`` is stamped once at construction.
    """

    def __init__(self, *, model_id: str) -> None:
        self._stop_reason: str | None = None
        self._usage: dict[str, Any] = {}
        self._terminal_emitted: bool = False
        self._base: dict[str, Any] = {
            "id": "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
        }

    def step(self, event_type: str, payload: dict[str, Any]) -> Iterator[bytes]:
        if event_type == "messageStart":
            chunk = {
                **self._base,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_type == "contentBlockDelta":
            text = payload.get("delta", {}).get("text")
            if text is not None:
                chunk = {
                    **self._base,
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                }
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_type == "contentBlockStop":
            # Skip — no frame emitted
            pass

        elif event_type == "messageStop":
            self._stop_reason = payload.get("stopReason")

        elif event_type == "metadata":
            self._usage = payload.get("usage", {})

    def finish(self) -> Iterator[bytes]:
        # Terminal frame — always emitted ONCE, even when no events were seen.
        # Idempotent: a second finish() call yields nothing (guards against duplicate [DONE]).
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        input_tokens: int = self._usage.get("inputTokens", 0)
        output_tokens: int = self._usage.get("outputTokens", 0)
        total_tokens_raw = self._usage.get("totalTokens")
        total_tokens: int = (
            int(total_tokens_raw) if total_tokens_raw is not None else input_tokens + output_tokens
        )

        terminal: dict[str, Any] = {
            **self._base,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": _map_finish_reason(self._stop_reason),
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
        }
        yield b"data: " + json.dumps(terminal).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"


def _converse_stream_to_openai_sse(  # pyright: ignore[reportUnusedFunction]  # kept as the byte-identical translation contract exercised by the unit tests
    events: list[tuple[str, dict[str, Any]]],
    *,
    model_id: str,
) -> Iterator[bytes]:
    """Translate a list of (event_type, payload_dict) ConverseStream events → OpenAI SSE bytes.

    Thin buffered wrapper over ``_BedrockSSEStepper`` (byte-identical to the historical
    implementation). The streaming adapter drives the same stepper live.
    """
    stepper = _BedrockSSEStepper(model_id=model_id)
    for event_type, payload in events:
        yield from stepper.step(event_type, payload)
    yield from stepper.finish()


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
        endpoint_url: str | None = None,
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # No boot credentials — resolved per-request from the contextvar (task-3 BYOK).
        self._endpoint_url_override = endpoint_url  # None = derive from credential region
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

    def _get_credentials(self) -> AwsCredentials:
        """Read BedrockCredential from the request contextvar and convert to AwsCredentials.

        Fail-CLOSED: raises ProviderKeyMissing("bedrock") if the contextvar is unset or
        holds a wrong-type credential — NEVER produces an unsigned upstream request.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BedrockCredential):
            raise ProviderKeyMissing("bedrock")
        return cred.to_aws_credentials()

    def _build_endpoint(self, aws: AwsCredentials) -> str:
        """Derive the Bedrock endpoint from the credential's region (or ctor override)."""
        return (
            self._endpoint_url_override or f"https://bedrock-runtime.{aws.region}.amazonaws.com"
        ).rstrip("/")

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the Bedrock Converse API.

        Returns (status_code, openai_shaped_body).
        - 200  → translated OpenAI chat.completion
        - 4xx  → OpenAI error body (no exception; gateway forwards the status)
        - 5xx / 429 / 408 / connect error / pool timeout → retried up to max_retries;
          exhausted → UpstreamUnavailableError

        Credential is read from the request contextvar (BedrockCredential); raises
        ProviderKeyMissing("bedrock") before any HTTP if unset or wrong type (fail-closed).

        Request translation is pure and runs ONCE, outside the retry loop.
        The body is serialized ONCE to bytes; the SigV4 x-amz-content-sha256 is
        computed from those same bytes — NEVER re-encoded by httpx (content= not json=).
        """
        # Fail-closed: read & validate credential BEFORE any network activity.
        aws = self._get_credentials()
        endpoint = self._build_endpoint(aws)

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
        url = f"{endpoint}/model/{model_id}/converse"

        body_bytes = json.dumps(converse_body, separators=(",", ":")).encode("utf-8")

        async def _do_request() -> httpx.Response:
            sig_headers = sign_request(
                method="POST",
                url=url,
                body=body_bytes,
                service="bedrock",
                region=aws.region,
                credentials=aws,
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
        """Return an async generator that yields OpenAI SSE chunk bytes.

        The circuit breaker is checked before the stream opens.  The AWS binary
        EventStream is decoded INCREMENTALLY via ``aiter_event_stream`` and translated
        live by ``_BedrockSSEStepper`` — each OpenAI SSE chunk is yielded as its source
        ConverseStream frame completes on the wire (TTFB ≈ first token); finish() emits
        the terminal frame + [DONE].

        The status check happens BEFORE the first ``yield``, so a 5xx raises
        UpstreamUnavailableError with zero chunks emitted (BS7).

        Uses ``content=body_bytes`` (raw bytes) so the signed x-amz-content-sha256
        matches the wire body exactly — httpx must NOT re-encode the body.

        SECURITY: the AWS secret_access_key is NEVER logged or echoed.
        """
        # Fail-closed: read & validate credential BEFORE yielding the generator object.
        # This raises ProviderKeyMissing("bedrock") synchronously if unset/wrong-type,
        # before the circuit-breaker guard and before the first byte.
        aws = self._get_credentials()
        endpoint = self._build_endpoint(aws)
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            model_id, converse_body = _openai_to_converse_request(
                payload, default_max_tokens=self._default_max_tokens
            )
            # Raw model_id in the path — same URL handed to sign_request AND
            # client.stream so the signed canonical URI and the wire target
            # stay in lock-step (v20 task-2 %3A rule: no double-encode).
            url = f"{endpoint}/model/{model_id}/converse-stream"
            body_bytes = json.dumps(converse_body, separators=(",", ":")).encode("utf-8")

            try:
                sig = sign_request(
                    method="POST",
                    url=url,
                    body=body_bytes,
                    service="bedrock",
                    region=aws.region,
                    credentials=aws,
                    timestamp=datetime.now(UTC),
                )
                headers = {**sig, "content-type": "application/json"}

                async with self._client.stream(
                    "POST", url, content=body_bytes, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        self._breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {resp.status_code} on stream"
                        )
                    self._breaker.record_success()

                    # Drive both layers LIVE: aiter_event_stream decodes each binary
                    # EventStream frame as it completes on the wire; the stepper
                    # translates it to OpenAI SSE and yields it immediately (incremental
                    # delivery — TTFB ≈ first token). finish() emits the terminal frame.
                    stepper = _BedrockSSEStepper(model_id=model_id)
                    async for ev_headers, ev_payload in aiter_event_stream(resp.aiter_bytes()):
                        for frame in stepper.step(
                            ev_headers.get(":event-type", ""), json.loads(ev_payload)
                        ):
                            yield frame
                    for frame in stepper.finish():
                        yield frame

            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()
