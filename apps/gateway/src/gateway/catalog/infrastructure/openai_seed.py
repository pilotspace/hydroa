"""Fixed seed list of known OpenAI model entries — provider-seam TASK.md §3.

OPENAI_SEED_MODELS is a module-level constant: a small fixed set of OpenAI models
with their modality and provider fields set.  Used by a catalog sync command to
pre-populate the models table so downstream endpoint tasks (embeddings/images/audio)
can find active catalog rows without a full dynamic sync.

Full dynamic OpenAI model sync is a follow-up task.
Endpoint tasks may seed additional rows in their own test fixtures.

Placement: catalog/infrastructure/ per §5 BUILD file list.
"""

from __future__ import annotations

from gateway.catalog.domain.entities import CatalogModel

OPENAI_SEED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="text-embedding-3-small",
        name="text-embedding-3-small",
        context_length=8191,
        prompt_usd_per_token=0.00000002,
        completion_usd_per_token=0.0,
        modality="embedding",
        provider="openai",
        input_modalities="text",  # embedding models accept text input
    ),
    CatalogModel(
        id="text-embedding-3-large",
        name="text-embedding-3-large",
        context_length=8191,
        prompt_usd_per_token=0.00000013,
        completion_usd_per_token=0.0,
        modality="embedding",
        provider="openai",
        input_modalities="text",  # embedding models accept text input
    ),
    CatalogModel(
        id="dall-e-3",
        name="dall-e-3",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        modality="image",
        provider="openai",
        input_modalities="text",  # image-gen models accept text prompts (vision not in scope v55)
    ),
    CatalogModel(
        id="whisper-1",
        name="whisper-1",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        modality="audio_stt",
        provider="openai",
        input_modalities="audio",  # STT accepts audio input
    ),
    CatalogModel(
        id="tts-1",
        name="tts-1",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        modality="audio_tts",
        provider="openai",
        input_modalities="text",  # TTS accepts text and produces audio output
    ),
    CatalogModel(
        id="tts-1-hd",
        name="tts-1-hd",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        modality="audio_tts",
        provider="openai",
        input_modalities="text",  # TTS-HD accepts text and produces audio output
    ),
]

__all__ = ["OPENAI_SEED_MODELS"]
