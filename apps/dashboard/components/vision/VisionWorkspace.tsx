"use client";

/**
 * components/vision/VisionWorkspace.tsx — Console-grade multimodal vision workspace.
 *
 * Layout: TopBar · Thread (role="log") · InspectorSidebar · InputArea
 *
 * Features:
 *   - Multi-turn conversation: each Ask appends a user + assistant turn; media
 *     thumbnail shown on first user turn only.
 *   - Media drop zone: click or drag image/* / video/* → inline preview (img/video)
 *     using the data URL from FileReader (testable; no URL.createObjectURL needed).
 *   - Inspector sidebar: Gemini model picker + per-response metadata (tokens, latency).
 *   - XSS-safe answer rendering via MessageMarkdown.
 *   - Stale-result guard: incrementing askSeq ref ignores outdated responses.
 *   - Failures non-blocking: ErrorState below thread; workspace preserved; optimistic
 *     user turn rolled back on error; prompt restored.
 *
 * Security: no raw HTML; media preview only from user-picked data URLs; BFF-only fetches.
 * Accessibility: WCAG 2.2 AA — landmarks, live regions, focus management, decorative icons aria-hidden.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Loading, ErrorState, Empty } from "@/components/ui/states";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import { bffGet, BffError } from "@/lib/bff-client";
import { askVisionWithMeta, type MediaKind, type VisionUsage } from "@/lib/vision";
import { cn } from "@/lib/cn";

// ── constants ─────────────────────────────────────────────────────────────────

const SIZE_WARN_BYTES = 20 * 1024 * 1024;

// ── helpers ───────────────────────────────────────────────────────────────────

function isGemini(id: string): boolean {
  return id.toLowerCase().includes("gemini");
}

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

// ── types ─────────────────────────────────────────────────────────────────────

type ModelsData = { data?: Array<{ id: string }> };

type MediaState = {
  dataUrl: string;
  kind: MediaKind;
  fileName: string;
  fileSize: number;
};

type UserTurn = {
  id: string;
  role: "user";
  prompt: string;
  /** Show media thumbnail inline on the first user turn only. */
  showMedia: boolean;
};

type AssistantTurn = {
  id: string;
  role: "assistant";
  content: string;
  usage?: VisionUsage;
  latencyMs?: number;
};

type ThreadTurn = UserTurn | AssistantTurn;

type LastMeta = {
  usage?: VisionUsage;
  latencyMs?: number;
  modelId?: string;
};

// ── MediaPreview ───────────────────────────────────────────────────────────────

function MediaPreview({ media }: { media: MediaState }) {
  if (media.kind === "image") {
    return (
      <img
        src={media.dataUrl}
        alt="Media preview"
        className="max-h-48 w-full rounded-lg object-cover"
      />
    );
  }
  return (
    <video
      src={media.dataUrl}
      controls
      aria-label="Video preview"
      className="max-h-48 w-full rounded-lg"
    />
  );
}

// ── UserBubble ─────────────────────────────────────────────────────────────────

function UserBubble({
  turn,
  media,
}: {
  turn: UserTurn;
  media: MediaState | null;
}) {
  return (
    <div className="flex flex-col items-end gap-2">
      {turn.showMedia && media && (
        <div className="w-full max-w-xs overflow-hidden rounded-xl border border-border">
          <MediaPreview media={media} />
        </div>
      )}
      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
        {turn.prompt}
      </div>
    </div>
  );
}

// ── AssistantBubble ────────────────────────────────────────────────────────────

function AssistantBubble({ turn }: { turn: AssistantTurn }) {
  return (
    <div className="max-w-[85%]">
      <div className="rounded-2xl rounded-tl-sm border border-border bg-muted/30 px-4 py-3">
        <MessageMarkdown content={turn.content} />
      </div>
      {turn.latencyMs !== undefined && (
        <p className="mt-1 pl-1 text-[11px] text-muted-foreground">
          {turn.latencyMs < 1000
            ? `${turn.latencyMs}ms`
            : `${(turn.latencyMs / 1000).toFixed(1)}s`}
        </p>
      )}
    </div>
  );
}

// ── VisionInspector ────────────────────────────────────────────────────────────

function VisionInspector({
  geminiModels,
  modelsLoading,
  noGemini,
  selectedModel,
  onModelChange,
  lastMeta,
}: {
  geminiModels: string[];
  modelsLoading: boolean;
  noGemini: boolean;
  selectedModel: string;
  onModelChange: (m: string) => void;
  lastMeta: LastMeta | null;
}) {
  return (
    <aside
      className="hidden h-full w-72 flex-shrink-0 flex-col border-l border-border bg-background xl:flex"
      aria-label="Vision inspector"
    >
      <div className="flex flex-col gap-4 overflow-y-auto p-4">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Configuration
        </span>

        {/* Model picker */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="vision-model" className="text-sm font-medium text-foreground">
            Model
          </label>
          {modelsLoading ? (
            <Loading label="Loading models…" />
          ) : noGemini ? (
            <p className="text-sm text-muted-foreground">No vision model available</p>
          ) : (
            <select
              id="vision-model"
              aria-label="Model"
              value={selectedModel}
              onChange={(e) => onModelChange(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {geminiModels.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Per-response metadata */}
        {lastMeta && (
          <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
            <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Last response
            </span>
            {lastMeta.modelId && (
              <div className="flex items-start justify-between gap-2">
                <span className="shrink-0 text-xs text-muted-foreground">Model</span>
                <span className="truncate text-right text-xs font-medium text-foreground">
                  {lastMeta.modelId}
                </span>
              </div>
            )}
            {lastMeta.usage?.totalTokens !== undefined && (
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground">Tokens</span>
                <span
                  className="text-xs font-medium text-foreground"
                  data-testid="inspector-tokens"
                >
                  {lastMeta.usage.totalTokens.toLocaleString()}
                </span>
              </div>
            )}
            {lastMeta.latencyMs !== undefined && (
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground">Latency</span>
                <span className="text-xs font-medium text-foreground">
                  {lastMeta.latencyMs < 1000
                    ? `${lastMeta.latencyMs}ms`
                    : `${(lastMeta.latencyMs / 1000).toFixed(1)}s`}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

// ── VisionWorkspace ───────────────────────────────────────────────────────────

export function VisionWorkspace() {
  // ── models ───────────────────────────────────────────────────────────────
  const [geminiModels, setGeminiModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [selectedModel, setSelectedModel] = useState<string>("");

  // ── media ─────────────────────────────────────────────────────────────────
  const [media, setMedia] = useState<MediaState | null>(null);
  const [sizeWarning, setSizeWarning] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── thread ────────────────────────────────────────────────────────────────
  const [thread, setThread] = useState<ThreadTurn[]>([]);

  // ── prompt ────────────────────────────────────────────────────────────────
  const [prompt, setPrompt] = useState<string>("");

  // ── ask state ─────────────────────────────────────────────────────────────
  const [isLoading, setIsLoading] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  /** Incremented on each ask; catch handler compares to ignore stale results. */
  const askSeqRef = useRef(0);

  // ── inspector metadata ────────────────────────────────────────────────────
  const [lastMeta, setLastMeta] = useState<LastMeta | null>(null);

  // ── fetch gemini models on mount ──────────────────────────────────────────
  useEffect(() => {
    let active = true;
    bffGet<ModelsData>("/admin/catalog/models")
      .then((res) => {
        if (!active) return;
        const all = res.data?.map((m) => m.id) ?? [];
        const filtered = all.filter(isGemini);
        setGeminiModels(filtered);
        setSelectedModel(filtered[0] ?? "");
        setModelsLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setGeminiModels([]);
        setSelectedModel("");
        setModelsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // ── file handling ─────────────────────────────────────────────────────────

  const processFile = useCallback((file: File) => {
    const kind: MediaKind = file.type.startsWith("video") ? "video" : "image";
    setSizeWarning(file.size > SIZE_WARN_BYTES);

    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = (ev.target as { result?: string }).result ?? "";
      setMedia({ dataUrl, kind, fileName: file.name, fileSize: file.size });
    };
    reader.onerror = () => {
      setMedia(null);
    };
    reader.readAsDataURL(file);
  }, []);

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    if (!file) {
      setMedia(null);
      setSizeWarning(false);
      return;
    }
    processFile(file);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  }

  // ── ask handler ───────────────────────────────────────────────────────────

  function handleAsk() {
    if (!selectedModel || !media || !prompt.trim() || isLoading) return;

    const currentPrompt = prompt.trim();
    const isFirstTurn = thread.length === 0;
    const userTurnId = `u-${uid()}`;
    const seq = ++askSeqRef.current;

    // Optimistically add user turn and clear prompt
    setThread((prev) => [
      ...prev,
      { id: userTurnId, role: "user", prompt: currentPrompt, showMedia: isFirstTurn },
    ]);
    setIsLoading(true);
    setAskError(null);
    setPrompt("");

    const t0 = Date.now();

    askVisionWithMeta({
      model: selectedModel,
      prompt: currentPrompt,
      dataUrl: media.dataUrl,
      mediaType: media.kind,
    })
      .then((result) => {
        if (seq !== askSeqRef.current) return; // stale result — ignore
        const latencyMs = Date.now() - t0;
        setThread((prev) => [
          ...prev,
          {
            id: `a-${uid()}`,
            role: "assistant",
            content: result.content,
            usage: result.usage,
            latencyMs,
          },
        ]);
        setLastMeta({ usage: result.usage, latencyMs, modelId: selectedModel });
        setIsLoading(false);
      })
      .catch((err: unknown) => {
        if (seq !== askSeqRef.current) return;
        // Roll back optimistic user turn; restore prompt for retry
        setThread((prev) => prev.filter((t) => t.id !== userTurnId));
        setPrompt(currentPrompt);
        let message: string;
        if (err instanceof BffError) {
          message =
            err.status === 413
              ? "media too large"
              : (err.problem?.title ?? err.message ?? "Request failed");
        } else {
          message = err instanceof Error ? err.message : "Request failed";
        }
        setAskError(message);
        setIsLoading(false);
      });
  }

  function handlePromptKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !isAskDisabled) {
      e.preventDefault();
      handleAsk();
    }
  }

  // ── derived ───────────────────────────────────────────────────────────────

  const noGemini = !modelsLoading && geminiModels.length === 0;
  const isAskDisabled =
    noGemini || !selectedModel || !media || !prompt.trim() || isLoading;

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      {/* ── TopBar ───────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-3 border-b border-border bg-background px-4 py-2.5">
        <h1 className="text-sm font-semibold text-foreground">Vision</h1>
        {isLoading && (
          <Loading label="Analyzing…" className="ml-auto text-xs" />
        )}
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">

        {/* ── Main (thread + input) ─────────────────────────────────────── */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">

          {/* Thread */}
          <div
            role="log"
            aria-label="Vision conversation"
            aria-live="polite"
            className="flex-1 overflow-y-auto"
          >
            {thread.length === 0 && !isLoading ? (
              <div className="flex h-full items-center justify-center p-8">
                <Empty
                  title={media ? "Ask something about this media" : "Upload media to start"}
                  description={
                    media
                      ? "Type a question below and press Ask"
                      : "Drag and drop or click to upload an image or video"
                  }
                />
              </div>
            ) : (
              <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4 pb-6">
                {thread.map((turn) =>
                  turn.role === "user" ? (
                    <UserBubble key={turn.id} turn={turn} media={media} />
                  ) : (
                    <AssistantBubble key={turn.id} turn={turn} />
                  ),
                )}
                {isLoading && (
                  <div className="self-start">
                    <Loading label="Thinking…" />
                  </div>
                )}
              </div>
            )}
          </div>

          {/* No Gemini warning (non-blocking, above input) */}
          {noGemini && (
            <div className="mx-4 mb-2">
              <p className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning-foreground">
                No Gemini model available. Vision analysis requires a Gemini model.
              </p>
            </div>
          )}

          {/* Error state (non-blocking) */}
          {askError && (
            <div className="mx-4 mb-2">
              <ErrorState title={askError} />
            </div>
          )}

          {/* ── Input area ─────────────────────────────────────────────── */}
          <div className="border-t border-border bg-background p-4">
            <div className="mx-auto flex max-w-2xl flex-col gap-3">

              {/* Drop zone + preview */}
              <div
                className={cn(
                  "relative rounded-xl border-2 border-dashed transition-colors",
                  isDragOver
                    ? "border-primary bg-primary/5"
                    : "border-border bg-muted/20 hover:border-muted-foreground/40",
                )}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                role="button"
                tabIndex={0}
                aria-label="Upload image or video"
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
              >
                {/* Hidden file input — programmatically triggered; always in DOM */}
                <input
                  ref={fileInputRef}
                  id="vision-file"
                  data-testid="vision-file-input"
                  type="file"
                  accept="image/*,video/*"
                  aria-label="Choose image or video file"
                  onChange={handleFileChange}
                  className="sr-only"
                  onClick={(e) => e.stopPropagation()}
                />

                {media ? (
                  <div className="p-2">
                    <MediaPreview media={media} />
                    <p className="mt-1.5 text-center text-xs text-muted-foreground">
                      {media.fileName} — click to change
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 p-5 text-center">
                    <UploadCloud className="size-7 text-muted-foreground" aria-hidden="true" />
                    <p className="text-sm font-medium text-foreground">
                      Drop image or video here
                    </p>
                    <p className="text-xs text-muted-foreground">
                      or click to browse · advisory 20 MB limit
                    </p>
                  </div>
                )}
              </div>

              {sizeWarning && (
                <p className="text-xs text-warning-foreground" aria-live="polite">
                  File exceeds 20 MB — the backend may reject files this large.
                </p>
              )}

              {/* Prompt + Ask */}
              <div className="flex gap-2">
                <Textarea
                  id="vision-prompt"
                  aria-label="Prompt"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={handlePromptKeyDown}
                  placeholder={
                    media
                      ? "Ask something about this media…"
                      : "Upload media first, then type your question…"
                  }
                  rows={2}
                  className="flex-1 resize-none"
                />
                <Button
                  type="button"
                  onClick={handleAsk}
                  disabled={isAskDisabled}
                  aria-disabled={isAskDisabled}
                  className="self-end"
                >
                  {isLoading ? "Asking…" : "Ask"}
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* ── Inspector sidebar ────────────────────────────────────────── */}
        <VisionInspector
          geminiModels={geminiModels}
          modelsLoading={modelsLoading}
          noGemini={noGemini}
          selectedModel={selectedModel}
          onModelChange={setSelectedModel}
          lastMeta={lastMeta}
        />
      </div>
    </div>
  );
}
