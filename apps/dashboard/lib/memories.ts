/**
 * lib/memories.ts — v44 typed BFF client for the memory store.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 */

import { bffGet, bffPost, bffDelete } from "@/lib/bff-client";

export interface MemoryItem {
  id: string;
  content: string;
  created_at: string;
  has_embedding?: boolean;
}

export interface SearchResult {
  id: string;
  content: string;
  score: number | null;
  created_at: string;
}

/** GET /v1/memories — list all memories for the authenticated tenant. */
export function listMemories(): Promise<{ data: MemoryItem[] }> {
  return bffGet<{ data: MemoryItem[] }>("/v1/memories");
}

/** POST /v1/memories — create a new memory with optional metadata. */
export function createMemory(
  content: string,
  metadata?: Record<string, unknown>,
): Promise<MemoryItem> {
  return bffPost<MemoryItem>("/v1/memories", {
    content,
    ...(metadata ? { metadata } : {}),
  });
}

/** POST /v1/memories/search — semantic search returning ranked results. */
export function searchMemories(
  query: string,
  top_k?: number,
): Promise<{ data: SearchResult[] }> {
  return bffPost<{ data: SearchResult[] }>("/v1/memories/search", {
    query,
    ...(top_k ? { top_k } : {}),
  });
}

/** DELETE /v1/memories/:id — permanently delete a memory. */
export function deleteMemory(id: string): Promise<void> {
  return bffDelete(`/v1/memories/${id}`);
}
