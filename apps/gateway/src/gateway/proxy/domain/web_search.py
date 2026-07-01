"""Web-search grounding seam — web-search TASK.md §3.

Canonical per-provider native grounding tool shapes and citation normalizer.
No IO; no provider logic — only the factory and the response normalizer.

Design: mirrors the style of tool_translation.py (FROZEN sibling).
  - DEFAULT-OFF + FAIL-SAFE invariant: when the knob is off OR no web_search flag,
    the outgoing upstream body must be byte-identical to today (no injected tool,
    no leftover flag).
  - Non-grounding providers (bedrock/azure/unknown) -> None -> nothing injected,
    never raise.
  - Streaming citation surfacing is OUT OF SCOPE (SSE steppers are not touched).

Security: no secrets, no tenant data, no keys ever enter this module.
"""

from __future__ import annotations

from typing import Any

WEB_SEARCH_FLAG = "web_search"

# ── Per-provider native grounding tool shapes ─────────────────────────────────

# OpenAI / OpenRouter: the web_search_preview built-in tool.
_WEB_SEARCH_PREVIEW: dict[str, Any] = {"type": "web_search_preview"}

# Anthropic: the web_search_20250305 built-in tool (requires a name field).
_ANTHROPIC_WEB_SEARCH: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}

# Google Gemini: the googleSearch grounding source (sibling to functionDeclarations,
# NOT nested inside it).
_GOOGLE_SEARCH: dict[str, Any] = {"googleSearch": {}}


def native_web_search_tool(provider: str) -> dict[str, Any] | None:
    """Return the provider-native grounding tool shape, or None for unsupported providers.

    Supported providers and their native grounding tool:
      "openai"     → {"type": "web_search_preview"}
      "openrouter" → {"type": "web_search_preview"}
      "anthropic"  → {"type": "web_search_20250305", "name": "web_search"}
      "google"     → {"googleSearch": {}}
    Unsupported (bedrock, azure, or any unknown string) → None.

    Returns a fresh copy so callers can safely mutate it.
    Never raises.
    """
    if provider in ("openai", "openrouter"):
        return dict(_WEB_SEARCH_PREVIEW)
    if provider == "anthropic":
        return dict(_ANTHROPIC_WEB_SEARCH)
    if provider == "google":
        return dict(_GOOGLE_SEARCH)
    # bedrock, azure, unknown → no native grounding tool
    return None


# ── Grounding citation normalizer ─────────────────────────────────────────────

# Normalized grounding item shape: {"title"?: str, "url"?: str, "snippet"?: str}
# Include whatever the provider gives; omit missing keys.


def _normalize_anthropic_grounding(
    content_blocks: list[Any],  # Any: upstream JSON is untrusted; isinstance guards are defensive
) -> list[dict[str, Any]] | None:
    """Extract web_search_result blocks from an Anthropic response and normalize them.

    Returns a list of normalized grounding items if any web_search_result blocks are
    present, otherwise returns None (no fabrication).

    Normalized item: {"url"?: str, "title"?: str, "snippet"?: str}
    """
    items: list[dict[str, Any]] = []
    for block in content_blocks:
        if not isinstance(block, dict):  # malformed upstream block — skip, never crash
            continue
        if block.get("type") != "web_search_result":
            continue
        item: dict[str, Any] = {}
        url = block.get("url")
        if isinstance(url, str) and url:
            item["url"] = url
        title = block.get("title")
        if isinstance(title, str) and title:
            item["title"] = title
        # Anthropic may also supply encrypted_content/page_age — we surface snippet
        # from encrypted_content only when it's a plain-text snippet (rare, skip encrypted).
        snippet = block.get("snippet")
        if isinstance(snippet, str) and snippet:
            item["snippet"] = snippet
        if item:  # only add non-empty items
            items.append(item)
    return items if items else None


def _normalize_gemini_grounding(
    grounding_metadata: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Extract grounding chunks from a Gemini groundingMetadata dict and normalize them.

    Returns a list of normalized grounding items if any web chunks are present,
    otherwise returns None (no fabrication).

    Normalized item: {"url"?: str, "title"?: str}
    """
    # `.get("groundingChunks", [])` is NOT enough: Gemini may send the key present
    # with a null value, and `.get`'s default only fires when the key is ABSENT.
    # `or []` collapses both absent and null (and any falsy) to an empty list so the
    # loop below never iterates None (would 500 the non-stream translation).
    chunks_raw = grounding_metadata.get("groundingChunks") or []
    # list[Any]: upstream JSON is untrusted; isinstance(chunk, dict) guards are defensive
    chunks: list[Any] = chunks_raw if isinstance(chunks_raw, list) else []
    items: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):  # malformed chunk — skip, never crash
            continue
        web = chunk.get("web")
        if not isinstance(web, dict):
            continue
        item: dict[str, Any] = {}
        uri = web.get("uri")
        if isinstance(uri, str) and uri:
            item["url"] = uri
        title = web.get("title")
        if isinstance(title, str) and title:
            item["title"] = title
        if item:
            items.append(item)
    return items if items else None


__all__ = [
    "WEB_SEARCH_FLAG",
    "_normalize_anthropic_grounding",
    "_normalize_gemini_grounding",
    "native_web_search_tool",
]
