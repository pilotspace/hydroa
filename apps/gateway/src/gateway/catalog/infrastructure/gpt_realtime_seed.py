"""Fixed seed entry for OpenAI's GPT-Realtime model — gpt-realtime-pricing-fields TASK.md §3.

Mirrors minimax_seed.py's shape. GPT-Realtime has no OpenRouter-style discovery API wired into
this proxy, so it is hand-seeded with real, dual-stream (text + audio) pay-as-you-go pricing
(https://developers.openai.com/api/docs/models/gpt-realtime, fetched 2026-07-02):

  text:  $4.00/1M prompt, $16.00/1M completion, $0.40/1M cached input
  audio: $32.00/1M prompt, $64.00/1M completion, $0.40/1M cached input

Tin explicitly chose (2026-07-02, AskUserQuestion) to switch the realtime relay's default model
to "gpt-realtime" (the current GA model) rather than seed the older "gpt-4o-realtime-preview"
preview model it previously defaulted to — see core/config.py:realtime_relay_openai_model.
"""

from __future__ import annotations

from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel

GPT_REALTIME_SEED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="gpt-realtime",
        name="gpt-realtime",
        context_length=None,
        # audit-remediation package C1: Decimal literals (money-is-Decimal rule).
        prompt_usd_per_token=Decimal("0.000004"),
        completion_usd_per_token=Decimal("0.000016"),
        modality="chat",
        provider="openai",
        input_modalities="text",
        cached_input_usd_per_token=0.0000004,
        audio_prompt_usd_per_token=0.000032,
        audio_completion_usd_per_token=0.000064,
        audio_cached_usd_per_token=0.0000004,
    ),
]

__all__ = ["GPT_REALTIME_SEED_MODELS"]
