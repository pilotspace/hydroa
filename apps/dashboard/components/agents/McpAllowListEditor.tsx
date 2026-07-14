"use client";

/**
 * McpAllowListEditor — agents-console TASK.md §3 CONTRACT M9. The ONE new
 * "editable list of {url,label} rows" component this task introduces (no existing
 * primitive in this codebase fits — DESIGN.md §3), mounted TWICE by McpPolicyPanel
 * (tenant scope + key scope) rather than forked — the "design it once, use everywhere"
 * rule `RegionBadge`/`TierSelector` already establish for this codebase.
 *
 * Local draft state is seeded from the `entries` prop ONLY when that prop's reference
 * actually changes (a successful save, or switching which list is being edited) — a
 * REJECTED save therefore leaves the operator's in-progress edits and any `fieldErrors`
 * visible, never optimistically reverted to the last-saved list (M11/R6/R7). `readOnly`
 * disables every control and hides Add/Save — the list itself still renders (GET is
 * always allowed; only the write is role-gated) rather than being hidden entirely (M9).
 */

import { useState } from "react";
import { Button, Input } from "@/components/ui";

export interface McpAllowListEntry {
  url: string;
  label: string;
}

export interface McpAllowListEditorProps {
  scope: "tenant" | "key";
  entries: McpAllowListEntry[];
  onSave: (entries: McpAllowListEntry[]) => Promise<void>;
  /** per-row inline error (M11), index-keyed */
  fieldErrors?: Record<number, string>;
  /** true for a non-owner viewing the tenant section (M9) */
  readOnly?: boolean;
}

export function McpAllowListEditor({ scope, entries, onSave, fieldErrors = {}, readOnly = false }: McpAllowListEditorProps) {
  const [draft, setDraft] = useState<McpAllowListEntry[]>(entries);
  const [seeded, setSeeded] = useState(entries);
  const [isSaving, setIsSaving] = useState(false);
  const [generalError, setGeneralError] = useState<string | null>(null);

  if (entries !== seeded) {
    setSeeded(entries);
    setDraft(entries);
  }

  function updateRow(index: number, patch: Partial<McpAllowListEntry>) {
    setDraft((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function removeRow(index: number) {
    setDraft((rows) => rows.filter((_, i) => i !== index));
  }

  function addRow() {
    setDraft((rows) => [...rows, { url: "", label: "" }]);
  }

  async function handleSave() {
    setGeneralError(null);
    setIsSaving(true);
    try {
      await onSave(draft);
    } catch {
      // the parent (McpPolicyPanel) owns fieldErrors/general error surfacing from the
      // mutation's own onError — this catch only prevents an unhandled rejection here.
      setGeneralError("Save failed");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-4" data-scope={scope}>
      {readOnly ? (
        // A read-only viewer sees the SAME list as real text, never a disabled-but-
        // editable-looking input — the section is presented read-only, not hidden
        // (M9: GET is any role; only the write is owner-gated).
        <ul className="flex flex-col gap-1.5 text-sm">
          {draft.length === 0 ? (
            <li className="text-muted-foreground">No servers on this list.</li>
          ) : (
            draft.map((row, index) => (
              <li key={index} className="text-foreground">
                {row.url} {row.label ? `— ${row.label}` : ""}
              </li>
            ))
          )}
        </ul>
      ) : (
        <ul className="flex flex-col gap-2">
          {draft.map((row, index) => (
            <li key={index} className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor={`mcp-allowlist-${scope}-url-${index}`} className="sr-only">
                  Server URL
                </label>
                <Input
                  id={`mcp-allowlist-${scope}-url-${index}`}
                  aria-label="Server URL"
                  type="text"
                  value={row.url}
                  onChange={(e) => updateRow(index, { url: e.target.value })}
                  placeholder="https://mcp.example.com"
                  className="min-w-64 flex-1"
                />
                <label htmlFor={`mcp-allowlist-${scope}-label-${index}`} className="sr-only">
                  Label
                </label>
                <Input
                  id={`mcp-allowlist-${scope}-label-${index}`}
                  aria-label="Label"
                  type="text"
                  value={row.label}
                  onChange={(e) => updateRow(index, { label: e.target.value })}
                  placeholder="Label"
                  className="w-40"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove server ${index + 1}`}
                  onClick={() => removeRow(index)}
                >
                  ×
                </Button>
              </div>
              {fieldErrors[index] ? (
                <p role="alert" aria-live="polite" className="text-xs text-destructive">
                  {fieldErrors[index]}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {readOnly ? (
        <p className="text-xs text-muted-foreground">Read-only — owner permission required to edit.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={addRow}>
            + Add server
          </Button>
          <Button type="button" size="sm" onClick={handleSave} disabled={isSaving}>
            {isSaving ? "Saving…" : "Save"}
          </Button>
        </div>
      )}

      {generalError ? (
        <p role="alert" aria-live="polite" className="text-xs text-destructive">
          {generalError}
        </p>
      ) : null}
    </div>
  );
}
