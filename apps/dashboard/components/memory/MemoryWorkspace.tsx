"use client";

/**
 * components/memory/MemoryWorkspace.tsx — v44 memory workspace.
 *
 * Surfaces:
 *   - List: on mount calls listMemories(), newest-first as returned.
 *   - Add form: textarea + submit (disabled when content empty/whitespace).
 *     On submit → createMemory(content) → clear input → refresh list.
 *     Best-effort: failure sets a non-blocking error, never throws.
 *   - Search: input + search button (disabled when query empty).
 *     On submit → searchMemories(query) → render ranked results section.
 *     null score renders as "text match" fallback — never a fabricated number.
 *   - Delete: per-item button → deleteMemory(id) → refresh list.
 *
 * All network calls go to /api/gw/... (the BFF) — never the gateway directly.
 * No new dependencies; uses the v13/v23 ui primitives.
 */

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Empty, ErrorState, Loading } from "@/components/ui/states";
import {
  listMemories,
  createMemory,
  searchMemories,
  deleteMemory,
  type MemoryItem,
  type SearchResult,
} from "@/lib/memories";

// ── MemoryList ────────────────────────────────────────────────────────────────

interface MemoryListProps {
  items: MemoryItem[];
  onDelete: (id: string) => void;
}

function MemoryList({ items, onDelete }: MemoryListProps) {
  if (items.length === 0) {
    return (
      <Empty
        title="No memories yet"
        description="Add a memory using the form above."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-2" aria-label="Memory list">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="flex-1 text-sm text-foreground">{item.content}</p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Delete memory: ${item.content}`}
            onClick={() => onDelete(item.id)}
            className="shrink-0 text-destructive hover:text-destructive"
          >
            Delete
          </Button>
        </li>
      ))}
    </ul>
  );
}

// ── SearchResults ─────────────────────────────────────────────────────────────

interface SearchResultsProps {
  results: SearchResult[];
}

function SearchResults({ results }: SearchResultsProps) {
  if (results.length === 0) {
    return (
      <Empty
        title="No results"
        description="No memories matched your search."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-2" aria-label="Search results">
      {results.map((item) => (
        <li
          key={item.id}
          className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="flex-1 text-sm text-foreground">{item.content}</p>
          <span className="shrink-0 text-xs text-muted-foreground" aria-label="Relevance score">
            {item.score !== null ? item.score.toFixed(2) : "text match"}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ── List state ────────────────────────────────────────────────────────────────

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; memories: MemoryItem[] };

// ── MemoryWorkspace ───────────────────────────────────────────────────────────

export function MemoryWorkspace() {
  // ── list state ──────────────────────────────────────────────────────────
  const [listState, setListState] = useState<ListState>({ kind: "loading" });
  // Increment to trigger a re-fetch after add, delete, or retry.
  const [fetchTick, setFetchTick] = useState(0);

  // ── add form state ───────────────────────────────────────────────────────
  const [addContent, setAddContent] = useState("");
  const [addPending, setAddPending] = useState(false);
  const [addError, setAddError] = useState<string>("");

  // ── search state ─────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [searchPending, setSearchPending] = useState(false);
  const [searchError, setSearchError] = useState<string>("");

  // ── fetch on mount and on tick ────────────────────────────────────────────
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

  // ── add handler ───────────────────────────────────────────────────────────

  function handleAdd(e: FormEvent) {
    e.preventDefault();
    const trimmed = addContent.trim();
    if (!trimmed) return; // guard: empty/whitespace → no-op

    setAddPending(true);
    setAddError("");

    createMemory(trimmed)
      .then(() => {
        setAddContent("");
        setAddPending(false);
        setListState({ kind: "loading" });
        setFetchTick((t) => t + 1);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to add memory";
        setAddError(msg);
        setAddPending(false);
      });
  }

  // ── search handler ────────────────────────────────────────────────────────

  function handleSearch(e: FormEvent) {
    e.preventDefault();
    const trimmed = searchQuery.trim();
    if (!trimmed) return; // guard

    setSearchPending(true);
    setSearchError("");
    setSearchResults(null);

    searchMemories(trimmed)
      .then((result) => {
        setSearchResults(result.data);
        setSearchPending(false);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Search failed";
        setSearchError(msg);
        setSearchPending(false);
      });
  }

  // ── delete handler ────────────────────────────────────────────────────────

  function handleDelete(id: string) {
    deleteMemory(id)
      .then(() => {
        setListState({ kind: "loading" });
        setFetchTick((t) => t + 1);
      })
      .catch(() => {
        // best-effort — failure is non-blocking
      });
  }

  // ── derived ───────────────────────────────────────────────────────────────

  const isAddDisabled = addPending || !addContent.trim();
  const isSearchDisabled = searchPending || !searchQuery.trim();

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Memory</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-6">

          {/* ── Add memory form ─────────────────────────────────────────── */}
          <section
            aria-labelledby="add-memory-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="add-memory-heading" className="text-base font-semibold text-foreground">
              Add memory
            </h2>

            <form onSubmit={handleAdd} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="memory-content" className="text-sm font-medium text-foreground">
                  Content
                </label>
                <Textarea
                  id="memory-content"
                  aria-label="Memory content"
                  value={addContent}
                  onChange={(e) => setAddContent(e.target.value)}
                  placeholder="Enter memory content…"
                  rows={3}
                />
              </div>

              <Button
                type="submit"
                disabled={isAddDisabled}
                aria-disabled={isAddDisabled}
              >
                {addPending ? "Adding…" : "Add memory"}
              </Button>
            </form>

            {addError && (
              <ErrorState title={addError} />
            )}
          </section>

          {/* ── Search form ──────────────────────────────────────────────── */}
          <section
            aria-labelledby="search-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="search-heading" className="text-base font-semibold text-foreground">
              Search memories
            </h2>

            <form onSubmit={handleSearch} className="flex gap-2">
              <div className="flex flex-1 flex-col gap-1.5">
                <label htmlFor="search-query" className="sr-only">
                  Search memories
                </label>
                <Input
                  id="search-query"
                  type="text"
                  aria-label="Search memories"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search memories…"
                />
              </div>
              <Button
                type="submit"
                disabled={isSearchDisabled}
                aria-disabled={isSearchDisabled}
              >
                {searchPending ? "Searching…" : "Search"}
              </Button>
            </form>

            {searchError && (
              <ErrorState title={searchError} />
            )}

            {searchResults !== null && (
              <div aria-live="polite" aria-label="Search results">
                <p className="mb-2 text-sm text-muted-foreground">
                  {searchResults.length} result{searchResults.length !== 1 ? "s" : ""} found
                </p>
                <SearchResults results={searchResults} />
              </div>
            )}
          </section>

          {/* ── Memory list ──────────────────────────────────────────────── */}
          <section
            aria-labelledby="list-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="list-heading" className="text-base font-semibold text-foreground">
              Saved memories
            </h2>

            <div aria-live="polite">
              {listState.kind === "loading" && <Loading label="Loading memories…" />}
              {listState.kind === "error" && (
                <ErrorState title={listState.message} />
              )}
              {listState.kind === "ready" && (
                <MemoryList items={listState.memories} onDelete={handleDelete} />
              )}
            </div>
          </section>

        </div>
      </div>
    </div>
  );
}
