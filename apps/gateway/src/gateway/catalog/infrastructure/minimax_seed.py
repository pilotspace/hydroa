"""Fixed seed list of known MiniMax chat model entries — minimax-catalog-seed TASK.md §3.

Mirrors OPENAI_SEED_MODELS' shape (openai_seed.py). MiniMax has no OpenRouter-style discovery
API wired into this proxy, so these 3 "Current"-tier models are hand-seeded with real pay-as-you-go
pricing (https://platform.minimax.io/docs/guides/pricing-paygo.md, fetched 2026-07-01). The 5
"Legacy" MiniMax models (M2.5/M2.1/M2 + highspeed variants) are deliberately NOT seeded here — see
TASK.md §1 Assumptions; add them to this list later if needed, additive-only.

MiniMax-M3 pricing is CONTEXT-TIERED upstream ($0.30/$1.20 per-M-tokens in/out for <=512k context,
$0.60/$2.40 for >512k) but CatalogModel supports only one flat price pair per id — the <=512k
(cheaper) tier is used as the flat rate; a >512k-context request is under-billed relative to
MiniMax's real invoice (documented limitation, TASK.md §1 top ⚠).

catalog-pricing-fields TASK.md §3: all 3 models also carry cached_input_usd_per_token — MiniMax's
real, officially-published prompt-caching (cache-hit) price of $0.06/M tokens (0.00000006 usd/token)
for every one of these 3 models, confirmed 2026-07-01 via the same pricing-paygo page (the >512k
tier's cache-hit price is $0.12/M — the same <=512k-flat-rate limitation noted above applies here
too, tracked by the same TASK.md §1 ⚠).
"""

from __future__ import annotations

from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel

MINIMAX_SEED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="MiniMax-M3",
        name="MiniMax-M3",
        context_length=1_000_000,
        # audit-remediation package C1: Decimal literals (money-is-Decimal rule).
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000012"),
        modality="chat",
        provider="minimax",
        input_modalities="text",
        cached_input_usd_per_token=0.00000006,
    ),
    CatalogModel(
        id="MiniMax-M2.7",
        name="MiniMax-M2.7",
        context_length=204_800,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000012"),
        modality="chat",
        provider="minimax",
        input_modalities="text",
        cached_input_usd_per_token=0.00000006,
    ),
    CatalogModel(
        id="MiniMax-M2.7-highspeed",
        name="MiniMax-M2.7-highspeed",
        context_length=204_800,
        prompt_usd_per_token=Decimal("0.0000006"),
        completion_usd_per_token=Decimal("0.0000024"),
        modality="chat",
        provider="minimax",
        input_modalities="text",
        cached_input_usd_per_token=0.00000006,
    ),
]

__all__ = ["MINIMAX_SEED_MODELS"]
