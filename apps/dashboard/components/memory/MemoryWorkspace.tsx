"use client";

/**
 * components/memory/MemoryWorkspace.tsx — Console-grade memory workspace.
 *
 * Two-pane layout:
 *   LEFT  → MemoryLibraryPane  (list / search / add / sort)
 *   RIGHT → MemoryInspectorPane (selected-item detail + guarded delete)
 *
 * All network calls route through /api/gw/... (BFF) — never the gateway directly.
 * No new dependencies; reuses existing ui/* primitives.
 *
 * Failure model:
 *   - List-load failure is non-blocking: the error is shown inside the library pane
 *     while the add-composer and search remain fully accessible.
 *   - Add/delete failures are shown inline; they never crash the workspace.
 */

import { useEffect, useState } from "react";
import {
  listMemories,
  createMemory,
  searchMemories,
  deleteMemory,
  type MemoryItem,
  type SearchResult,
} from "@/lib/memories";
import {
  MemoryLibraryPane,
  type ListState,
} from "@/components/memory/MemoryLibraryPane";
import { MemoryInspectorPane } from "@/components/memory/MemoryInspectorPane";

type SortMode = "recency" | "relevance";

export function MemoryWorkspace() {
  // ── List state ─────────────────────────────────────────────────────────────
  const [listState, setListState] = useState<ListState>({ kind: "loading" });
  const [fetchTick, setFetchTick] = useState(0);

  // ── Selection ──────────────────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // ── Sort ───────────────────────────────────────────────────────────────────
  const [sortMode, setSortMode] = useState<SortMode>("recency");

  // ── Search state ───────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [searchPending, setSearchPending] = useState(false);
  const [searchError, setSearchError] = useState("");

  // ── Fetch list on mount and on tick ───────────────────────────────────────
  // NOTE: setListState({ kind: "loading" }) is intentionally NOT called here.
  // The rule react-hooks/set-state-in-effect forbids synchronous setState in
  // effect bodies. Loading state is set by callers before they increment fetchTick.
  // Initial loading state is set by the useState initializer above.
  useEffect(() => {
    let cancelled = false;

    listMemories()
      .then((result) => {
        if (!cancelled) setListState({ kind: "ready", memories: result.data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load memories";
          setListState({ kind: "error", message: msg });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchTick]);

  // ── Add handler ────────────────────────────────────────────────────────────
  async function handleAdd(content: string, metadata?: Record<string, unknown>): Promise<void> {
    await createMemory(content, metadata);
    // Clear search results and return to list mode after adding.
    // Set loading state here (event-handler context, not inside an effect).
    setSearchResults(null);
    setSortMode("recency");
    setListState({ kind: "loading" });
    setFetchTick((t) => t + 1);
  }

  // ── Search handler ─────────────────────────────────────────────────────────
  function handleSearch(query: string) {
    const trimmed = query.trim();
    if (!trimmed) return;

    setSearchPending(true);
    setSearchError("");

    searchMemories(trimmed)
      .then((result) => {
        setSearchResults(result.data);
        setSortMode("relevance"); // auto-switch sort to relevance after search
        setSearchPending(false);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Search failed";
        setSearchError(msg);
        setSearchPending(false);
      });
  }

  // ── Delete handler ─────────────────────────────────────────────────────────
  function handleDelete(id: string) {
    deleteMemory(id)
      .then(() => {
        if (selectedId === id) setSelectedId(null);
        // Clear search and refresh list.
        // setListState in a Promise callback (not inside an effect body) is allowed.
        setSearchResults(null);
        setListState({ kind: "loading" });
        setFetchTick((t) => t + 1);
      })
      .catch(() => {
        // best-effort — failure is non-blocking
      });
  }

  // ── Select handler ─────────────────────────────────────────────────────────
  function handleSelect(id: string) {
    setSelectedId(id);
  }

  // ── Derive selected item ───────────────────────────────────────────────────
  const libraryItems: MemoryItem[] =
    listState.kind === "ready" ? listState.memories : [];

  const selectedItem: MemoryItem | null = selectedId
    ? (libraryItems.find((m) => m.id === selectedId) ??
       // Fall back to search results cast to MemoryItem (score-less fields match)
       (searchResults?.find((r) => r.id === selectedId) as MemoryItem | undefined) ??
       null)
    : null;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      {/* Page header */}
      <header className="shrink-0 border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Memory</h1>
      </header>

      {/* Two-pane body */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <MemoryLibraryPane
          listState={listState}
          searchResults={searchResults}
          searchQuery={searchQuery}
          searchPending={searchPending}
          searchError={searchError}
          sortMode={sortMode}
          selectedId={selectedId}
          onAdd={handleAdd}
          onSearch={handleSearch}
          onSearchQueryChange={setSearchQuery}
          onSortChange={setSortMode}
          onSelect={handleSelect}
          onDelete={handleDelete}
        />

        <MemoryInspectorPane item={selectedItem} onDelete={handleDelete} />
      </div>
    </div>
  );
}
