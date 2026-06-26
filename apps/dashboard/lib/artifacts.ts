/**
 * lib/artifacts.ts — v45 typed BFF client for the artifact store.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 *
 * downloadArtifact is a raw fetch (not bffGet) because the response is a
 * binary blob, not JSON — it must call res.blob() rather than res.json().
 */

import { bffGet, bffPost, bffDelete } from "@/lib/bff-client";

function appBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return (
      process.env.NEXT_PUBLIC_APP_URL ??
      process.env.VERCEL_URL ??
      "http://localhost:3000"
    );
  }
  return "";
}

export interface ArtifactItem {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

/** GET /v1/artifacts — list all artifacts for the authenticated tenant. */
export function listArtifacts(): Promise<{ data: ArtifactItem[] }> {
  return bffGet<{ data: ArtifactItem[] }>("/v1/artifacts");
}

/** POST /v1/artifacts — create a new artifact with base64-encoded content. */
export function createArtifact(
  name: string,
  content_type: string,
  content_base64: string,
): Promise<ArtifactItem> {
  return bffPost<ArtifactItem>("/v1/artifacts", {
    name,
    content_type,
    content_base64,
  });
}

/**
 * GET /api/gw/v1/artifacts/:id — download artifact as a Blob.
 *
 * Uses raw fetch + res.blob() (not bffGet) because the upstream returns binary
 * data, not JSON. Throws on any non-OK status so the caller can show an error.
 */
export async function downloadArtifact(id: string): Promise<Blob> {
  const res = await fetch(`${appBase()}/api/gw/v1/artifacts/${id}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Download failed: HTTP ${res.status}`);
  }
  return res.blob();
}

/** DELETE /v1/artifacts/:id — permanently delete an artifact. */
export function deleteArtifact(id: string): Promise<void> {
  return bffDelete(`/v1/artifacts/${id}`);
}
