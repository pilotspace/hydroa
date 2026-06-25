"use client";

/**
 * components/artifacts/ArtifactsWorkspace.tsx — v45 artifacts workspace.
 *
 * Surfaces:
 *   - List: on mount calls listArtifacts(), shows name, content_type, human size,
 *     created_at; each row has a Download button and a Delete button.
 *   - Upload: <input type="file"> + submit button (disabled until a file is chosen).
 *     On submit: reads File via FileReader.readAsDataURL → extracts base64 part →
 *     calls createArtifact() → prepends returned item to list + resets input.
 *     Best-effort: failure sets a non-blocking ErrorState, never throws.
 *   - Download: downloadArtifact(id) → URL.createObjectURL + anchor click →
 *     URL.revokeObjectURL.  Failure → non-blocking ErrorState.
 *   - Delete: deleteArtifact(id) → drop from list; failure → ErrorState.
 *
 * All network calls go to /api/gw/... (the BFF) — never the gateway directly.
 * No new dependencies; uses the v13/v23 ui primitives.
 */

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Empty, ErrorState, Loading } from "@/components/ui/states";
import {
  listArtifacts,
  createArtifact,
  downloadArtifact,
  deleteArtifact,
  type ArtifactItem,
} from "@/lib/artifacts";

// ── helpers ───────────────────────────────────────────────────────────────────

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ── ArtifactList ──────────────────────────────────────────────────────────────

interface ArtifactListProps {
  items: ArtifactItem[];
  onDownload: (item: ArtifactItem) => void;
  onDelete: (item: ArtifactItem) => void;
}

function ArtifactList({ items, onDownload, onDelete }: ArtifactListProps) {
  if (items.length === 0) {
    return (
      <Empty
        title="No artifacts yet"
        description="Upload a file using the form above."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-2" aria-label="Artifact list">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 p-4"
        >
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
            <p className="text-xs text-muted-foreground">{item.content_type}</p>
            <p className="text-xs text-muted-foreground">
              {humanSize(item.size_bytes)} &middot; {formatDate(item.created_at)}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Download ${item.name}`}
              onClick={() => onDownload(item)}
            >
              Download
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Delete ${item.name}`}
              onClick={() => onDelete(item)}
              className="text-destructive hover:text-destructive"
            >
              Delete
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ── List state ────────────────────────────────────────────────────────────────

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; artifacts: ArtifactItem[] };

// ── ArtifactsWorkspace ────────────────────────────────────────────────────────

export function ArtifactsWorkspace() {
  // ── list state ──────────────────────────────────────────────────────────
  const [listState, setListState] = useState<ListState>({ kind: "loading" });

  // ── upload form state ─────────────────────────────────────────────────
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadPending, setUploadPending] = useState(false);
  const [uploadError, setUploadError] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── action error ──────────────────────────────────────────────────────
  const [actionError, setActionError] = useState<string>("");

  // ── fetch on mount ─────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    listArtifacts()
      .then((result) => {
        if (!cancelled) setListState({ kind: "ready", artifacts: result.data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load artifacts";
          setListState({ kind: "error", message: msg });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // ── upload handler ────────────────────────────────────────────────────

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    setUploadError("");
  }

  function handleUpload(e: FormEvent) {
    e.preventDefault();
    if (!selectedFile) return; // guard: no file → no-op

    setUploadPending(true);
    setUploadError("");

    const file = selectedFile;
    const reader = new FileReader();

    reader.onload = (ev) => {
      const result = (ev.target as { result?: string }).result ?? "";
      // result is "data:<type>;base64,<B64>" — extract after the first comma
      const commaIdx = result.indexOf(",");
      const content_base64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
      const name = file.name;
      const content_type = file.type || "application/octet-stream";

      createArtifact(name, content_type, content_base64)
        .then((newItem) => {
          setListState((prev) => {
            if (prev.kind === "ready") {
              return { kind: "ready", artifacts: [newItem, ...prev.artifacts] };
            }
            return { kind: "ready", artifacts: [newItem] };
          });
          setSelectedFile(null);
          setUploadPending(false);
          if (fileInputRef.current) {
            fileInputRef.current.value = "";
          }
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : "Upload failed";
          setUploadError(msg);
          setUploadPending(false);
        });
    };

    reader.onerror = () => {
      setUploadError("Failed to read file");
      setUploadPending(false);
    };

    reader.readAsDataURL(file);
  }

  // ── download handler ──────────────────────────────────────────────────

  function handleDownload(item: ArtifactItem) {
    setActionError("");

    downloadArtifact(item.id)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = item.name;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Download failed";
        setActionError(msg);
      });
  }

  // ── delete handler ─────────────────────────────────────────────────────

  function handleDelete(item: ArtifactItem) {
    setActionError("");

    deleteArtifact(item.id)
      .then(() => {
        setListState((prev) => {
          if (prev.kind !== "ready") return prev;
          return {
            kind: "ready",
            artifacts: prev.artifacts.filter((a) => a.id !== item.id),
          };
        });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Delete failed";
        setActionError(msg);
      });
  }

  // ── derived ───────────────────────────────────────────────────────────

  const isUploadDisabled = uploadPending || !selectedFile;

  // ── render ────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Artifacts</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-6">

          {/* ── Upload form ──────────────────────────────────────────────── */}
          <section
            aria-labelledby="upload-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="upload-heading" className="text-base font-semibold text-foreground">
              Upload artifact
            </h2>

            <form onSubmit={handleUpload} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="artifact-file" className="text-sm font-medium text-foreground">
                  File
                </label>
                <input
                  ref={fileInputRef}
                  id="artifact-file"
                  data-testid="artifact-file-input"
                  type="file"
                  aria-label="Choose file to upload"
                  onChange={handleFileChange}
                  className="text-sm text-foreground file:mr-3 file:cursor-pointer file:rounded-md file:border file:border-border file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-foreground hover:file:bg-accent"
                />
              </div>

              <Button
                type="submit"
                disabled={isUploadDisabled}
                aria-disabled={isUploadDisabled}
              >
                {uploadPending ? "Uploading…" : "Upload"}
              </Button>
            </form>

            {uploadError && (
              <ErrorState title={uploadError} />
            )}
          </section>

          {/* ── Action error (download/delete failures) ───────────────── */}
          {actionError && (
            <ErrorState title={actionError} />
          )}

          {/* ── Artifact list ─────────────────────────────────────────── */}
          <section
            aria-labelledby="list-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="list-heading" className="text-base font-semibold text-foreground">
              Saved artifacts
            </h2>

            <div aria-live="polite">
              {listState.kind === "loading" && <Loading label="Loading artifacts…" />}
              {listState.kind === "error" && (
                <ErrorState title={listState.message} />
              )}
              {listState.kind === "ready" && (
                <ArtifactList
                  items={listState.artifacts}
                  onDownload={handleDownload}
                  onDelete={handleDelete}
                />
              )}
            </div>
          </section>

        </div>
      </div>
    </div>
  );
}
