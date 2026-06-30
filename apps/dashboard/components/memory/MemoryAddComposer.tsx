"use client";

/**
 * components/memory/MemoryAddComposer.tsx — add-memory form.
 *
 * Fields:
 *   - Content textarea (required, disabled on empty/whitespace)
 *   - Metadata textarea (optional JSON, parsed before submit)
 *
 * Rules:
 *   - Submit disabled when content is empty or whitespace-only.
 *   - If metadata field is non-empty it must be valid JSON; invalid JSON
 *     shows an inline error and blocks submit.
 *   - Content is always rendered as plain text, never as raw HTML.
 */

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";

interface MemoryAddComposerProps {
  onAdd: (content: string, metadata?: Record<string, unknown>) => Promise<void>;
}

export function MemoryAddComposer({ onAdd }: MemoryAddComposerProps) {
  const [content, setContent] = useState("");
  const [metadataRaw, setMetadataRaw] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const isDisabled = pending || !content.trim();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = content.trim();
    if (!trimmed) return;

    let metadata: Record<string, unknown> | undefined;
    if (metadataRaw.trim()) {
      try {
        metadata = JSON.parse(metadataRaw.trim()) as Record<string, unknown>;
      } catch {
        setError('Metadata must be valid JSON (e.g. {"key": "value"})');
        return;
      }
    }

    setError("");
    setPending(true);

    onAdd(trimmed, metadata)
      .then(() => {
        setContent("");
        setMetadataRaw("");
        setPending(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to add memory");
        setPending(false);
      });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="memory-content" className="text-sm font-medium text-foreground">
          Content
        </label>
        <Textarea
          id="memory-content"
          aria-label="Memory content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Enter memory content…"
          rows={3}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="memory-metadata" className="text-sm font-medium text-foreground">
          Metadata{" "}
          <span className="font-normal text-muted-foreground">(optional JSON)</span>
        </label>
        <Input
          id="memory-metadata"
          aria-label="Metadata"
          value={metadataRaw}
          onChange={(e) => setMetadataRaw(e.target.value)}
          placeholder='{"key": "value"}'
        />
      </div>

      <Button type="submit" disabled={isDisabled} aria-disabled={isDisabled}>
        {pending ? "Adding…" : "Add memory"}
      </Button>

      {error && (
        <ErrorState title={error} />
      )}
    </form>
  );
}
