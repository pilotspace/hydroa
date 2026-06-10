"""Usage recorder adapters.

NoopUsageRecorder: default wired in main.py.
The usage-metering task will replace it with a real implementation.
"""

from __future__ import annotations

import uuid


class NoopUsageRecorder:
    """No-op implementation of the UsageRecorder port.

    Silently discards all records. Safe to share across requests.
    """

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, object] | None,
        status: int,
    ) -> None:
        """Discard the usage event."""
        return
