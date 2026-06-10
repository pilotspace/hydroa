"""Application use cases for the proxy module.

The CompletionUseCase is the single orchestrator:
  1. Authenticate key → tenant/key ids
  2. Validate payload (model, messages)
  3. Check model is active in catalog
  4. Delegate to CompletionUpstream (circuit-breaker-wrapped via BoundCircuitBreakerUpstream)
  5. Fire-and-forget UsageRecorder

The circuit breaker lives in the infrastructure layer (BoundCircuitBreakerUpstream),
so this layer only sees UpstreamUnavailableError or CircuitOpenError on failures.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from gateway.core.errors import ProblemError
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import (
    CompletionUpstream,
    KeyAuthenticator,
    ModelChecker,
    UsageRecorder,
)


def _fire_record(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    status: int,
) -> None:
    """Schedule a fire-and-forget usage record.

    Stores the Task reference to satisfy RUF006 (avoids garbage-collected task).
    Failures in the recorder are intentionally ignored — recording must never
    affect the caller's response.
    """
    task = asyncio.ensure_future(
        usage_recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            usage=usage,
            status=status,
        )
    )
    # Suppress unhandled-exception noise if recorder raises unexpectedly.
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


class CompletionUseCase:
    """Orchestrate a single /v1/chat/completions request."""

    def __init__(
        self,
        authenticator: KeyAuthenticator,
        model_checker: ModelChecker,
    ) -> None:
        self._authenticator = authenticator
        self._model_checker = model_checker

    async def _authenticate(self, raw_key: str | None) -> tuple[uuid.UUID, uuid.UUID]:
        """Extract bearer key and return (tenant_id, key_id).

        Raises ProblemError 401 on any failure.
        """
        if not raw_key:
            raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Missing or invalid API key")
        try:
            result = await self._authenticator.authenticate(raw_key)
        except InvalidApiKeyError:
            raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Missing or invalid API key") from None
        return result.tenant_id, result.key_id

    async def _validate_payload(self, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """Validate model and messages fields.

        Returns (model_id, messages).
        Raises ProblemError 422/400 on validation failure.
        """
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise ProblemError(
                422, "ERR_PAYLOAD_INVALID", "Field 'model' is required and non-empty"
            )

        messages = body.get("messages")
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            raise ProblemError(
                422,
                "ERR_PAYLOAD_INVALID",
                "Field 'messages' must be a non-empty list",
            )

        is_active = await self._model_checker.is_active(model_id)
        if not is_active:
            raise ProblemError(400, "ERR_MODEL_UNKNOWN", f"Model '{model_id}' is not available")

        return model_id, messages

    async def complete(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Handle a non-streaming completion.

        Returns (status_code, json_body).
        On upstream 4xx: pass-through verbatim.
        On upstream 5xx / circuit open: raise ProblemError 502.
        """
        tenant_id, key_id = await self._authenticate(raw_key)
        model_id, _ = await self._validate_payload(body)

        try:
            status, response_body = await upstream.complete(body)
        except (UpstreamUnavailableError, CircuitOpenError):
            # Circuit-breaker proxy has already counted the failure.
            _fire_record(
                usage_recorder,
                tenant_id=tenant_id,
                key_id=key_id,
                model=model_id,
                usage=None,
                status=502,
            )
            raise ProblemError(
                502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream service unavailable"
            ) from None

        # Record successful or upstream 4xx completion
        _fire_record(
            usage_recorder,
            tenant_id=tenant_id,
            key_id=key_id,
            model=model_id,
            usage=response_body.get("usage"),  # type: ignore[arg-type]
            status=status,
        )
        return status, response_body

    async def stream(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
    ) -> AsyncIterator[bytes]:
        """Handle a streaming completion.

        Authenticates and validates before yielding any bytes.
        Returns an async generator of raw SSE byte chunks.
        On upstream error: raises ProblemError 502.
        """
        tenant_id, key_id = await self._authenticate(raw_key)
        model_id, _ = await self._validate_payload(body)

        try:
            gen = upstream.stream(body)
        except (UpstreamUnavailableError, CircuitOpenError):
            _fire_record(
                usage_recorder,
                tenant_id=tenant_id,
                key_id=key_id,
                model=model_id,
                usage=None,
                status=502,
            )
            raise ProblemError(
                502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream service unavailable"
            ) from None

        async def _wrapped() -> AsyncIterator[bytes]:
            try:
                async for chunk in gen:
                    yield chunk
            except (UpstreamUnavailableError, CircuitOpenError):
                # Can't change status code mid-stream; record and stop
                _fire_record(
                    usage_recorder,
                    tenant_id=tenant_id,
                    key_id=key_id,
                    model=model_id,
                    usage=None,
                    status=502,
                )
                return

        _fire_record(
            usage_recorder,
            tenant_id=tenant_id,
            key_id=key_id,
            model=model_id,
            usage=None,
            status=200,
        )
        return _wrapped()
