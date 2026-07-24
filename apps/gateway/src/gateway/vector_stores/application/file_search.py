"""The shared file_search grounding seam (file-search-tool PLAN.md §3 M1/M2/M5/M7).

`FileSearchGrounder.ground()` is the ONE pre-dispatch chokepoint both ingresses
(/v1/responses and /v1/chat/completions) reach via `CompletionUseCase`. For a request
carrying a `file_search` tool it:

  1. validates `vector_store_ids` (empty/missing -> 422, BEFORE any retrieval/meter);
  2. loads the store tenant-scoped for its OWN `embedding_model` (absent / cross-tenant
     -> 404 `vector_store_not_found`, a uniform no-leak reject raised BEFORE metering);
  3. embeds the last user message via the PER-TENANT breaker (breaker-open / timeout after
     the one idempotent retry -> 503 `upstream_unavailable`, fail-closed, NO bill);
  4. runs the top-k cosine search (clamped [1,50], default 20), injects the retrieved
     chunk text into `messages` as grounding (M2), and STRIPS `file_search` from the body
     so the provider never receives a hosted tool it cannot service (M5);
  5. fires the injected `meter` callback EXACTLY ONCE — even for a 0-hit valid search
     (M3 parity) — never on a 404/422/503 reject.

A request with NO `file_search` tool returns immediately (`False`) — ZERO plumbing,
byte-identical default path (M4). Retrieval+meter run EXACTLY ONCE here, before any
breaker/dispatch retry loop in the caller (M7).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.vector_stores.infrastructure.file_repository import VectorStoreFileRepository
from gateway.vector_stores.infrastructure.orm import VectorStoreRow
from gateway.vector_stores.wire_id import parse_wire_id

_FILE_SEARCH_TYPE = "file_search"
#: The billing-only synthetic catalog model per_query prices against (migration-seeded).
FILE_SEARCH_MODEL = "file_search"

# Tin-frozen OpenAI-parity top-k bound (PLAN.md §3): absent -> 20; provided -> clamp[1,50].
_DEFAULT_TOP_K = 20
_MIN_TOP_K = 1
_MAX_TOP_K = 50


class FileSearchError(Exception):
    """A file_search reject carrying its uniform wire code + HTTP status.

    The caller (CompletionUseCase) maps this onto a ProblemError — the codes/statuses
    are the FROZEN §3 Contract values, never re-derived here.
    """

    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class MissingVectorStoreIds(FileSearchError):
    def __init__(self) -> None:
        super().__init__(
            "file_search_vector_store_ids_required",
            "file_search requires a non-empty vector_store_ids array",
            status=422,
        )


class VectorStoreNotFound(FileSearchError):
    def __init__(self) -> None:
        # Uniform reject for unknown AND cross-tenant stores — zero enumeration oracle.
        super().__init__("vector_store_not_found", "vector store not found", status=404)


class UpstreamUnavailable(FileSearchError):
    def __init__(self) -> None:
        super().__init__(
            "upstream_unavailable",
            "query-embedding upstream unavailable; retrieval failed closed",
            status=503,
        )


class _EmbeddingClient(Protocol):
    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]: ...


def _clamp_top_k(max_num_results: Any) -> int:
    """PLAN.md §3: absent/None -> 20; else clamp the int to [1, 50]. Non-int -> default."""
    if max_num_results is None or isinstance(max_num_results, bool):
        return _DEFAULT_TOP_K
    if not isinstance(max_num_results, int):
        return _DEFAULT_TOP_K
    return max(_MIN_TOP_K, min(_MAX_TOP_K, max_num_results))


def _extract_file_search_tool(body: dict[str, Any]) -> dict[str, Any] | None:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == _FILE_SEARCH_TYPE:
            return tool
    return None


def _last_user_text(body: dict[str, Any]) -> str:
    """The text of the LAST user message — the retrieval query (M1)."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") in (None, "text", "input_text")
                ]
                return " ".join(p for p in parts if p)
    return ""


def _strip_file_search(body: dict[str, Any]) -> None:
    """M5: remove EVERY file_search tool from the outbound body; drop an emptied tools list."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return
    remaining = [
        tool
        for tool in tools
        if not (isinstance(tool, dict) and tool.get("type") == _FILE_SEARCH_TYPE)
    ]
    if remaining:
        body["tools"] = remaining
    else:
        body.pop("tools", None)


def _inject_grounding(body: dict[str, Any], chunk_texts: list[str]) -> None:
    """M2: prepend a system message carrying the retrieved chunk text as grounding context.

    A 0-hit search injects an explicit "no matching context" note (still grounded on the
    empty result set) so the model does not silently answer as if no store was attached.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return
    if chunk_texts:
        joined = "\n\n".join(f"[{i + 1}] {text}" for i, text in enumerate(chunk_texts))
        content = (
            "Use the following retrieved context to answer the user's question. "
            "If it is not relevant, say so.\n\n" + joined
        )
    else:
        content = (
            "No matching context was retrieved from the attached vector store for this query."
        )
    messages.insert(0, {"role": "system", "content": content})


class FileSearchGrounder:
    """Pre-dispatch retrieval + grounding + strip + meter for the file_search tool."""

    def __init__(
        self,
        *,
        embedding_client: _EmbeddingClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._embedding_client = embedding_client
        self._session_factory = session_factory

    async def ground(
        self,
        *,
        body: dict[str, Any],
        tenant_id: uuid.UUID,
        meter: Callable[[], Awaitable[None]],
    ) -> bool:
        """Ground `body` in place; return True iff a file_search tool was serviced.

        Raises MissingVectorStoreIds (422) / VectorStoreNotFound (404) /
        UpstreamUnavailable (503) — the caller maps these to problem+json. The `meter`
        callback fires exactly once, only on a fully-successful invocation.
        """
        tool = _extract_file_search_tool(body)
        if tool is None:
            return False  # M4 default path — ZERO new plumbing.

        ids = tool.get("vector_store_ids")
        if not isinstance(ids, list) or len(ids) == 0:
            raise MissingVectorStoreIds()

        first = ids[0]
        store_id = parse_wire_id(first) if isinstance(first, str) else None
        if store_id is None:
            raise VectorStoreNotFound()

        top_k = _clamp_top_k(tool.get("max_num_results"))

        # (1) Load the store tenant-scoped for its OWN embedding_model — a cross-tenant or
        #     unknown store is indistinguishable from absent (404, raised before metering).
        async with self._session_factory() as session:
            store_row = (
                await session.execute(
                    select(VectorStoreRow).where(
                        VectorStoreRow.id == store_id,
                        VectorStoreRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        if store_row is None:
            raise VectorStoreNotFound()

        # (2) Embed the query via the PER-TENANT breaker — fail closed on breaker-open/timeout.
        try:
            vectors, _usage = await self._embedding_client.embed(
                tenant_id, store_row.embedding_model, [_last_user_text(body)]
            )
        except (UpstreamUnavailableError, CircuitOpenError) as exc:
            raise UpstreamUnavailable() from exc
        query_embedding = vectors[0]

        # (3) Top-k cosine search, tenant + store scoped ([] cross-tenant -> still valid: 0 hits).
        async with self._session_factory() as session:
            repo = VectorStoreFileRepository(session)
            chunks = await repo.search_chunks(
                tenant_id=tenant_id,
                store_id=store_id,
                query_embedding=query_embedding,
                top_k=top_k,
            )

        # (4) Ground (M2) + strip file_search from the outbound body (M5).
        _inject_grounding(body, [chunk.content for chunk in chunks])
        _strip_file_search(body)

        # (5) Meter EXACTLY ONE per_query (M3) — after success, incl. a 0-hit search.
        await meter()
        return True
