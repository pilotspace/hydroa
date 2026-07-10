"""NoopPayloadCapture — safe default/off implementation of PayloadCapturePort.

Mirrors NoopUsageRecorder's shape exactly (proxy/infrastructure/usage_recorder.py):
silently discards every capture call. Used as CompletionUseCase's implicit
default (payload_capture=None → no dispatch at all, see _dispatch_capture) and
available as an explicit opt-out for ops (e.g. an emergency capture kill switch)
without touching the real SqlAlchemyPayloadCapture wiring.
"""

from __future__ import annotations

import uuid
from typing import Any


class NoopPayloadCapture:
    """No-op implementation of the PayloadCapturePort port.

    Silently discards all capture calls. Safe to share across requests.
    """

    async def capture(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        request_body: dict[str, Any],
        response_body: dict[str, Any] | None,
        status: int,
        stream: bool,
        cached: bool,
        guardrail_configs: dict[str, Any],
    ) -> None:
        """Discard the capture request."""
        return
