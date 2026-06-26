"use client";

/**
 * components/vision/VisionWorkspace.tsx — v46 vision workspace.
 *
 * Surfaces:
 *   - Model select: fetches GET /v1/models, keeps ONLY Gemini ids (case-insensitive).
 *     If the filtered list is empty → renders "No Gemini model available" and
 *     DISABLES the Ask flow (vision is Gemini-only in v46).
 *   - File input: <input type="file" accept="image/*,video/*">; on change →
 *     FileReader.readAsDataURL(file) → stores { dataUrl, mediaType, fileName }.
 *     If file.size > 20 MB shows an advisory non-blocking size warning.
 *   - Prompt: a textarea.
 *   - Ask button: DISABLED unless (selectedModel && dataUrl && prompt.trim()).
 *     On click → sets loading → calls askVision() → renders answer text.
 *     On BffError → renders a non-blocking ErrorState:
 *       - 413 → "media too large"
 *       - otherwise → problem title/message
 *     NEVER throws unhandled.
 *
 * States: idle / loading / answer / error.
 * All network calls go to /api/gw/... (the BFF) — never the gateway directly.
 * No tenant id is ever sent client-side.
 */

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState, Loading } from "@/components/ui/states";
import { bffGet } from "@/lib/bff-client";
import { askVision, type MediaKind } from "@/lib/vision";
import { BffError } from "@/lib/bff-client";

// ── constants ─────────────────────────────────────────────────────────────────

const SIZE_WARN_BYTES = 20 * 1024 * 1024; // 20 MB advisory threshold

// ── model fetching ────────────────────────────────────────────────────────────

type ModelsData = { data?: Array<{ id: string }> };

function isGemini(id: string): boolean {
  return id.toLowerCase().includes("gemini");
}

// ── VisionWorkspace ───────────────────────────────────────────────────────────

type AskState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "answer"; text: string }
  | { kind: "error"; message: string };

export function VisionWorkspace() {
  // ── models ───────────────────────────────────────────────────────────────
  const [geminiModels, setGeminiModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [selectedModel, setSelectedModel] = useState<string>("");

  // ── file state ────────────────────────────────────────────────────────────
  const [dataUrl, setDataUrl] = useState<string>("");
  const [mediaType, setMediaType] = useState<MediaKind>("image");
  const [fileName, setFileName] = useState<string>("");
  const [sizeWarning, setSizeWarning] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── prompt ────────────────────────────────────────────────────────────────
  const [prompt, setPrompt] = useState<string>("");

  // ── ask state ─────────────────────────────────────────────────────────────
  const [askState, setAskState] = useState<AskState>({ kind: "idle" });

  // ── fetch gemini models on mount ──────────────────────────────────────────
  useEffect(() => {
    let active = true;

    bffGet<ModelsData>("/v1/models")
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

  // ── file change handler ───────────────────────────────────────────────────

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    if (!file) {
      setDataUrl("");
      setFileName("");
      setSizeWarning(false);
      return;
    }

    const kind: MediaKind = file.type.startsWith("video") ? "video" : "image";
    setMediaType(kind);
    setFileName(file.name);
    setSizeWarning(file.size > SIZE_WARN_BYTES);

    const reader = new FileReader();
    reader.onload = (ev) => {
      const result = (ev.target as { result?: string }).result ?? "";
      setDataUrl(result);
    };
    reader.onerror = () => {
      setDataUrl("");
    };
    reader.readAsDataURL(file);
  }

  // ── ask handler ───────────────────────────────────────────────────────────

  function handleAsk() {
    if (!selectedModel || !dataUrl || !prompt.trim()) return;

    setAskState({ kind: "loading" });

    askVision({ model: selectedModel, prompt: prompt.trim(), dataUrl, mediaType })
      .then((text) => {
        setAskState({ kind: "answer", text });
      })
      .catch((err: unknown) => {
        let message: string;
        if (err instanceof BffError) {
          if (err.status === 413) {
            message = "media too large";
          } else {
            message = err.problem?.title ?? err.message ?? "Request failed";
          }
        } else {
          message = err instanceof Error ? err.message : "Request failed";
        }
        setAskState({ kind: "error", message });
      });
  }

  // ── derived ───────────────────────────────────────────────────────────────

  const noGemini = !modelsLoading && geminiModels.length === 0;
  const isAskDisabled =
    noGemini ||
    !selectedModel ||
    !dataUrl ||
    !prompt.trim() ||
    askState.kind === "loading";

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Vision</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-6">

          {/* ── Ask form ──────────────────────────────────────────────────── */}
          <section
            aria-labelledby="vision-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="vision-heading" className="text-base font-semibold text-foreground">
              Ask about an image or video
            </h2>

            {/* ── No Gemini model empty state ─────────────────────────────── */}
            {noGemini && (
              <p className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning-foreground">
                No Gemini model available. Vision analysis requires a Gemini model.
              </p>
            )}

            {/* ── Model select ─────────────────────────────────────────────── */}
            {!noGemini && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor="vision-model" className="text-sm font-medium text-foreground">
                  Model
                </label>
                {modelsLoading ? (
                  <Loading label="Loading models…" />
                ) : (
                  <select
                    id="vision-model"
                    aria-label="Model"
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
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
            )}

            {/* ── File input ───────────────────────────────────────────────── */}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="vision-file" className="text-sm font-medium text-foreground">
                Image or video
              </label>
              <input
                ref={fileInputRef}
                id="vision-file"
                data-testid="vision-file-input"
                type="file"
                accept="image/*,video/*"
                aria-label="Choose image or video file"
                onChange={handleFileChange}
                className="text-sm text-foreground file:mr-3 file:cursor-pointer file:rounded-md file:border file:border-border file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-foreground hover:file:bg-accent"
              />
              {fileName && (
                <p className="text-xs text-muted-foreground">
                  Selected: {fileName}{" "}
                  <span className="capitalize text-primary">({mediaType})</span>
                </p>
              )}
              {sizeWarning && (
                <p className="text-xs text-warning-foreground" aria-live="polite">
                  File exceeds 20 MB — the backend may reject files this large.
                </p>
              )}
            </div>

            {/* ── Prompt textarea ──────────────────────────────────────────── */}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="vision-prompt" className="text-sm font-medium text-foreground">
                Prompt
              </label>
              <Textarea
                id="vision-prompt"
                aria-label="Prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Describe what you want to know about this media…"
                rows={3}
              />
            </div>

            {/* ── Ask button ───────────────────────────────────────────────── */}
            <Button
              type="button"
              onClick={handleAsk}
              disabled={isAskDisabled}
              aria-disabled={isAskDisabled}
            >
              {askState.kind === "loading" ? "Asking…" : "Ask"}
            </Button>

            {/* ── Error state ──────────────────────────────────────────────── */}
            {askState.kind === "error" && (
              <ErrorState title={askState.message} />
            )}
          </section>

          {/* ── Answer ────────────────────────────────────────────────────── */}
          {askState.kind === "answer" && (
            <section
              aria-labelledby="answer-heading"
              className="flex flex-col gap-3 rounded-xl border border-border bg-background p-6"
            >
              <h2 id="answer-heading" className="text-base font-semibold text-foreground">
                Answer
              </h2>
              <p className="whitespace-pre-wrap text-sm text-foreground">{askState.text}</p>
            </section>
          )}

        </div>
      </div>
    </div>
  );
}
