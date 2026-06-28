"use client";

/**
 * components/chat/ChatHistorySidebar.tsx — v43 conversation history sidebar.
 *
 * Lists past conversations, supports "New" (clears workspace), resuming a
 * thread (onSelect), and deleting a conversation. All persistence calls are
 * best-effort: a failure shows a non-blocking error state in the sidebar but
 * never throws through the stream or drops an on-screen turn.
 *
 * WCAG-AA: real <button>s, aria-current on the active item, accessible names.
 */

import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Loading, ErrorState } from "@/components/ui/states";
import {
  listConversations,
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
}

type SidebarState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; conversations: ConversationSummary[] };

export function ChatHistorySidebar({
  activeId,
  onSelect,
  onNew,
  refreshKey,
  streaming,
}: ChatHistorySidebarProps) {
  const [state, setState] = useState<SidebarState>({ kind: "loading" });
  // Internal tick incremented to trigger a re-fetch after delete or retry.
  const [fetchTick, setFetchTick] = useState(0);

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

        {state.kind === "ready" && state.conversations.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">No conversations yet.</p>
        )}

        {state.kind === "ready" &&
          state.conversations.map((conv) => {
            const isActive = conv.id === activeId;
            const label = conv.title ?? "Untitled";
            return (
              <div
                key={conv.id}
                className={cn(
                  "group flex items-center gap-1 rounded-md",
                  isActive && "bg-accent",
                )}
              >
                {/* Select button — full-width, left-aligned title */}
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

                {/* Delete button — shown on hover (opacity transition) */}
                <button
                  type="button"
                  onClick={() => handleDelete(conv.id, label)}
                  aria-label={`Delete ${label}`}
                  className={cn(
                    "flex-shrink-0 rounded p-1 text-muted-foreground",
                    "opacity-0 transition-opacity group-hover:opacity-100",
                    "hover:text-destructive focus-visible:opacity-100",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                </button>
              </div>
            );
          })}
      </div>
    </aside>
  );
}
