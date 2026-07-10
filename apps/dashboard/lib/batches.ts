/**
 * lib/batches.ts — typed BFF client for the read-only batch statistics page.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 */

import { bffGet } from "@/lib/bff-client";

export interface BatchStatusCounts {
  pending: number;
  succeeded: number;
  errored: number;
  canceled: number;
  expired: number;
}

export interface BatchStatsData {
  /** ALWAYS "0.00" today — an explicit constant, not a query (see gateway stats_router.py). */
  savings_usd: string;
  total_requests: number;
  status_counts: BatchStatusCounts;
  /** Real, computed from flushed windows' actual elapsed wait time. null = no samples yet. */
  avg_window_wait_ms: number | null;
  /** ALWAYS null today — pending v58's batch-processing adapters (see gateway stats_router.py). */
  avg_completion_latency_ms: number | null;
  /** ALWAYS null today — pending v58's batch-processing adapters (see gateway stats_router.py). */
  completion_tpm: number | null;
}

/** GET /admin/batches/stats — tenant-wide batch statistics (owner or admin only). */
export function getBatchStats(): Promise<BatchStatsData> {
  return bffGet<BatchStatsData>("/admin/batches/stats");
}
