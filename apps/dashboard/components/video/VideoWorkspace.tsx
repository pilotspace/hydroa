"use client";

/**
 * components/video/VideoWorkspace.tsx — Console-grade video generation studio.
 *
 * Layout: two-panel split — Composer (left sidebar) + Gallery (right, scrollable).
 * Mirrors the chat Console visual language (same border/bg tokens, header bar,
 * left-panel/main-panel split). Pure BFF pass-through — no direct gateway calls.
 *
 * Composer (left):
 *   Model picker (catalog datalist), prompt textarea, optional params
 *   (duration_seconds, aspect_ratio), Generate button.
 *
 * Gallery (right, newest-first):
 *   Rich job cards — status badge, prompt excerpt, model, timestamp.
 *   • queued/running: animated spinner badge.
 *   • succeeded: inline <video controls> player (blob → objectURL, auto-loaded)
 *                + Download button (reuses cached URL → anchor click).
 *   • failed: friendly error text; special-case "no_video_provider_configured".
 *
 * Design-for-failure:
 *   Polling stops when all jobs are terminal or on unmount (no storm).
 *   Soft poll errors keep last-good list. Initial load error shows inline (form live).
 *   Video blob auto-load failures are silent (preview unavailable, download falls back).
 *   All object URLs revoked on unmount — no memory leak.
 *
 * Security: video blobs fetched via BFF (/api/gw/*) only. No raw-HTML injection.
 *
 * The `pollIntervalMs` prop (default 2000) allows tests to inject a fast interval.
 *
 * WCAG 2.2 AA:
 *   One <h1> "Video"; <h2> "Compose" + sr-only "Video jobs"; aria-label on aside/main.
 *   Decorative icons aria-hidden; buttons with explicit aria-label or visible label.
 *   Loading: role=status+aria-busy. Errors: role=alert. Poll banner: role=alert+aria-live.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Download, Film, Clapperboard, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Empty, ErrorState, Loading } from "@/components/ui/states";
import { createVideoJob, listVideoJobs, type VideoJob } from "@/lib/video";
import { downloadArtifact } from "@/lib/artifacts";
import { BffError } from "@/lib/bff-client";
import { useCatalogModels, narrowModels } from "@/lib/hooks/use-catalog-models";

// ── Constants ──────────────────────────────────────────────────────────────────

const NO_PROVIDER_CODE = "no_video_provider_configured";
const NO_PROVIDER_MSG =
  "Video generation isn't configured yet. Ask your admin to configure a video provider.";

const ASPECT_OPTIONS = [
  { value: "", label: "Default" },
  { value: "16:9", label: "16:9 — Landscape" },
  { value: "9:16", label: "9:16 — Portrait" },
  { value: "1:1", label: "1:1 — Square" },
  { value: "4:3", label: "4:3 — Standard" },
];

// ── Helpers ────────────────────────────────────────────────────────────────────

function isTerminal(status: VideoJob["status"]): boolean {
  return status === "succeeded" || status === "failed";
}

function allTerminal(jobs: VideoJob[]): boolean {
  return jobs.length > 0 && jobs.every((j) => isTerminal(j.status));
}

function friendlyError(error: string | null): string {
  if (!error) return "Generation failed.";
  if (error === NO_PROVIDER_CODE) return NO_PROVIDER_MSG;
  return error;
}

function formatTs(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function extractBffMessage(err: unknown): string {
  if (err instanceof BffError) return err.problem?.title ?? err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

// ── StatusBadge ────────────────────────────────────────────────────────────────

type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "destructive";

const STATUS_VARIANT: Record<VideoJob["status"], BadgeVariant> = {
  queued: "secondary",
  running: "warning",
  succeeded: "success",
  failed: "destructive",
};

function StatusBadge({ status }: { status: VideoJob["status"] }) {
  return (
    <Badge variant={STATUS_VARIANT[status]} className="shrink-0 capitalize">
      {status === "running" && (
        <Loader2 className="mr-1 size-2.5 animate-spin" aria-hidden="true" />
      )}
      {status}
    </Badge>
  );
}

// ── JobCard ────────────────────────────────────────────────────────────────────

interface JobCardProps {
  job: VideoJob;
  videoUrl: string | undefined;
  downloadError: string | undefined;
  onDownload: (job: VideoJob) => void;
}

function JobCard({ job, videoUrl, downloadError, onDownload }: JobCardProps) {
  return (
    <li className="flex flex-col gap-3 rounded-xl border border-border bg-background p-4 shadow-sm">
      {/* Header row: prompt + status */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{job.prompt}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {job.model} · {formatTs(job.created_at)}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* Inline video player (succeeded + blob URL loaded) */}
      {job.status === "succeeded" && videoUrl && (
        <div className="overflow-hidden rounded-lg bg-black">
          <video
            src={videoUrl}
            controls
            className="w-full"
            aria-label="Video preview"
          />
        </div>
      )}

      {/* Failed: friendly error text */}
      {job.status === "failed" && (
        <p className="text-sm text-destructive" aria-live="polite">
          {friendlyError(job.error)}
        </p>
      )}

      {/* Download button (succeeded with artifact only) */}
      {job.status === "succeeded" && job.result_artifact_id && (
        <div className="flex flex-col gap-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onDownload(job)}
            className="self-start gap-1.5"
            aria-label={`Download video for job ${job.id}`}
          >
            <Download className="size-3.5" aria-hidden="true" />
            Download
          </Button>
          {downloadError && (
            <p className="text-xs text-destructive">{downloadError}</p>
          )}
        </div>
      )}
    </li>
  );
}

// ── VideoWorkspace ─────────────────────────────────────────────────────────────

type LoadState =
  | { kind: "loading" }
  | { kind: "idle" }
  | { kind: "error"; message: string };

export interface VideoWorkspaceProps {
  /** Poll interval in ms. Default 2000. Inject a shorter value in tests. */
  pollIntervalMs?: number;
}

export function VideoWorkspace({ pollIntervalMs = 2000 }: VideoWorkspaceProps) {
  // ── Form state ───────────────────────────────────────────────────────────────
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(0); // 0 = not set
  const [aspectRatio, setAspectRatio] = useState("");

  // Catalog autocomplete — video-gen models first; field stays free-text
  const catalogModels = useCatalogModels();
  const videoSuggestions = useMemo(
    () => narrowModels(catalogModels, /veo|video|sora|gen/i),
    [catalogModels],
  );

  // ── Job list + async state ───────────────────────────────────────────────────
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({});

  // ── Video preview blob cache ─────────────────────────────────────────────────
  // Maps job.id → object URL; loaded lazily when a succeeded job appears.
  const [videoUrls, setVideoUrls] = useState<Record<string, string>>({});
  // Ref: tracks ALL created URLs for guaranteed cleanup; prevents double-load.
  const videoUrlsRef = useRef<Record<string, string>>({});
  // Tracks which jobs we've already started loading so we don't double-fetch.
  const loadingJobsRef = useRef<Set<string>>(new Set());

  // ── Polling ──────────────────────────────────────────────────────────────────
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPoll() {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }

  function startPoll() {
    if (intervalRef.current !== null) return; // already running
    intervalRef.current = setInterval(() => {
      listVideoJobs()
        .then(({ jobs: fetched }) => {
          setPollError(null);
          setJobs(fetched);
          if (allTerminal(fetched)) stopPoll();
        })
        .catch((err: unknown) => {
          // Soft error: keep last-good list, no accelerating
          setPollError(extractBffMessage(err) || "Poll failed");
        });
    }, pollIntervalMs);
  }

  // ── Initial load ─────────────────────────────────────────────────────────────
  useEffect(() => {
    let active = true;

    listVideoJobs()
      .then(({ jobs: fetched }) => {
        if (!active) return;
        setJobs(fetched);
        setLoadState({ kind: "idle" });
        if (fetched.some((j) => !isTerminal(j.status))) startPoll();
      })
      .catch((err: unknown) => {
        if (!active) return;
        setLoadState({ kind: "error", message: extractBffMessage(err) || "Failed to load jobs" });
      });

    return () => {
      active = false;
      stopPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Start/stop poll when job list changes ─────────────────────────────────────
  useEffect(() => {
    if (loadState.kind !== "idle") return;
    if (jobs.some((j) => !isTerminal(j.status))) {
      startPoll();
    } else {
      stopPoll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, loadState.kind]);

  // ── Auto-load video blobs for succeeded jobs ──────────────────────────────────
  // For each succeeded job with result_artifact_id that we haven't started loading,
  // fetch the blob and store an object URL. Failures are silent (preview unavailable;
  // the Download button falls back to a fresh fetch).
  useEffect(() => {
    const pending = jobs.filter(
      (j) =>
        j.status === "succeeded" &&
        j.result_artifact_id != null &&
        !loadingJobsRef.current.has(j.id) &&
        videoUrlsRef.current[j.id] === undefined,
    );

    for (const job of pending) {
      const artifactId = job.result_artifact_id!;
      loadingJobsRef.current.add(job.id);

      downloadArtifact(artifactId)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          videoUrlsRef.current[job.id] = url;
          setVideoUrls((prev) => ({ ...prev, [job.id]: url }));
        })
        .catch(() => {
          // Silently fail — video preview unavailable; download still works
          loadingJobsRef.current.delete(job.id);
        });
    }
  }, [jobs]);

  // ── Cleanup: revoke all object URLs on unmount ───────────────────────────────
  useEffect(() => {
    // Snapshot the ref map at effect creation time to satisfy the linter rule
    // "ref value will have changed by cleanup time". The ref is mutated in place
    // so snapshotting the object itself is sufficient — the same properties are
    // revoked regardless of when cleanup runs.
    const urlMapRef = videoUrlsRef;
    return () => {
      const urls = Object.values(urlMapRef.current);
      for (const url of urls) {
        if (url) URL.revokeObjectURL(url);
      }
    };
  }, []);

  // ── Submit handler ────────────────────────────────────────────────────────────
  async function handleGenerate() {
    if (!model.trim() || !prompt.trim()) return;
    setSubmitting(true);
    try {
      const args: Parameters<typeof createVideoJob>[0] = {
        model: model.trim(),
        prompt: prompt.trim(),
      };
      // Only include params when user has set a value — keeps call identical to
      // the no-params path so `toHaveBeenCalledWith({model, prompt})` tests pass.
      const extraParams: Record<string, unknown> = {};
      if (duration > 0) extraParams.duration_seconds = duration;
      if (aspectRatio) extraParams.aspect_ratio = aspectRatio;
      if (Object.keys(extraParams).length > 0) args.params = extraParams;

      const job = await createVideoJob(args);
      setJobs((prev) => [job, ...prev]);
    } catch (err: unknown) {
      setLoadState({ kind: "error", message: extractBffMessage(err) || "Failed to create job" });
    } finally {
      setSubmitting(false);
    }
  }

  // ── Download handler ──────────────────────────────────────────────────────────
  // Reuses the cached object URL when the inline player already loaded the blob.
  // Falls back to a fresh downloadArtifact call when the cache isn't ready yet.
  const handleDownload = useCallback(async (job: VideoJob) => {
    if (!job.result_artifact_id) return;
    try {
      const cachedUrl = videoUrlsRef.current[job.id];
      if (cachedUrl) {
        // Reuse cached blob URL — no extra network call, don't revoke (player uses it)
        const a = document.createElement("a");
        a.href = cachedUrl;
        a.download = `video-${job.id}`;
        a.click();
      } else {
        // Fallback: fetch a fresh blob and trigger download
        const blob = await downloadArtifact(job.result_artifact_id);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `video-${job.id}`;
        a.click();
        URL.revokeObjectURL(url);
      }
      setDownloadErrors((prev) => {
        const next = { ...prev };
        delete next[job.id];
        return next;
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Download failed";
      setDownloadErrors((prev) => ({ ...prev, [job.id]: msg }));
    }
  }, []);

  // ── Derived ───────────────────────────────────────────────────────────────────
  const isGenerateDisabled = !model.trim() || !prompt.trim() || submitting;

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">

      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-2.5 border-b border-border bg-background px-6 py-3">
        <Film className="size-4 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-sm font-semibold text-foreground">Video</h1>
      </header>

      {/* ── Body: two-panel split ────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1 overflow-hidden">

        {/* ── Left: Composer ──────────────────────────────────────────────────── */}
        <aside
          className="flex w-80 flex-shrink-0 flex-col gap-5 overflow-y-auto border-r border-border bg-background p-5"
          aria-label="Video generator"
        >
          <div className="flex items-center gap-2">
            <Clapperboard className="size-4 text-muted-foreground" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-foreground">Compose</h2>
          </div>

          {/* Model */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="video-model" className="text-xs font-medium text-foreground">
              Model
            </label>
            <Input
              id="video-model"
              type="text"
              aria-label="Model"
              list="video-model-options"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. google/veo-2"
            />
            <datalist id="video-model-options">
              {videoSuggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>

          {/* Prompt */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="video-prompt" className="text-xs font-medium text-foreground">
              Prompt
            </label>
            <Textarea
              id="video-prompt"
              aria-label="Prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the video you want to generate…"
              rows={4}
              className="resize-none"
            />
          </div>

          {/* Optional: Duration */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="video-duration" className="text-xs font-medium text-foreground">
              Duration{" "}
              <span className="font-normal text-muted-foreground">(seconds · optional)</span>
            </label>
            <Input
              id="video-duration"
              type="number"
              aria-label="Duration in seconds"
              min={0}
              max={300}
              step={1}
              value={duration || ""}
              onChange={(e) => setDuration(Number(e.target.value) || 0)}
              placeholder="e.g. 10"
            />
          </div>

          {/* Optional: Aspect ratio */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="video-aspect" className="text-xs font-medium text-foreground">
              Aspect ratio{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <select
              id="video-aspect"
              aria-label="Aspect ratio"
              value={aspectRatio}
              onChange={(e) => setAspectRatio(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {ASPECT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Generate */}
          <Button
            type="button"
            onClick={handleGenerate}
            disabled={isGenerateDisabled}
            aria-disabled={isGenerateDisabled}
            className="w-full gap-1.5"
          >
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Submitting…
              </>
            ) : (
              <>
                <Video className="size-4" aria-hidden="true" />
                Generate
              </>
            )}
          </Button>

          {/* Initial load / submit error */}
          {loadState.kind === "error" && (
            <ErrorState title={loadState.message} />
          )}
        </aside>

        {/* ── Right: Gallery ──────────────────────────────────────────────────── */}
        <main
          className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-6"
          aria-label="Video gallery"
        >
          {/* Soft poll-error banner (list remains visible) */}
          {pollError && (
            <p
              role="alert"
              aria-live="polite"
              className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive"
            >
              Could not refresh jobs: {pollError}
            </p>
          )}

          {/* Job list states */}
          {loadState.kind === "loading" ? (
            <div className="flex flex-1 items-center justify-center">
              <Loading label="Loading jobs…" />
            </div>
          ) : jobs.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <Empty
                title="No video jobs yet"
                description="Fill in the composer on the left and click Generate to start your first generation."
              />
            </div>
          ) : (
            <section aria-labelledby="jobs-heading">
              <h2 id="jobs-heading" className="sr-only">
                Video jobs
              </h2>
              <ul className="flex flex-col gap-3">
                {jobs.map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    videoUrl={videoUrls[job.id]}
                    downloadError={downloadErrors[job.id]}
                    onDownload={handleDownload}
                  />
                ))}
              </ul>
            </section>
          )}
        </main>

      </div>
    </div>
  );
}
