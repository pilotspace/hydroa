"""Fixed seed list of AWS Bedrock cross-region inference-profile entries —
region-catalog-dimension TASK.md §3.

Mirrors minimax_seed.py / gpt_realtime_seed.py's shape (a provider with no
OpenRouter-style dynamic discovery gets a hand-written list[CatalogModel]). Bedrock's
chat adapter (BedrockCompletionUpstream, main.py:982) is ALREADY registered
unconditionally but has ZERO existing catalog rows today — seeding it is new ground
this task establishes (TASK.md §0 Issue #3), not a pure region-tag-on-existing-rows
change.

id scheme (TASK.md §0 Issue #4 / §1 R3): `models.id` is the catalog's bare-string
PRIMARY KEY, and the SAME literal Bedrock model id is reachable from multiple AWS
regions — so each row uses AWS's own REAL cross-region inference-profile id
(`us.`/`eu.`/`apac.`-prefixed) as the catalog `id`. These ids are already globally
unique by AWS's own convention (no synthetic namespace invented) and are directly
usable by BedrockCompletionUpstream unchanged.

Coarse `region` granularity (TASK.md §0 Issue #5 + DECIDED addendum, mid-freeze Asia
grow): each cross-region inference profile fans out server-side across a REGION
GROUP, documented here as a comment, never a 5th schema column or extra rows:
  us.    -> region="us" -> US East (N. Virginia us-east-1) / US West (Oregon us-west-2)
  eu.    -> region="eu" -> Frankfurt eu-central-1 / Ireland eu-west-1 /
                            Paris eu-west-3 / Stockholm eu-north-1
  apac.  -> region="ap" -> Tokyo ap-northeast-1 / Seoul ap-northeast-2 /
                            Singapore ap-southeast-1 / Sydney ap-southeast-2 /
                            Mumbai ap-south-1

Pricing: on-demand per-token USD, matching AWS Bedrock's published (and Anthropic's
direct-API-parity) rates for these two models (fetched 2026-07-12) — identical
across every region group AWS bills these cross-region profiles at:
  Claude 3.5 Sonnet v2: $3.00 / 1M prompt tokens, $15.00 / 1M completion tokens
  Claude 3.5 Haiku:     $0.80 / 1M prompt tokens, $4.00  / 1M completion tokens

Seeding BOTH "us" and "eu"/"ap" siblings (TASK.md §1 lowest-confidence assumption 3):
an EU/AP-only Bedrock catalog would leave no region="us"-pinned tenant a Bedrock
candidate to route to — a low-cost-if-wrong symmetric choice (dropping "us" rows
later is a pure subtraction).

Vertex is explicitly OUT of scope here (TASK.md §1 R2 / §3 Scope-cut) — no
provider="vertex" row is ever added by this file. Vertex ships in the sibling
vertex-adapter task.
"""

from __future__ import annotations

from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel

_SONNET_V2_SUFFIX = "anthropic.claude-3-5-sonnet-20241022-v2:0"
_HAIKU_SUFFIX = "anthropic.claude-3-5-haiku-20241022-v1:0"

# audit-remediation package C1: Decimal literals (money-is-Decimal rule).
_SONNET_V2_PROMPT_USD_PER_TOKEN = Decimal("0.000003")
_SONNET_V2_COMPLETION_USD_PER_TOKEN = Decimal("0.000015")
_HAIKU_PROMPT_USD_PER_TOKEN = Decimal("0.0000008")
_HAIKU_COMPLETION_USD_PER_TOKEN = Decimal("0.000004")

BEDROCK_SEED_MODELS: list[CatalogModel] = [
    # --- Claude 3.5 Sonnet v2 --------------------------------------------------
    CatalogModel(
        id=f"us.{_SONNET_V2_SUFFIX}",
        name="Claude 3.5 Sonnet v2 (Bedrock, US)",
        context_length=200_000,
        prompt_usd_per_token=_SONNET_V2_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_SONNET_V2_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text,image",
        region="us",
    ),
    CatalogModel(
        id=f"eu.{_SONNET_V2_SUFFIX}",
        name="Claude 3.5 Sonnet v2 (Bedrock, EU)",
        context_length=200_000,
        prompt_usd_per_token=_SONNET_V2_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_SONNET_V2_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text,image",
        region="eu",
    ),
    CatalogModel(
        id=f"apac.{_SONNET_V2_SUFFIX}",
        name="Claude 3.5 Sonnet v2 (Bedrock, APAC)",
        context_length=200_000,
        prompt_usd_per_token=_SONNET_V2_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_SONNET_V2_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text,image",
        region="ap",
    ),
    # --- Claude 3.5 Haiku --------------------------------------------------
    CatalogModel(
        id=f"us.{_HAIKU_SUFFIX}",
        name="Claude 3.5 Haiku (Bedrock, US)",
        context_length=200_000,
        prompt_usd_per_token=_HAIKU_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_HAIKU_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text",
        region="us",
    ),
    CatalogModel(
        id=f"eu.{_HAIKU_SUFFIX}",
        name="Claude 3.5 Haiku (Bedrock, EU)",
        context_length=200_000,
        prompt_usd_per_token=_HAIKU_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_HAIKU_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text",
        region="eu",
    ),
    CatalogModel(
        id=f"apac.{_HAIKU_SUFFIX}",
        name="Claude 3.5 Haiku (Bedrock, APAC)",
        context_length=200_000,
        prompt_usd_per_token=_HAIKU_PROMPT_USD_PER_TOKEN,
        completion_usd_per_token=_HAIKU_COMPLETION_USD_PER_TOKEN,
        modality="chat",
        provider="bedrock",
        input_modalities="text",
        region="ap",
    ),
]

__all__ = ["BEDROCK_SEED_MODELS"]
