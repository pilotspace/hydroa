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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { Loading, ErrorState, Empty } from "@/components/ui";
import { SpendSparkline } from "./SpendSparkline";

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
    <div data-testid="spend-page" className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Spend Analytics
      </h1>

      {/* Window selector — drives the query param (native <select> preserved
          so userEvent.selectOptions in the BFF tests keeps working) */}
      <div className="flex items-center gap-2">
        <label
          htmlFor="window-selector"
          className="text-sm font-medium text-foreground"
        >
          Time window
        </label>
        <select
          id="window-selector"
          data-testid="window-selector"
          value={window}
          onChange={(e) => setWindow(e.target.value as SpendWindow)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="day">day</option>
          <option value="week">week</option>
          <option value="month">month</option>
        </select>
      </div>

      {/* Loading state */}
      {isLoading && (
        <Loading
          label="Loading spend data"
          data-testid="spend-loading"
          className="animate-pulse"
        />
      )}

      {/* Error state (403, 422, etc.) */}
      {isError && !isLoading && (
        <div data-testid="spend-error">
          <ErrorState title={getErrorMessage(error)} />
        </div>
      )}

      {/* Zero-state */}
      {isZeroState && (
        <div data-testid="spend-zero-state">
          <Empty
            title="No usage in this period"
            description={`${data.totals.requests} requests — $${data.totals.cost_usd}`}
          />
        </div>
      )}

      {/* Data state */}
      {!isLoading && !isError && data !== undefined && !isZeroState && (
        <div data-testid="spend-data" className="flex flex-col gap-6">
          {/* Spend-over-time chart (additive, decorative; data fallback below) */}
          <SpendSparkline buckets={data.buckets} />

          {/* Totals */}
          <Card>
            <CardHeader>
              <CardTitle>Totals ({window})</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div>
                  <dt className="text-sm text-muted-foreground">Cost (USD)</dt>
                  <dd
                    data-testid="totals-cost"
                    className="text-2xl font-semibold text-foreground"
                  >
                    {data.totals.cost_usd}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Requests</dt>
                  <dd
                    data-testid="totals-requests"
                    className="text-2xl font-semibold text-foreground"
                  >
                    {data.totals.requests}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Prompt tokens</dt>
                  <dd
                    data-testid="totals-prompt"
                    className="text-2xl font-semibold text-foreground"
                  >
                    {data.totals.prompt_tokens}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    Completion tokens
                  </dt>
                  <dd
                    data-testid="totals-completion"
                    className="text-2xl font-semibold text-foreground"
                  >
                    {data.totals.completion_tokens}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {/* Buckets — accessible data fallback for the chart */}
          {data.buckets.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Buckets</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-1">
                  {data.buckets.map((bucket) => (
                    <li
                      key={bucket.bucket_start}
                      data-testid="spend-bucket"
                      className="text-sm text-foreground"
                    >
                      <span>{bucket.bucket_start}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
