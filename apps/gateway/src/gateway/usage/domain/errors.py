"""Domain errors for the usage metering module — zero framework imports."""

from __future__ import annotations


class UsageMeteringError(Exception):
    """Base error for usage metering domain."""


class PricingNotFoundError(UsageMeteringError):
    """No pricing snapshot found for the given model."""

    def __init__(self, model_id: str) -> None:
        super().__init__(f"No pricing snapshot found for model: {model_id}")
        self.model_id = model_id
