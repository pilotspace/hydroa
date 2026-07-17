"use client";

/**
 * components/memory/MemoryLibraryPane.tsx — left panel of the memory workspace.
 *
 * Features:
 *   - Add composer (MemoryAddComposer)
 *   - Search input + button (disabled when query is empty)
 *   - Sort toggle: Recency (default) / Relevance
 *   - Result count ("N memories")
 *   - ARIA listbox (role="listbox") with keyboard navigation (ArrowUp/ArrowDown/Enter)
 *   - Per-item: content snippet, created_at, embedding-status indicator, delete button
 *
 * WCAG 2.2 AA:
 *   - role="listbox" + role="option" + aria-selected
 *   - Keyboard: ArrowDown/ArrowUp move selection; Enter = confirm
 *   - Embedding icons are aria-hidden; title attribute gives accessible label
 *   - Decorative icons aria-hidden
 */

import { useRef, type FormEvent, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loading, ErrorState, Empty } from "@/components/ui/states";
import { MemoryAddComposer } from "@/components/memory/MemoryAddComposer";
import { MemoryScoreBar } from "@/components/memory/MemoryScoreBar";
import type { MemoryItem, SearchResult } from "@/lib/memories";

// ── Shared display item ────────────────────────────────────────────────────────

export interface DisplayItem {
  id: string;
  content: string;
  created_at: string;
  has_embedding?: boolean;
  score?: number | null;
  metadata?: Record<string, unknown>;
}

function toDisplayItem(src: MemoryItem | SearchResult): DisplayItem {
  if ("score" in src) {
    return { id: src.id, content: src.content, created_at: src.created_at, score: src.score };
  }
  return src as DisplayItem;
}

// ── LibraryItem ───────────────────────────────────────────────────────────────

interface LibraryItemProps {
  item: DisplayItem;
  isSelected: boolean;
  isSearchMode: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

function LibraryItem({ item, isSelected, isSearchMode, onSelect, onDelete }: LibraryItemProps) {
  function handleDeleteClick(e: React.MouseEvent) {
    e.stopPropagation();
    onDelete(item.id);
  }

  return (
    <li
      role="option"
      aria-selected={isSelected}
      aria-label={item.content}
      onClick={() => onSelect(item.id)}
      className={[
        "flex cursor-pointer flex-col gap-1.5 border-b border-border px-4 py-3",
        "focus-within:ring-2 focus-within:ring-ring focus-within:ring-inset",
        "hover:bg-muted/50 transition-colors",
        isSelected ? "bg-muted/70" : "bg-transparent",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-2">
        {/* Embedding status icon */}
        <span
          title={item.has_embedding ? "Embedded" : "Not embedded"}
          aria-hidden="true"
          className={[
            "mt-0.5 size-2 shrink-0 rounded-full",
            item.has_embedding ? "bg-success" : "bg-muted-foreground/40",
          ].join(" ")}
        />
        <p className="flex-1 text-sm text-foreground line-clamp-2">{item.content}</p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleDeleteClick}
          aria-label={`Delete memory: ${item.content}`}
          className="shrink-0 h-6 px-2 text-xs text-destructive hover:text-destructive opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
          tabIndex={-1}
        >
          Delete
        </Button>
      </div>

      <div className="flex items-center gap-2 pl-4">
        <time
          dateTime={item.created_at}
          className="text-xs text-muted-foreground"
        >
          {formatDateShort(item.created_at)}
        </time>
        {isSearchMode && item.score !== undefined && (
          <MemoryScoreBar score={item.score ?? null} />
        )}
      </div>
    </li>
  );
}

function formatDateShort(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// ── SortToggle ─────────────────────────────────────────────────────────────────

type SortMode = "recency" | "relevance";

interface SortToggleProps {
  mode: SortMode;
  onChange: (m: SortMode) => void;
}

function SortToggle({ mode, onChange }: SortToggleProps) {
  return (
    <div role="group" aria-label="Sort order" className="flex items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-pressed={mode === "recency"}
        onClick={() => onChange("recency")}
        className={[
          "h-7 px-2.5 text-xs",
          mode === "recency"
            ? "bg-muted font-semibold text-foreground"
            : "text-muted-foreground hover:text-foreground",
        ].join(" ")}
      >
        Recency
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-pressed={mode === "relevance"}
        onClick={() => onChange("relevance")}
        className={[
          "h-7 px-2.5 text-xs",
          mode === "relevance"
            ? "bg-muted font-semibold text-foreground"
            : "text-muted-foreground hover:text-foreground",
        ].join(" ")}
      >
        Relevance
      </Button>
    </div>
  );
}

// ── MemoryLibraryPane ─────────────────────────────────────────────────────────

export type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; memories: MemoryItem[] };

interface MemoryLibraryPaneProps {
  listState: ListState;
  searchResults: SearchResult[] | null;
  searchQuery: string;
  searchPending: boolean;
  searchError: string;
  sortMode: SortMode;
  selectedId: string | null;
  onAdd: (content: string, metadata?: Record<string, unknown>) => Promise<void>;
  onSearch: (q: string) => void;
  onSearchQueryChange: (q: string) => void;
  onSortChange: (m: SortMode) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export function MemoryLibraryPane({
  listState,
  searchResults,
  searchQuery,
  searchPending,
  searchError,
  sortMode,
  selectedId,
  onAdd,
  onSearch,
  onSearchQueryChange,
  onSortChange,
  onSelect,
  onDelete,
}: MemoryLibraryPaneProps) {
  const listboxRef = useRef<HTMLUListElement>(null);

  // Determine what to display
  const isSearchMode = searchResults !== null;
  const displayItems: DisplayItem[] =
    isSearchMode
      ? searchResults.map(toDisplayItem)
      : listState.kind === "ready"
      ? listState.memories.map(toDisplayItem)
      : [];

  const resultCount = displayItems.length;

  // ── Keyboard navigation ──────────────────────────────────────────────────
  function handleListKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    if (displayItems.length === 0) return;
    const currentIdx = selectedId
      ? displayItems.findIndex((item) => item.id === selectedId)
      : -1;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const nextIdx = currentIdx === -1 ? 0 : Math.min(currentIdx + 1, displayItems.length - 1);
      onSelect(displayItems[nextIdx].id);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (currentIdx <= 0) return;
      onSelect(displayItems[currentIdx - 1].id);
    } else if (e.key === "Enter" && currentIdx !== -1) {
      e.preventDefault();
      onSelect(displayItems[currentIdx].id);
    }
  }

  // ── Search submit ────────────────────────────────────────────────────────
  function handleSearchSubmit(e: FormEvent) {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    onSearch(searchQuery);
  }

  const isSearchDisabled = searchPending || !searchQuery.trim();

  return (
    <section
      aria-label="Memory library"
      className="flex h-full min-h-0 w-80 shrink-0 flex-col border-r border-border bg-background"
    >
      {/* Add composer */}
      <div className="border-b border-border p-4">
        <MemoryAddComposer onAdd={onAdd} />
      </div>

      {/* Search */}
      <div className="border-b border-border p-3">
        <form onSubmit={handleSearchSubmit} className="flex gap-2">
          <div className="relative flex-1">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              aria-label="Search memories"
              value={searchQuery}
              onChange={(e) => onSearchQueryChange(e.target.value)}
              placeholder="Search memories…"
              className="pl-8 h-8 text-sm"
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={isSearchDisabled}
            aria-disabled={isSearchDisabled}
            className="h-8"
          >
            {searchPending ? "…" : "Search"}
          </Button>
        </form>
        {searchError && (
          <p role="alert" aria-live="polite" className="mt-2 text-xs text-destructive">
            {searchError}
          </p>
        )}
      </div>

      {/* Sort + count bar */}
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="text-xs text-muted-foreground">
          {resultCount} {resultCount === 1 ? "memory" : "memories"}
        </span>
        <SortToggle mode={sortMode} onChange={onSortChange} />
      </div>

      {/* List region */}
      <div className="flex-1 overflow-y-auto" aria-live="polite">
        {/* Loading (only in list mode, not search mode) */}
        {!isSearchMode && listState.kind === "loading" && (
          <div className="p-4">
            <Loading label="Loading memories…" />
          </div>
        )}

        {/* Error in list mode */}
        {!isSearchMode && listState.kind === "error" && (
          <div className="p-4">
            <ErrorState title={listState.message} />
          </div>
        )}

        {/* Empty state */}
        {listState.kind === "ready" && displayItems.length === 0 && (
          <div className="p-4">
            <Empty
              title={isSearchMode ? "No results" : "No memories yet"}
              description={
                isSearchMode
                  ? "No memories matched your search."
                  : "Add a memory using the form above."
              }
            />
          </div>
        )}

        {/* Listbox */}
        {displayItems.length > 0 && (
          <ul
            ref={listboxRef}
            role="listbox"
            aria-label="Memories"
            tabIndex={0}
            onKeyDown={handleListKeyDown}
            className="focus:outline-none"
          >
            {displayItems.map((item) => (
              <LibraryItem
                key={item.id}
                item={item}
                isSelected={selectedId === item.id}
                isSearchMode={isSearchMode}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
