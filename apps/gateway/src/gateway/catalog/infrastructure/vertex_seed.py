"""Fixed seed list of Google Vertex AI Gemini-publisher catalog entries —
vertex-adapter TASK.md §3.

Mirrors bedrock_seed.py / minimax_seed.py / gpt_realtime_seed.py's shape (a provider
with no OpenRouter-style dynamic discovery gets a hand-written list[CatalogModel]).

id scheme (TASK.md §0 Issue #2 / §3 M8): unlike Bedrock's cross-region inference
profiles, Vertex model ids (e.g. "gemini-2.5-flash") are always region-BARE — there is
no upstream-real disambiguator for the SAME model served from two GCP locations. Each
row therefore uses a GATEWAY-INVENTED synthetic `<region-code>.<bare-model>` id
(`eu.gemini-2.5-flash`, `ap.gemini-2.5-flash`, ...) — visually parallel to Bedrock's own
`us.`/`eu.`/`apac.` convention, but NOT an upstream-real Vertex/GCP identifier. A future
reader must never confuse the two. `VertexCompletionUpstream._parse_vertex_model` parses
and STRIPS this prefix before the bare model name ever reaches the Vertex URL/body.

Location mapping (TASK.md §0 Issue #3 / §1 M5, DECIDED at freeze 2026-07-12):
  eu. -> region="eu" -> europe-west4   (MILESTONE.md said "europe-west*"; europe-west4
                                         confirmed at freeze — cheap to change, one
                                         string literal in vertex_upstream._ID_PREFIX_TO_LOCATION
                                         + this file's ids, no ripple, if a live check
                                         later shows a different literal location.)
  ap. -> region="ap" -> asia-southeast1

Pricing (TASK.md §1 Assumption #3, flagged UNCONFIRMED-live — this repo has never made
a real Vertex call): assumed identical to the public Gemini Developer API's published
per-token rates for the same model names (a documented GCP convention: Vertex AI's
Gemini-publisher pricing matches the Gemini API's), fetched 2026-07-12:
  Gemini 2.5 Flash: $0.30 / 1M prompt tokens,  $2.50  / 1M completion tokens
  Gemini 2.5 Pro:   $1.25 / 1M prompt tokens,  $10.00 / 1M completion tokens
    (Pro's published rate is tiered above 200K prompt tokens to $2.50/$15.00 per 1M;
    CatalogModel has no tiered-pricing field, so the <=200K base tier is seeded here —
    same single-tier-of-a-tiered-rate simplification as this codebase's other seed
    files; a genuine, disclosed, cheap-to-revisit approximation, not an oversight).
Context length: 1,048,576 tokens (~1M) for both models (Google's published context
window for Gemini 2.5 Flash/Pro).

`region=` forward-cites region-catalog-dimension's frozen `Region` field — it IS
present in this tree (region-catalog-dimension's Build has already landed on this
branch per the M2 wave dispatch context), so these rows carry a real, non-default
region tag from the first sync, not a "global" no-op placeholder.

M10/R6 (vertex-adapter TASK.md): these rows MUST land in the SAME diff as
main.py's `_chat_adapters["vertex"]` registration — never seed a provider="vertex"
catalog row without the matching adapter already registered.
"""

from __future__ import annotations

from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel

_CONTEXT_LENGTH = 1_048_576  # ~1M tokens — Gemini 2.5 Flash/Pro published context window

# audit-remediation package C1: Decimal literals (money-is-Decimal rule).
_FLASH_PROMPT_USD_PER_TOKEN = Decimal("0.0000003")  # $0.30 / 1M
_FLASH_COMPLETION_USD_PER_TOKEN = Decimal("0.0000025")  # $2.50 / 1M
_PRO_PROMPT_USD_PER_TOKEN = Decimal("0.00000125")  # $1.25 / 1M (<=200K base tier)
_PRO_COMPLETION_USD_PER_TOKEN = Decimal("0.00001")  # $10.00 / 1M (<=200K base tier)

VERTEX_SEED_MODELS: list[CatalogModel] = [
    # --- Gemini 2.5 Flash -------------------------------------------------
    CatalogModel(
        id="eu.gemini-2.5-flash",
        name="Gemini 2.5 Flash (Vertex AI, EU)",
        context_length=_CONTEXT_LENGTH,
        prompt_usd_per_token=_FLASH_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_FLASH_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="vertex",
        region="eu",
    ),
    CatalogModel(
        id="ap.gemini-2.5-flash",
        name="Gemini 2.5 Flash (Vertex AI, AP)",
        context_length=_CONTEXT_LENGTH,
        prompt_usd_per_token=_FLASH_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_FLASH_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="vertex",
        region="ap",
    ),
    # --- Gemini 2.5 Pro -----------------------------------------------------
    CatalogModel(
        id="eu.gemini-2.5-pro",
        name="Gemini 2.5 Pro (Vertex AI, EU)",
        context_length=_CONTEXT_LENGTH,
        prompt_usd_per_token=_PRO_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_PRO_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="vertex",
        region="eu",
    ),
    CatalogModel(
        id="ap.gemini-2.5-pro",
        name="Gemini 2.5 Pro (Vertex AI, AP)",
        context_length=_CONTEXT_LENGTH,
        prompt_usd_per_token=_PRO_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_PRO_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="vertex",
        region="ap",
    ),
]

__all__ = ["VERTEX_SEED_MODELS"]
