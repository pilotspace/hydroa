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
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { Loading, ErrorState, Empty, StatCard, DataTable } from "@/components/ui";
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

interface SpendBreakdownItem {
  key_id: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
}

interface TeamSpendBreakdownItem {
  team_id: string | null;
  team_name: string | null;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  ledger_cost_usd: string;
}

interface KeyOpt {
  key_id: string;
  name: string;
  prefix?: string;
}

interface SpendWindowResponse {
  window: SpendWindow;
  bucket_size: SpendWindow;
  totals: SpendTotals;
  buckets: SpendBucket[];
  breakdown: SpendBreakdownItem[] | TeamSpendBreakdownItem[] | null;
}

const KEY_BREAKDOWN_COLUMNS: ColumnDef<SpendBreakdownItem>[] = [
  { accessorKey: "key_id", header: "Key" },
  { accessorKey: "requests", header: "Requests" },
  { accessorKey: "prompt_tokens", header: "Prompt" },
  { accessorKey: "completion_tokens", header: "Completion" },
  { accessorKey: "cost_usd", header: "Cost (USD)" },
];

const TEAM_BREAKDOWN_COLUMNS: ColumnDef<TeamSpendBreakdownItem>[] = [
  { id: "team", header: "Team", accessorFn: (r) => r.team_name ?? "(no team)" },
  { accessorKey: "requests", header: "Requests" },
  { accessorKey: "prompt_tokens", header: "Prompt" },
  { accessorKey: "completion_tokens", header: "Completion" },
  { accessorKey: "cost_usd", header: "Cost (USD)" },
  { accessorKey: "ledger_cost_usd", header: "Ledger cost (USD)" },
];

export function SpendPage() {
  const [spendWindow, setSpendWindow] = useState<SpendWindow>("month");
  const [groupBy, setGroupBy] = useState<"" | "key_id" | "team_id">("");
  const [keyId, setKeyId] = useState<string>("");

  // Keys dropdown for the key filter — tolerate loading/error (no crash)
  const { data: keys } = useQuery<KeyOpt[]>({
    queryKey: ["admin-keys"],
    queryFn: () => bffGet<KeyOpt[]>("/admin/keys"),
    retry: false,
  });

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery<SpendWindowResponse>({
    queryKey: ["admin-spend", spendWindow, groupBy, keyId],
    queryFn: () =>
      bffGet<SpendWindowResponse>(
        `/admin/spend?window=${spendWindow}` +
          (groupBy ? `&group_by=${groupBy}` : "") +
          (keyId ? `&key_id=${keyId}` : ""),
      ),
    // Keep the last good window visible while a new query loads/errors so a
    // transient 422/404 (bad group_by or cross-tenant key) leaves the prior
    // view intact with an inline alert (contract §1 / reject "bad_query").
    placeholderData: keepPreviousData,
  });

  function getErrorMessage(err: unknown): string {
    if (err instanceof BffError) {
      return err.problem.title ?? `Error ${err.status}`;
    }
    if (err instanceof Error) return err.message;
    return "An unexpected error occurred.";
  }

  // Remember the last successfully-rendered response. keepPreviousData covers
  // the pending phase, but an ERRORED query has data===undefined — so on a
  // transient 422/404 we fall back to this last-good value to keep the prior view
  // intact. Held in state and updated via React's "adjust state during render"
  // guard (no ref read in render, no setState in an effect): the update fires only
  // when a fresh successful response differs from what we last stored, so it
  // converges in one extra render and never loops.
  const [lastGood, setLastGood] = useState<SpendWindowResponse | undefined>(undefined);
  if (!isError && data !== undefined && data !== lastGood) {
    setLastGood(data);
  }
  const viewData = isError ? lastGood : data;

  const isZeroState =
    !isLoading && !isError && viewData !== undefined && viewData.totals.requests === 0;

  return (
    <div data-testid="spend-page" className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Spend Analytics
      </h1>

      {/* Window selector — drives the query param (native <select> preserved
          so userEvent.selectOptions in the BFF tests keeps working) */}
      <div className="flex flex-wrap items-center gap-4">
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
            value={spendWindow}
            onChange={(e) => setSpendWindow(e.target.value as SpendWindow)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="day">day</option>
            <option value="week">week</option>
            <option value="month">month</option>
          </select>
        </div>

        {/* Group-by + key-filter selects — always available per the v15
            governance-completion-ui contract (group_by works without keys;
            the key filter lists "All keys" plus any keys the tenant has). */}
        <div className="flex items-center gap-2">
          <label
            htmlFor="group-by-selector"
            className="text-sm font-medium text-foreground"
          >
            Group by
          </label>
          <select
            id="group-by-selector"
            aria-label="Group by"
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as "" | "key_id" | "team_id")}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">None</option>
            <option value="key_id">Key</option>
            <option value="team_id">Team</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label
            htmlFor="key-filter-selector"
            className="text-sm font-medium text-foreground"
          >
            Filter by key
          </label>
          <select
            id="key-filter-selector"
            aria-label="Filter by key"
            value={keyId}
            onChange={(e) => setKeyId(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">All keys</option>
            {(keys ?? []).map((k) => (
              <option key={k.key_id} value={k.key_id}>
                {k.name}{k.prefix ? ` (${k.prefix})` : ""}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <Loading
          label="Loading spend data"
          data-testid="spend-loading"
          className="animate-pulse"
        />
      )}

      {/* Error state (403, 422, etc.) — inline alert; the prior data (kept by
          placeholderData) stays rendered below so the view is not wiped. */}
      {isError && !isLoading && (
        <div data-testid="spend-error">
          <ErrorState title={getErrorMessage(error)} />
        </div>
      )}

      {/* Zero-state */}
      {isZeroState && viewData !== undefined && (
        <div data-testid="spend-zero-state">
          <Empty
            title="No usage in this period"
            description={`${viewData.totals.requests} requests — $${viewData.totals.cost_usd}`}
          />
        </div>
      )}

      {/* Data state — rendered whenever we have data (even alongside an error
          banner, via keepPreviousData + the last-good ref) so a transient query
          error does not blow away the prior totals/buckets/chart. */}
      {!isLoading && viewData !== undefined && !isZeroState && (
        <div data-testid="spend-data" className="flex flex-col gap-6">
          {/* Spend-over-time chart (additive, decorative; data fallback below) */}
          <SpendSparkline buckets={viewData.buckets} />

          {/* Totals — rendered via the shared StatCard block. The "Totals ({window})"
              heading and the sm:grid-cols-4 grid hook are preserved; each tile keeps its
              frozen totals-* data-testid via StatCard's valueTestId. */}
          <section className="flex flex-col gap-3">
            <h2 className="text-lg font-medium text-foreground">
              Totals ({viewData.window})
            </h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatCard
                label="Cost (USD)"
                value={viewData.totals.cost_usd}
                valueTestId="totals-cost"
              />
              <StatCard
                label="Requests"
                value={viewData.totals.requests}
                valueTestId="totals-requests"
              />
              <StatCard
                label="Prompt tokens"
                value={viewData.totals.prompt_tokens}
                valueTestId="totals-prompt"
              />
              <StatCard
                label="Completion tokens"
                value={viewData.totals.completion_tokens}
                valueTestId="totals-completion"
              />
            </div>
          </section>

          {/* Buckets — accessible data fallback for the chart */}
          {viewData.buckets.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Buckets</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-1">
                  {viewData.buckets.map((bucket) => (
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

          {/* Breakdown table — rendered only when breakdown is non-null AND the
              query is not in error (its shape is coupled to the current
              group_by; a kept-previous error response may be the other shape). */}
          {/* Breakdown tables — rendered via the shared sortable DataTable block. ariaLabel
              forwards to the <table> (keeps getByRole("table",{name:/spend by .../i}) green). */}
          {!isError && viewData.breakdown != null && groupBy === "key_id" && (
            <DataTable
              ariaLabel="Spend by key"
              caption="Spend by key"
              columns={KEY_BREAKDOWN_COLUMNS}
              data={viewData.breakdown as SpendBreakdownItem[]}
            />
          )}

          {!isError && viewData.breakdown != null && groupBy === "team_id" && (
            <DataTable
              ariaLabel="Spend by team"
              caption="Spend by team"
              columns={TEAM_BREAKDOWN_COLUMNS}
              data={viewData.breakdown as TeamSpendBreakdownItem[]}
            />
          )}
        </div>
      )}
    </div>
  );
}
