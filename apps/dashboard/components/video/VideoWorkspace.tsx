"use client";

/**
 * components/video/VideoWorkspace.tsx — v47 video generation workspace.
 *
 * Surfaces:
 *   - A form: model text input + prompt textarea → "Generate" button DISABLED
 *     until BOTH are non-empty. On click → createVideoJob → prepends the returned
 *     job to the list.
 *   - A list of jobs (newest first), each showing its status.
 *   - "Download" action ONLY when status==="succeeded" && result_artifact_id;
 *     click → downloadArtifact(result_artifact_id) → object URL → anchor click.
 *   - Failed jobs show their error verbatim, with a friendly special-case for
 *     "no_video_provider_configured". No Download button for failed jobs.
 *   - POLLING (design-for-failure): active ONLY while any job is queued/running;
 *     each tick calls listVideoJobs() and merges the result; cleared when all jobs
 *     are terminal AND on unmount; a poll error shows a soft inline banner and
 *     keeps the last good list — no poll-storm.
 *   - Four states: empty / submitting / error / data.
 *   - WCAG-AA: labels, button states, aria-live regions.
 *
 * All network calls go to /api/gw/... (the BFF) — never the gateway directly.
 * No tenant id is ever sent client-side.
 *
 * The `pollIntervalMs` prop (default 2000) is exposed so tests can inject a
 * shorter interval and verify polling behaviour with real timers.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState, Loading } from "@/components/ui/states";
import { createVideoJob, listVideoJobs, type VideoJob } from "@/lib/video";
import { downloadArtifact } from "@/lib/artifacts";
import { BffError } from "@/lib/bff-client";
import { useCatalogModels, narrowModels } from "@/lib/hooks/use-catalog-models";

// ── constants ──────────────────────────────────────────────────────────────────

const NO_PROVIDER_CODE = "no_video_provider_configured";
const NO_PROVIDER_MSG = "Video generation isn't configured yet.";

// ── helpers ────────────────────────────────────────────────────────────────────

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

// ── download helper ────────────────────────────────────────────────────────────

async function triggerDownload(artifactId: string): Promise<void> {
  const blob = await downloadArtifact(artifactId);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `video-${artifactId}`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── VideoWorkspace ─────────────────────────────────────────────────────────────

type LoadState =
  | { kind: "loading" }
  | { kind: "idle" }
  | { kind: "error"; message: string };

interface VideoWorkspaceProps {
  /** Poll interval in ms. Default 2000. Pass a shorter value in tests. */
  pollIntervalMs?: number;
}

export function VideoWorkspace({ pollIntervalMs = 2000 }: VideoWorkspaceProps) {
  // ── form fields ──────────────────────────────────────────────────────────────
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");

  // Autocomplete suggestions from the configured catalog (video-gen models first);
  // free-text preserved so a catalog-omitted model (e.g. "google/veo-2") still works.
  const catalogModels = useCatalogModels();
  const videoSuggestions = useMemo(
    () => narrowModels(catalogModels, /veo|video|sora|gen/i),
    [catalogModels],
  );

  // ── job list ─────────────────────────────────────────────────────────────────
  const [jobs, setJobs] = useState<VideoJob[]>([]);

  // ── load/submit state ────────────────────────────────────────────────────────
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);

  // ── soft poll-error banner (does not replace the list) ───────────────────────
  const [pollError, setPollError] = useState<string | null>(null);

  // ── download per-job error ────────────────────────────────────────────────────
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({});

  // ── polling ref ──────────────────────────────────────────────────────────────
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── stop poll helper ──────────────────────────────────────────────────────────
  function stopPoll() {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }

  // ── start poll helper ─────────────────────────────────────────────────────────
  function startPoll() {
    if (intervalRef.current !== null) return; // already running
    intervalRef.current = setInterval(() => {
      listVideoJobs()
        .then(({ jobs: fetched }) => {
          setPollError(null);
          setJobs(fetched);
          if (allTerminal(fetched)) {
            stopPoll();
          }
        })
        .catch((err: unknown) => {
          // Soft error — keep last good list, do NOT accelerate the interval
          const msg =
            err instanceof BffError
              ? (err.problem?.title ?? err.message)
              : err instanceof Error
                ? err.message
                : "Poll failed";
          setPollError(msg);
        });
    }, pollIntervalMs);
  }

  // ── initial load ──────────────────────────────────────────────────────────────
  useEffect(() => {
    let active = true;

    listVideoJobs()
      .then(({ jobs: fetched }) => {
        if (!active) return;
        setJobs(fetched);
        setLoadState({ kind: "idle" });
        // Start polling only if any job is non-terminal
        if (fetched.some((j) => !isTerminal(j.status))) {
          startPoll();
        }
      })
      .catch((err: unknown) => {
        if (!active) return;
        const msg =
          err instanceof BffError
            ? (err.problem?.title ?? err.message)
            : err instanceof Error
              ? err.message
              : "Failed to load jobs";
        setLoadState({ kind: "error", message: msg });
      });

    return () => {
      active = false;
      stopPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── watch jobs: start/stop poll whenever the job list changes ────────────────
  useEffect(() => {
    if (loadState.kind !== "idle") return;
    if (jobs.some((j) => !isTerminal(j.status))) {
      startPoll();
    } else {
      stopPoll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, loadState.kind]);

  // ── submit handler ────────────────────────────────────────────────────────────
  async function handleGenerate() {
    if (!model.trim() || !prompt.trim()) return;
    setSubmitting(true);
    try {
      const job = await createVideoJob({ model: model.trim(), prompt: prompt.trim() });
      // Prepend new job (newest first)
      setJobs((prev) => [job, ...prev]);
    } catch (err: unknown) {
      const msg =
        err instanceof BffError
          ? (err.problem?.title ?? err.message)
          : err instanceof Error
            ? err.message
            : "Failed to create job";
      setLoadState({ kind: "error", message: msg });
    } finally {
      setSubmitting(false);
    }
  }

  // ── download handler ──────────────────────────────────────────────────────────
  async function handleDownload(job: VideoJob) {
    if (!job.result_artifact_id) return;
    try {
      await triggerDownload(job.result_artifact_id);
      setDownloadErrors((prev) => {
        const next = { ...prev };
        delete next[job.id];
        return next;
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Download failed";
      setDownloadErrors((prev) => ({ ...prev, [job.id]: msg }));
    }
  }

  // ── derived ───────────────────────────────────────────────────────────────────
  const isGenerateDisabled = !model.trim() || !prompt.trim() || submitting;

  // ── render ────────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Video</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-6">

          {/* ── Generate form ───────────────────────────────────────────────── */}
          <section
            aria-labelledby="video-heading"
            className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
          >
            <h2 id="video-heading" className="text-base font-semibold text-foreground">
              Generate a video
            </h2>

            {/* ── Model input ─────────────────────────────────────────────── */}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="video-model" className="text-sm font-medium text-foreground">
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

            {/* ── Prompt textarea ─────────────────────────────────────────── */}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="video-prompt" className="text-sm font-medium text-foreground">
                Prompt
              </label>
              <Textarea
                id="video-prompt"
                aria-label="Prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Describe the video you want to generate…"
                rows={3}
              />
            </div>

            {/* ── Generate button ─────────────────────────────────────────── */}
            <Button
              type="button"
              onClick={handleGenerate}
              disabled={isGenerateDisabled}
              aria-disabled={isGenerateDisabled}
            >
              {submitting ? "Submitting…" : "Generate"}
            </Button>

            {/* ── Submit / load error ─────────────────────────────────────── */}
            {loadState.kind === "error" && (
              <ErrorState title={loadState.message} />
            )}
          </section>

          {/* ── Poll error banner (soft — list still visible) ───────────────── */}
          {pollError && (
            <p
              role="alert"
              aria-live="polite"
              className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive"
            >
              Could not refresh jobs: {pollError}
            </p>
          )}

          {/* ── Job list ────────────────────────────────────────────────────── */}
          {loadState.kind === "loading" ? (
            <Loading label="Loading jobs…" />
          ) : jobs.length === 0 ? (
            <p className="text-center text-sm text-muted-foreground py-8">
              No video jobs yet. Fill in the form above and click Generate.
            </p>
          ) : (
            <section aria-labelledby="jobs-heading">
              <h2 id="jobs-heading" className="sr-only">
                Video jobs
              </h2>
              <ul className="flex flex-col gap-3">
                {jobs.map((job) => (
                  <li
                    key={job.id}
                    className="flex flex-col gap-2 rounded-xl border border-border bg-background p-4"
                  >
                    {/* ── Job header ───────────────────────────────────────── */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-foreground">
                          {job.prompt}
                        </p>
                        <p className="text-xs text-muted-foreground">{job.model}</p>
                      </div>
                      <StatusBadge status={job.status} />
                    </div>

                    {/* ── Error message ────────────────────────────────────── */}
                    {job.status === "failed" && (
                      <p className="text-sm text-destructive" aria-live="polite">
                        {friendlyError(job.error)}
                      </p>
                    )}

                    {/* ── Download button (succeeded + has artifact only) ───── */}
                    {job.status === "succeeded" && job.result_artifact_id && (
                      <div className="flex flex-col gap-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => handleDownload(job)}
                          aria-label={`Download video for job ${job.id}`}
                        >
                          Download
                        </Button>
                        {downloadErrors[job.id] && (
                          <p className="text-xs text-destructive">
                            {downloadErrors[job.id]}
                          </p>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

        </div>
      </div>
    </div>
  );
}

// ── StatusBadge ────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<VideoJob["status"], string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-destructive/10 text-destructive",
};

function StatusBadge({ status }: { status: VideoJob["status"] }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}
