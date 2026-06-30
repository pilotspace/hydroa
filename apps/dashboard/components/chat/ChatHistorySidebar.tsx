"use client";

/**
 * components/chat/ChatHistorySidebar.tsx — v43 + chat-conversation-mgmt.
 *
 * Features:
 *   - List past conversations, New, resume, delete (v43)
 *   - Inline rename: click pencil → input, Enter commits, Escape cancels (chat-mgmt)
 *   - Fork / duplicate: client-side copy with "(copy)" suffix (chat-mgmt)
 *   - Export: JSON or markdown Blob download (chat-mgmt)
 *   - Search: client-side text filter on title (chat-mgmt)
 *
 * All persistence calls are best-effort: a failure shows a non-blocking error
 * state in the sidebar but never throws through the stream or drops an on-screen turn.
 *
 * WCAG-AA: real <button>s, aria-current on the active item, accessible names.
 */

import { useEffect, useRef, useState } from "react";
import { Copy, Download, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Loading, ErrorState } from "@/components/ui/states";
import {
  listConversations,
  getConversation,
  createConversation,
  appendMessage,
  renameConversation,
  deleteConversation,
  type ConversationSummary,
} from "@/lib/conversations";
import { cn } from "@/lib/cn";

export interface ChatHistorySidebarProps {
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** Increment to trigger a re-fetch of the conversation list. */
  refreshKey: number;
  /** When true the "New" button is disabled (a stream is in progress). */
  streaming: boolean;
  /** Optional callback when a rename completes (e.g. to update the top bar title). */
  onRenameComplete?: (id: string, title: string) => void;
}

type SidebarState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; conversations: ConversationSummary[] };

/** Download a Blob as a file. */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    document.body.removeChild(a);
  }, 100);
}

export function ChatHistorySidebar({
  activeId,
  onSelect,
  onNew,
  refreshKey,
  streaming,
  onRenameComplete,
}: ChatHistorySidebarProps) {
  const [state, setState] = useState<SidebarState>({ kind: "loading" });
  // Internal tick incremented to trigger a re-fetch after delete/rename/fork.
  const [fetchTick, setFetchTick] = useState(0);

  // Search query (client-side filter)
  const [searchQuery, setSearchQuery] = useState("");

  // Inline rename state: null = not renaming, string = the id being renamed
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;

    listConversations()
      .then((result) => {
        if (!cancelled) setState({ kind: "ready", conversations: result.data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load conversations";
          setState({ kind: "error", message: msg });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [refreshKey, fetchTick]);

  // Focus the rename input when it appears
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  function handleRetry() {
    setState({ kind: "loading" });
    setFetchTick((t) => t + 1);
  }

  function handleDelete(id: string, label: string) {
    deleteConversation(id)
      .then(() => {
        setState({ kind: "loading" });
        setFetchTick((t) => t + 1);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : `Failed to delete ${label}`;
        setState({ kind: "error", message: msg });
      });
  }

  function startRename(id: string, currentTitle: string) {
    setRenamingId(id);
    setRenameValue(currentTitle);
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameValue("");
  }

  function commitRename(id: string) {
    const trimmed = renameValue.trim();
    if (!trimmed) {
      cancelRename();
      return;
    }
    setRenamingId(null);
    setRenameValue("");
    renameConversation(id, trimmed)
      .then((updated) => {
        onRenameComplete?.(id, updated.title ?? trimmed);
        setFetchTick((t) => t + 1);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to rename conversation";
        setState({ kind: "error", message: msg });
      });
  }

  function handleFork(id: string, title: string) {
    getConversation(id)
      .then(async (detail) => {
        const forked = await createConversation(`${title} (copy)`);
        for (const msg of detail.messages) {
          await appendMessage(forked.id, msg.role, msg.content);
        }
        setFetchTick((t) => t + 1);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to fork conversation";
        setState({ kind: "error", message: msg });
      });
  }

  function handleExportJSON(id: string, title: string) {
    getConversation(id)
      .then((detail) => {
        const blob = new Blob([JSON.stringify(detail, null, 2)], {
          type: "application/json",
        });
        downloadBlob(blob, `${title || "conversation"}.json`);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to export conversation";
        setState({ kind: "error", message: msg });
      });
  }

  function handleExportMarkdown(id: string, title: string) {
    getConversation(id)
      .then((detail) => {
        const lines = [
          `# ${detail.title ?? "Conversation"}`,
          "",
          ...detail.messages.map((m) => `**${m.role}**: ${m.content}`),
        ];
        const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
        downloadBlob(blob, `${title || "conversation"}.md`);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to export conversation";
        setState({ kind: "error", message: msg });
      });
  }

  // Client-side filter
  const filteredConversations =
    state.kind === "ready"
      ? state.conversations.filter((c) => {
          if (!searchQuery) return true;
          const q = searchQuery.toLowerCase();
          const t = (c.title ?? "").toLowerCase();
          return t.includes(q);
        })
      : [];

  return (
    <aside
      className="hidden h-full min-h-0 w-64 flex-shrink-0 flex-col border-r border-border bg-background md:flex"
      aria-label="Conversation history"
    >
      {/* Header with New button */}
      <div className="flex items-center justify-between border-b border-border px-3 py-3">
        <span className="text-sm font-semibold text-foreground">History</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onNew}
          disabled={streaming}
          aria-label="New conversation"
        >
          <Plus className="size-4" aria-hidden="true" />
          New
        </Button>
      </div>

      {/* Search box */}
      <div className="border-b border-border px-3 py-2">
        <div className="relative">
          <Search
            className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            type="search"
            role="searchbox"
            aria-label="Search conversations"
            placeholder="Search…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn(
              "w-full rounded-md border border-border bg-transparent py-1.5 pl-7 pr-7 text-xs",
              "placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring",
            )}
          />
          {searchQuery && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {state.kind === "loading" && (
          <Loading label="Loading conversations…" className="px-2 py-4" />
        )}

        {state.kind === "error" && (
          <ErrorState
            title="Could not load history"
            description={state.message}
            onRetry={handleRetry}
            className="text-xs"
          />
        )}

        {state.kind === "ready" && filteredConversations.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">
            {searchQuery ? "No matching conversations." : "No conversations yet."}
          </p>
        )}

        {state.kind === "ready" &&
          filteredConversations.map((conv) => {
            const isActive = conv.id === activeId;
            const label = conv.title ?? "Untitled";
            const isRenaming = renamingId === conv.id;

            return (
              <div
                key={conv.id}
                className={cn(
                  "group flex items-center gap-1 rounded-md",
                  isActive && "bg-accent",
                )}
              >
                {isRenaming ? (
                  /* Inline rename input */
                  <input
                    ref={renameInputRef}
                    type="text"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename(conv.id);
                      if (e.key === "Escape") cancelRename();
                    }}
                    onBlur={() => commitRename(conv.id)}
                    aria-label={`Rename ${label}`}
                    className={cn(
                      "min-w-0 flex-1 rounded-md border border-ring bg-background px-2 py-1 text-sm",
                      "focus:outline-none focus:ring-2 focus:ring-ring",
                    )}
                  />
                ) : (
                  /* Select button — full-width, left-aligned title */
                  <button
                    type="button"
                    onClick={() => onSelect(conv.id)}
                    aria-current={isActive ? "true" : undefined}
                    className={cn(
                      "min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      isActive
                        ? "font-medium text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {label}
                  </button>
                )}

                {/* Action buttons — shown on hover */}
                {!isRenaming && (
                  <div className="flex flex-shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    {/* Rename */}
                    <button
                      type="button"
                      onClick={() => startRename(conv.id, label)}
                      aria-label={`Rename ${label}`}
                      className={cn(
                        "rounded p-1 text-muted-foreground",
                        "hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      )}
                    >
                      <Pencil className="size-3" aria-hidden="true" />
                    </button>

                    {/* Fork / duplicate */}
                    <button
                      type="button"
                      onClick={() => handleFork(conv.id, label)}
                      aria-label={`Duplicate ${label}`}
                      className={cn(
                        "rounded p-1 text-muted-foreground",
                        "hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      )}
                    >
                      <Copy className="size-3" aria-hidden="true" />
                    </button>

                    {/* Export — split into JSON and MD via a simple dropdown-free pair */}
                    <button
                      type="button"
                      onClick={() => handleExportJSON(conv.id, label)}
                      aria-label={`Export ${label} as JSON`}
                      title="Export as JSON"
                      className={cn(
                        "rounded p-1 text-muted-foreground",
                        "hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      )}
                    >
                      <Download className="size-3" aria-hidden="true" />
                    </button>

                    {/* Delete */}
                    <button
                      type="button"
                      onClick={() => handleDelete(conv.id, label)}
                      aria-label={`Delete ${label}`}
                      className={cn(
                        "rounded p-1 text-muted-foreground",
                        "hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      )}
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </aside>
  );
}
