"use client";

/**
 * components/memory/MemoryInspectorPane.tsx — detail view for a selected memory.
 *
 * Shows:
 *   - Full content (plain text)
 *   - Metadata as JSON (if present)
 *   - created_at (formatted)
 *   - Embedding status label
 *   - Delete button with confirmation guard (two-step: Delete → Confirm / Cancel)
 *
 * All text content is rendered as plain text; no raw HTML interpolation.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/states";
import type { MemoryItem } from "@/lib/memories";

interface MemoryInspectorPaneProps {
  item: MemoryItem | null;
  onDelete: (id: string) => void;
}

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export function MemoryInspectorPane({ item, onDelete }: MemoryInspectorPaneProps) {
  const [awaitingConfirm, setAwaitingConfirm] = useState(false);

  // Reset confirmation guard whenever the selected item changes
  if (!item && awaitingConfirm) setAwaitingConfirm(false);

  function handleDeleteClick() {
    setAwaitingConfirm(true);
  }

  function handleConfirmDelete() {
    if (!item) return;
    onDelete(item.id);
    setAwaitingConfirm(false);
  }

  function handleCancelDelete() {
    setAwaitingConfirm(false);
  }

  return (
    <section
      aria-label="Inspector"
      className="flex h-full min-h-0 flex-1 flex-col border-l border-border bg-background"
    >
      {!item ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <Empty
            title="No memory selected"
            description="Click a memory in the library to inspect its details."
          />
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-0 overflow-y-auto">
          {/* Inspector header */}
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-sm font-semibold text-foreground">Memory detail</h2>

            {/* Delete guard */}
            {!awaitingConfirm ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleDeleteClick}
                className="text-destructive hover:text-destructive"
                aria-label="Delete memory"
              >
                Delete
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Are you sure?</span>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={handleConfirmDelete}
                  aria-label="Confirm delete"
                >
                  Confirm
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleCancelDelete}
                >
                  Cancel
                </Button>
              </div>
            )}
          </div>

          {/* Content */}
          <div className="flex flex-col gap-5 p-5">
            <section aria-labelledby="inspector-content-label">
              <p
                id="inspector-content-label"
                className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground"
              >
                Content
              </p>
              <p className="text-sm text-foreground leading-relaxed">{item.content}</p>
            </section>

            {/* Metadata */}
            {item.metadata && Object.keys(item.metadata).length > 0 && (
              <section aria-labelledby="inspector-meta-label">
                <p
                  id="inspector-meta-label"
                  className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground"
                >
                  Metadata
                </p>
                <pre className="rounded-md bg-muted p-3 text-xs text-foreground overflow-x-auto">
                  {JSON.stringify(item.metadata, null, 2)}
                </pre>
              </section>
            )}

            {/* Created at */}
            <section aria-labelledby="inspector-date-label">
              <p
                id="inspector-date-label"
                className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground"
              >
                Created
              </p>
              <time
                dateTime={item.created_at}
                className="text-sm text-foreground"
              >
                {formatDate(item.created_at)}
              </time>
            </section>

            {/* Embedding status */}
            <section aria-labelledby="inspector-embed-label">
              <p
                id="inspector-embed-label"
                className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground"
              >
                Embedding
              </p>
              <span
                className={
                  item.has_embedding
                    ? "text-sm font-medium text-success-text"
                    : "text-sm text-muted-foreground"
                }
              >
                {item.has_embedding ? "Embedded" : "Not embedded"}
              </span>
            </section>
          </div>
        </div>
      )}
    </section>
  );
}
