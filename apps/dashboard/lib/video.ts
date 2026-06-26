/**
 * lib/video.ts — v47 typed BFF client for video generation jobs.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 *
 * Result download REUSES downloadArtifact from lib/artifacts (raw fetch → blob
 * → object URL) because the video file is stored as an artifact once the job
 * succeeds.
 */

import { bffGet, bffPost } from "@/lib/bff-client";

export interface VideoJob {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  model: string;
  prompt: string;
  result_artifact_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** POST /v1/video/generations — submit a new text-to-video generation job. */
export function createVideoJob({
  model,
  prompt,
}: {
  model: string;
  prompt: string;
}): Promise<VideoJob> {
  return bffPost<VideoJob>("/v1/video/generations", { model, prompt });
}

/** GET /v1/video/generations/:id — fetch a single job by id. */
export function getVideoJob(id: string): Promise<VideoJob> {
  return bffGet<VideoJob>(`/v1/video/generations/${id}`);
}

/** GET /v1/video/generations — list all jobs for the authenticated tenant. */
export function listVideoJobs(): Promise<{ jobs: VideoJob[] }> {
  return bffGet<{ jobs: VideoJob[] }>("/v1/video/generations");
}
