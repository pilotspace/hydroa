"use client";

/**
 * components/artifacts/ArtifactsWorkspace.tsx — v54 console-grade artifact library.
 *
 * Surfaces:
 *   - Search/filter by name or content_type (live, client-side)
 *   - Sort by name / size / date (toggle asc/desc; first click = ascending)
 *   - Upload zone: drag-and-drop OR file picker; reads File → base64 → POST
 *   - Artifact list: type icon, name, type, human size, timestamp
 *   - Detail pane (selected artifact):
 *       image/*        → downloadArtifact → objectURL → <img> (revoke on change/unmount)
 *       text/* or json → fetchArtifactText → res.text() → <pre> (escaped, no innerHTML)
 *       other          → download affordance only
 *   - Download: blob → objectURL → anchor click → revoke
 *   - Delete: guarded by confirmation dialog
 *
 * SECURITY:
 *   - Previews are XSS-safe: images shown via objectURLs, text/JSON shown as text
 *     content inside <pre> — never dangerouslySetInnerHTML.
 *   - Text preview size-capped at 256 KB.
 *   - All BFF calls go through /api/gw/* (same-origin, credentials:include).
 *
 * ACCESSIBILITY: WCAG 2.2 AA — all interactive elements reachable by keyboard,
 *   labelled, and role-correct. Decorative icons aria-hidden. One h1.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from "react";
import {
  File as FileIcon,
  FileText,
  FileImage,
  FileCode,
  Database,
  Download,
  Trash2,
  SortAsc,
  SortDesc,
  Search,
  Upload as UploadIcon,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, ErrorState, Loading } from "@/components/ui/states";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  listArtifacts,
  createArtifact,
  downloadArtifact,
  fetchArtifactText,
  deleteArtifact,
  type ArtifactItem,
} from "@/lib/artifacts";

// ── constants ─────────────────────────────────────────────────────────────────

const MAX_PREVIEW_BYTES = 256 * 1024; // 256 KB text/JSON preview cap

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

function isImage(ct: string): boolean {
  return ct.startsWith("image/");
}

function isTextPreviewable(ct: string): boolean {
  return ct.startsWith("text/") || ct === "application/json";
}

function typeIcon(ct: string) {
  if (ct.startsWith("image/")) return <FileImage className="size-4 shrink-0 text-blue-500" aria-hidden="true" />;
  if (ct.startsWith("text/")) return <FileText className="size-4 shrink-0 text-green-500" aria-hidden="true" />;
  if (ct === "application/json") return <FileCode className="size-4 shrink-0 text-yellow-500" aria-hidden="true" />;
  if (ct === "application/pdf") return <FileIcon className="size-4 shrink-0 text-red-500" aria-hidden="true" />;
  return <Database className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />;
}

// ── types ─────────────────────────────────────────────────────────────────────

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; artifacts: ArtifactItem[] };

type SortField = "none" | "name" | "size" | "date";
type SortDir = "asc" | "desc";

// PreviewState: derived from selectedItem + fetchResult — never stored in state directly.
// fetchResult tracks only the async download result. "idle" means "fetch not yet done".
type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "image"; objectURL: string }
  | { kind: "text"; content: string }
  | { kind: "download-only" }
  | { kind: "error"; message: string };

type FetchResult =
  | { kind: "idle" }
  | { kind: "image"; objectURL: string }
  | { kind: "text"; content: string }
  | { kind: "error"; message: string };

// ── ArtifactsWorkspace ────────────────────────────────────────────────────────

export function ArtifactsWorkspace() {
  // ── list state ─────────────────────────────────────────────────────────
  const [listState, setListState] = useState<ListState>({ kind: "loading" });

  // ── search + sort ──────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("none");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // ── selection + preview ────────────────────────────────────────────────
  const [selectedItem, setSelectedItem] = useState<ArtifactItem | null>(null);
  // fetchResult tracks the async download result. "idle" = fetch not done yet.
  // previewState is derived (not stored) — see derive block below.
  const [fetchResult, setFetchResult] = useState<FetchResult>({ kind: "idle" });
  // "adjust during render": reset fetchResult when selection changes (no effect needed).
  const [prevSelectedId, setPrevSelectedId] = useState<string | null>(null);
  const currentSelectedId = selectedItem?.id ?? null;
  if (currentSelectedId !== prevSelectedId) {
    setPrevSelectedId(currentSelectedId);
    setFetchResult({ kind: "idle" });
  }
  // Derive previewState from selectedItem + fetchResult — pure computation.
  const previewState: PreviewState = (() => {
    if (!selectedItem) return { kind: "idle" };
    const ct = selectedItem.content_type;
    if (!isImage(ct) && !isTextPreviewable(ct)) return { kind: "download-only" };
    if (fetchResult.kind === "idle") return { kind: "loading" };
    return fetchResult;
  })();

  // ── upload state ───────────────────────────────────────────────────────
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadPending, setUploadPending] = useState(false);
  const [uploadError, setUploadError] = useState<string>("");
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── action / delete state ──────────────────────────────────────────────
  const [actionError, setActionError] = useState<string>("");
  const [deleteCandidate, setDeleteCandidate] = useState<ArtifactItem | null>(null);

  // ── initial list fetch ─────────────────────────────────────────────────
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
    return () => { cancelled = true; };
  }, []);

  // ── preview effect: fetch when selection changes ───────────────────────
  // Note: previewState is derived (not state); only setFetchResult is called here,
  // and ONLY after awaits — satisfying react-hooks/set-state-in-effect.
  useEffect(() => {
    if (!selectedItem) return;
    const ct = selectedItem.content_type;
    if (!isImage(ct) && !isTextPreviewable(ct)) return;

    let objectURL: string | null = null;
    let cancelled = false;

    const run = async () => {
      try {
        if (isImage(ct)) {
          const blob = await downloadArtifact(selectedItem.id);
          if (cancelled) return;
          objectURL = URL.createObjectURL(blob);
          setFetchResult({ kind: "image", objectURL });
        } else {
          if (selectedItem.size_bytes > MAX_PREVIEW_BYTES) {
            setFetchResult({ kind: "error", message: "File too large to preview (limit 256 KB)" });
            return;
          }
          const text = await fetchArtifactText(selectedItem.id);
          if (cancelled) return;

          if (ct === "application/json") {
            try {
              const formatted = JSON.stringify(JSON.parse(text), null, 2);
              setFetchResult({ kind: "text", content: formatted });
            } catch {
              setFetchResult({ kind: "text", content: text });
            }
          } else {
            setFetchResult({ kind: "text", content: text });
          }
        }
      } catch (err: unknown) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Preview failed";
          setFetchResult({ kind: "error", message: msg });
        }
      }
    };

    void run();

    return () => {
      cancelled = true;
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [selectedItem]);

  // ── derived: filtered + sorted list ──────────────────────────────────
  const visibleArtifacts = useMemo(() => {
    if (listState.kind !== "ready") return [];

    const q = searchQuery.trim().toLowerCase();
    let items = q
      ? listState.artifacts.filter(
          (a) => a.name.toLowerCase().includes(q) || a.content_type.toLowerCase().includes(q),
        )
      : [...listState.artifacts];

    if (sortField !== "none") {
      items.sort((a, b) => {
        let cmp = 0;
        if (sortField === "name") cmp = a.name.localeCompare(b.name);
        else if (sortField === "size") cmp = a.size_bytes - b.size_bytes;
        else if (sortField === "date") cmp = a.created_at.localeCompare(b.created_at);
        return sortDir === "asc" ? cmp : -cmp;
      });
    }

    return items;
  }, [listState, searchQuery, sortField, sortDir]);

  // ── sort toggle ────────────────────────────────────────────────────────
  function handleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  }

  // ── shared file processing (used by picker + dropzone) ────────────────
  function processFile(file: File) {
    setUploadPending(true);
    setUploadError("");
    setSelectedFile(file);

    const reader = new FileReader();

    reader.onload = (ev) => {
      const result = (ev.target as { result?: string }).result ?? "";
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
          if (fileInputRef.current) fileInputRef.current.value = "";
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

  // ── upload form handlers ───────────────────────────────────────────────
  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    setUploadError("");
  }

  function handleUpload(e: FormEvent) {
    e.preventDefault();
    if (!selectedFile) return;
    processFile(selectedFile);
  }

  // ── drag-and-drop handlers ────────────────────────────────────────────
  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragEnter(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      setSelectedFile(null); // clear picker selection
      if (fileInputRef.current) fileInputRef.current.value = "";
      processFile(file);
    }
  }

  // ── download handler ───────────────────────────────────────────────────
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

  // ── delete guard handlers ──────────────────────────────────────────────
  function handleDeleteRequest(item: ArtifactItem) {
    setDeleteCandidate(item);
  }

  function handleDeleteConfirm() {
    if (!deleteCandidate) return;
    const item = deleteCandidate;
    setDeleteCandidate(null);
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
        if (selectedItem?.id === item.id) setSelectedItem(null);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Delete failed";
        setActionError(msg);
      });
  }

  function handleDeleteCancel() {
    setDeleteCandidate(null);
  }

  // ── sort label helper ─────────────────────────────────────────────────
  function sortLabel(field: SortField, label: string) {
    const active = sortField === field;
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label={`Sort by ${label.toLowerCase()}`}
        aria-pressed={active}
        onClick={() => handleSort(field)}
        className={active ? "text-foreground font-semibold" : "text-muted-foreground"}
      >
        {active ? (
          sortDir === "asc" ? (
            <SortAsc className="size-3.5" aria-hidden="true" />
          ) : (
            <SortDesc className="size-3.5" aria-hidden="true" />
          )
        ) : null}
        {label}
      </Button>
    );
  }

  const isUploadDisabled = uploadPending || !selectedFile;

  // ── render ─────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Artifacts</h1>
      </header>

      {/* ── Main two-pane layout ─────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1 overflow-hidden">

        {/* ── Left panel: upload + search + sort + list ──────────────────── */}
        <div className="flex w-full max-w-xl flex-shrink-0 flex-col overflow-y-auto border-r border-border p-5 xl:w-96">

          {/* Upload zone ──────────────────────────────────────────────── */}
          <section aria-labelledby="upload-heading" className="mb-5">
            <h2 id="upload-heading" className="mb-3 text-sm font-semibold text-foreground">
              Upload artifact
            </h2>

            {/* Drag-and-drop zone */}
            <div
              data-testid="artifact-dropzone"
              onDragOver={handleDragOver}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              aria-label="Drop zone for file upload"
              className={[
                "mb-3 flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-5 text-center transition-colors",
                isDragging
                  ? "border-primary bg-primary/5"
                  : "border-border bg-muted/20 hover:border-primary/50",
              ].join(" ")}
            >
              <UploadIcon className="mb-2 size-6 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                Drag files here to upload
              </p>
            </div>

            {/* File picker form */}
            <form onSubmit={handleUpload} className="flex flex-col gap-2">
              <div className="flex flex-col gap-1">
                <label htmlFor="artifact-file" className="text-xs font-medium text-foreground">
                  Or choose a file
                </label>
                <input
                  ref={fileInputRef}
                  id="artifact-file"
                  data-testid="artifact-file-input"
                  type="file"
                  aria-label="Choose file to upload"
                  onChange={handleFileChange}
                  className="text-sm text-foreground file:mr-3 file:cursor-pointer file:rounded-md file:border file:border-border file:bg-muted file:px-3 file:py-1 file:text-xs file:font-medium file:text-foreground hover:file:bg-accent"
                />
              </div>
              <Button
                type="submit"
                size="sm"
                disabled={isUploadDisabled}
                aria-disabled={isUploadDisabled}
              >
                {uploadPending ? "Uploading…" : "Upload"}
              </Button>
            </form>

            {uploadError && <ErrorState title={uploadError} className="mt-2" />}
          </section>

          {/* Search + sort ────────────────────────────────────────────── */}
          <div className="mb-3 flex flex-col gap-2">
            {/* Search */}
            <div className="relative">
              <Search
                className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <input
                type="text"
                aria-label="Search artifacts"
                placeholder="Search by name or type…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-8 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              {searchQuery && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" aria-hidden="true" />
                </button>
              )}
            </div>

            {/* Sort controls */}
            <div className="flex items-center gap-1">
              <span className="mr-1 text-xs text-muted-foreground">Sort:</span>
              {sortLabel("name", "Name")}
              {sortLabel("size", "Size")}
              {sortLabel("date", "Date")}
            </div>
          </div>

          {/* Artifact list ────────────────────────────────────────────── */}
          {actionError && <ErrorState title={actionError} className="mb-3" />}

          <div aria-live="polite">
            {listState.kind === "loading" && <Loading label="Loading artifacts…" />}
            {listState.kind === "error" && <ErrorState title={listState.message} />}
            {listState.kind === "ready" && visibleArtifacts.length === 0 && (
              <Empty
                title="No artifacts"
                description={
                  searchQuery
                    ? "No artifacts match your search."
                    : "Upload a file to get started."
                }
              />
            )}
            {listState.kind === "ready" && visibleArtifacts.length > 0 && (
              <ul aria-label="Artifact list" className="flex flex-col gap-1.5">
                {visibleArtifacts.map((item) => {
                  const isSelected = selectedItem?.id === item.id;
                  return (
                    <li
                      key={item.id}
                      className={[
                        "flex items-center gap-2 rounded-lg border bg-background px-3 py-2.5 transition-colors",
                        isSelected
                          ? "border-primary ring-1 ring-primary"
                          : "border-border hover:border-primary/50",
                      ].join(" ")}
                    >
                      {/* View button (selects artifact) */}
                      <button
                        type="button"
                        aria-label={`View ${item.name}`}
                        aria-pressed={isSelected}
                        onClick={() =>
                          setSelectedItem(isSelected ? null : item)
                        }
                        className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 rounded"
                      >
                        {typeIcon(item.content_type)}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-foreground">
                            {item.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {item.content_type}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {humanSize(item.size_bytes)} &middot; {formatDate(item.created_at)}
                          </p>
                        </div>
                      </button>

                      {/* Action buttons */}
                      <div className="flex shrink-0 gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Download ${item.name}`}
                          onClick={() => handleDownload(item)}
                        >
                          <Download className="size-3.5" aria-hidden="true" />
                          <span className="sr-only">Download</span>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Delete ${item.name}`}
                          onClick={() => handleDeleteRequest(item)}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="size-3.5" aria-hidden="true" />
                          <span className="sr-only">Delete</span>
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* ── Right panel: detail / preview pane ─────────────────────────── */}
        {selectedItem ? (
          <section
            aria-label="Artifact detail"
            className="flex min-w-0 flex-1 flex-col overflow-y-auto p-6"
          >
            {/* Header */}
            <div className="mb-4 flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-base font-semibold text-foreground">
                  {selectedItem.name}
                </h2>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {selectedItem.content_type}
                </p>
              </div>
              <button
                type="button"
                aria-label="Close detail pane"
                onClick={() => setSelectedItem(null)}
                className="shrink-0 rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </div>

            {/* Metadata */}
            <dl className="mb-5 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">Size</dt>
              <dd className="font-medium text-foreground">
                {humanSize(selectedItem.size_bytes)}
              </dd>
              <dt className="text-muted-foreground">Created</dt>
              <dd className="font-medium text-foreground">
                {formatDate(selectedItem.created_at)}
              </dd>
              <dt className="text-muted-foreground">ID</dt>
              <dd className="truncate font-mono text-xs text-foreground">
                {selectedItem.id}
              </dd>
            </dl>

            {/* Download action */}
            <div className="mb-5 flex gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => handleDownload(selectedItem)}
              >
                <Download className="size-3.5" aria-hidden="true" />
                Download
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => handleDeleteRequest(selectedItem)}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
                Delete
              </Button>
            </div>

            {/* Preview */}
            <div className="min-h-0 flex-1">
              {previewState.kind === "loading" && (
                <Loading label="Loading preview…" />
              )}
              {previewState.kind === "error" && (
                <ErrorState title={previewState.message} />
              )}
              {previewState.kind === "image" && (
                <div className="flex items-center justify-center rounded-lg border border-border bg-muted/20 p-4">
                  {/* XSS-safe: src is a blob: URL we created ourselves */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={previewState.objectURL}
                    alt={`Preview of ${selectedItem.name}`}
                    className="max-h-96 max-w-full rounded object-contain"
                  />
                </div>
              )}
              {previewState.kind === "text" && (
                <pre
                  data-testid="preview-text"
                  className="max-h-96 overflow-auto rounded-lg border border-border bg-muted/20 p-4 text-xs font-mono text-foreground whitespace-pre-wrap break-all"
                >
                  {/* XSS-safe: text content set via React children (escaped automatically) */}
                  {previewState.content}
                </pre>
              )}
              {previewState.kind === "download-only" && (
                <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border p-8 text-center">
                  {typeIcon(selectedItem.content_type)}
                  <p className="text-sm text-muted-foreground">
                    No inline preview available for this file type.
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => handleDownload(selectedItem)}
                  >
                    <Download className="size-3.5" aria-hidden="true" />
                    Download to view
                  </Button>
                </div>
              )}
            </div>
          </section>
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-center">
            <div>
              <FileIcon className="mx-auto mb-2 size-8 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                Select an artifact to view details and preview
              </p>
            </div>
          </div>
        )}
      </div>

      {/* ── Delete confirmation dialog ────────────────────────────────────── */}
      <Dialog open={deleteCandidate !== null} onOpenChange={(open) => { if (!open) handleDeleteCancel(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete artifact?</DialogTitle>
            <DialogDescription>
              {deleteCandidate
                ? `"${deleteCandidate.name}" will be permanently deleted. This action cannot be undone.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={handleDeleteCancel}
              aria-label="Cancel"
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              aria-label="Confirm"
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
