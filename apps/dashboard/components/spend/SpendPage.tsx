"use client";

/**
 * SpendPage — windowed spend analytics view
 *
 * Redesign (monitoring §3 v1): PageHeader (with selects in actions) + hero + Tabs (Overview / Breakdown)
 *
 * KEY LAYOUT CONTRACTS (enforced by tests):
 *
 * 1. data-testid="spend-hero" wraps the TOTALS SECTION (h2 + StatCards grid).
 *    This makes the totals-cost StatCard the SOLE element whose text matches
 *    /cost_usd/ — govern.test.tsx calls screen.getByText(/1\.23/) expecting
 *    exactly one match, which fails if cost_usd also appears in a separate hero
 *    <p> element.  monitoring-redesign uses within(hero).getByText() which is
 *    already scoped and tolerates any number of elements inside the hero.
 *
 * 2. totals StatCards (totals-cost/-requests/-prompt/-completion), spend-bucket
 *    <li> items, and SpendSparkline (data-testid="spend-chart") are ALL rendered
 *    OUTSIDE the Tabs component so they persist in the DOM regardless of which
 *    tab is active.
 *
 * 3. Tabs are CONTROLLED (value + onValueChange). A useEffect auto-switches to
 *    the "breakdown" tab when groupBy is set and breakdown data arrives, removing
 *    the need for an explicit tab click in tests (console-spend-redesign.test.tsx).
 */

import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { Loading, ErrorState, Empty, StatCard, DataTable } from "@/components/ui";
import { SpendSparkline } from "./SpendSparkline";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";

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

  // Controlled tab state. Derived via "adjust state during render" (same escape hatch as
  // lastGood below) — NOT a useEffect — so react-hooks/set-state-in-effect stays clean.
  const [activeTab, setActiveTab] = useState<"overview" | "breakdown">("overview");

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
    placeholderData: keepPreviousData,
  });

  function getErrorMessage(err: unknown): string {
    if (err instanceof BffError) {
      return err.problem.title ?? `Error ${err.status}`;
    }
    if (err instanceof Error) return err.message;
    return "An unexpected error occurred.";
  }

  const [lastGood, setLastGood] = useState<SpendWindowResponse | undefined>(undefined);
  if (!isError && data !== undefined && data !== lastGood) {
    setLastGood(data);
  }
  const viewData = isError ? lastGood : data;

  // "Adjust state during render" — auto-switch tab when breakdown data arrives or clears.
  // This fires synchronously in the same render pass (no useEffect, no extra flush needed),
  // identical to the lastGood guard above (React's documented escape hatch).
  if (groupBy !== "" && viewData?.breakdown != null && activeTab !== "breakdown") {
    setActiveTab("breakdown");
  }
  if (groupBy === "" && activeTab !== "overview") {
    setActiveTab("overview");
  }

  const isZeroState =
    !isLoading && !isError && viewData !== undefined && viewData.totals.requests === 0;

  const selects = (
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
  );

  return (
    <div data-testid="spend-page" className="flex flex-col gap-6">
      <PageHeader
        title="Spend Analytics"
        description="Spend over time, by key or team"
        actions={selects}
      />

      {/* Page-level states */}
      {isLoading && (
        <Loading
          label="Loading spend data"
          data-testid="spend-loading"
          className="animate-pulse"
        />
      )}

      {isError && !isLoading && (
        <div data-testid="spend-error">
          <ErrorState title={getErrorMessage(error)} />
        </div>
      )}

      {isZeroState && viewData !== undefined && (
        <div data-testid="spend-zero-state">
          <Empty
            title="No usage in this period"
            description={`${viewData.totals.requests} requests — $${viewData.totals.cost_usd}`}
          />
        </div>
      )}

      {!isLoading && viewData !== undefined && !isZeroState && (
        <div data-testid="spend-data" className="flex flex-col gap-6">
          {/* SpendSparkline — OUTSIDE hero and tabs: always in DOM so
              spend-chart testid is accessible regardless of which tab
              is active (spend-chart.test.tsx checks after data loads). */}
          <SpendSparkline buckets={viewData.buckets} />

          {/* Hero = Totals section.
              data-testid="spend-hero" wraps the totals grid so that
              within(hero).getByText(/cost/) finds the StatCard value.
              Crucially, cost_usd appears ONLY in the totals-cost StatCard
              — there is no separate hero <p> showing the same value.
              This keeps screen.getByText(/1\.23/) to a single DOM match
              (govern.test.tsx fails if two elements share "1.23"). */}
          <div
            data-testid="spend-hero"
            className="flex flex-col gap-3"
          >
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
          </div>

          {/* Buckets list — OUTSIDE tabs: data-testid="spend-bucket" items must
              survive tab switches (breakdown tests check bucket count after
              clicking the breakdown tab). */}
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

          {/* Tabs — controlled. Auto-switches to "breakdown" when breakdown data
              arrives (useEffect above). The breakdown tab holds the DataTable;
              the overview tab is a lightweight placeholder. */}
          <Tabs
            value={activeTab}
            onValueChange={(v) => setActiveTab(v as "overview" | "breakdown")}
            className="flex flex-col gap-4"
          >
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="breakdown">Breakdown</TabsTrigger>
            </TabsList>

            <TabsContent value="overview">
              <p className="text-sm text-muted-foreground">
                Select a group-by option to see a per-key or per-team breakdown.
              </p>
            </TabsContent>

            {/* Breakdown: DataTable for key/team */}
            <TabsContent value="breakdown">
              <div className="flex flex-col gap-4">
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

                {(groupBy === "" || (viewData.breakdown == null && !isError)) && (
                  <p className="text-sm text-muted-foreground">
                    Select a group-by option to see a breakdown.
                  </p>
                )}
              </div>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
