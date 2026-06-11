"use client";

/**
 * SpendPage — windowed spend analytics view
 *
 * Calls GET /api/gw/admin/spend?window={day|week|month} through the BFF
 * catch-all proxy with credentials:"include". No Authorization header
 * is ever constructed client-side.
 *
 * Default window: "month" on mount.
 * Window selector controls a TanStack Query queryKey so changing window
 * triggers a fresh fetch.
 *
 * Surfaces:
 *   - totals (cost_usd, requests, prompt_tokens, completion_tokens)
 *   - buckets list (bucket_start + cost_usd per bucket)
 *   - zero-state when totals.requests === 0
 *   - inline 403 / 422 errors (no crash)
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";

type SpendWindow = "day" | "week" | "month";

interface SpendTotals {
  bucket_start: string;
  bucket_end: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
}

interface SpendBucket {
  bucket_start: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
}

interface SpendWindowResponse {
  window: SpendWindow;
  bucket_size: SpendWindow;
  totals: SpendTotals;
  buckets: SpendBucket[];
  breakdown: null;
}

export function SpendPage() {
  const [window, setWindow] = useState<SpendWindow>("month");

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery<SpendWindowResponse>({
    // queryKey includes window so changing the selector triggers a fresh fetch (A3)
    queryKey: ["admin-spend", window],
    queryFn: () => bffGet<SpendWindowResponse>(`/admin/spend?window=${window}`),
  });

  function getErrorMessage(err: unknown): string {
    if (err instanceof BffError) {
      return err.problem.title ?? `Error ${err.status}`;
    }
    if (err instanceof Error) return err.message;
    return "An unexpected error occurred.";
  }

  const isZeroState =
    !isLoading && !isError && data !== undefined && data.totals.requests === 0;

  return (
    <div data-testid="spend-page">
      <h1>Spend Analytics</h1>

      {/* Window selector — drives the query param */}
      <div>
        <label htmlFor="window-selector">Time window</label>
        <select
          id="window-selector"
          data-testid="window-selector"
          value={window}
          onChange={(e) => setWindow(e.target.value as SpendWindow)}
        >
          <option value="day">day</option>
          <option value="week">week</option>
          <option value="month">month</option>
        </select>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div
          role="status"
          aria-busy="true"
          aria-label="Loading spend data"
          data-testid="spend-loading"
        >
          <span className="animate-pulse">Loading…</span>
        </div>
      )}

      {/* Error state (403, 422, etc.) */}
      {isError && !isLoading && (
        <p role="alert" data-testid="spend-error">
          {getErrorMessage(error)}
        </p>
      )}

      {/* Zero-state */}
      {isZeroState && (
        <div data-testid="spend-zero-state">
          <p>No usage in this period</p>
          <p>
            {data.totals.requests} requests — ${data.totals.cost_usd}
          </p>
        </div>
      )}

      {/* Data state */}
      {!isLoading && !isError && data !== undefined && !isZeroState && (
        <div data-testid="spend-data">
          {/* Totals */}
          <section>
            <h2>Totals ({window})</h2>
            <dl>
              <dt>Cost (USD)</dt>
              <dd data-testid="totals-cost">{data.totals.cost_usd}</dd>

              <dt>Requests</dt>
              <dd data-testid="totals-requests">{data.totals.requests}</dd>

              <dt>Prompt tokens</dt>
              <dd data-testid="totals-prompt">{data.totals.prompt_tokens}</dd>

              <dt>Completion tokens</dt>
              <dd data-testid="totals-completion">
                {data.totals.completion_tokens}
              </dd>
            </dl>
          </section>

          {/* Buckets */}
          {data.buckets.length > 0 && (
            <section>
              <h2>Buckets</h2>
              <ul>
                {data.buckets.map((bucket) => (
                  <li key={bucket.bucket_start} data-testid="spend-bucket">
                    <span>{bucket.bucket_start}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
