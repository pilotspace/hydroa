"use client";

/**
 * GuardrailAnalyticsPage — windowed guardrail-verdict analytics view.
 *
 * Structural clone of components/spend/SpendPage.tsx (guardrail-analytics TASK.md §3
 * M8: "mirrors SpendPage's exact IA — PageHeader with window/group-by/key selects in
 * actions, a StatCard hero grid for totals OUTSIDE the tabs, a Recharts sparkline for
 * the time series, controlled Tabs (Overview/Breakdown) with a DataTable for the
 * active group_by breakdown").
 *
 * KEY LAYOUT CONTRACTS (mirrors SpendPage's own, enforced by tests):
 *
 * 1. data-testid="guardrail-analytics-hero" wraps the totals section (h2 + StatCards).
 * 2. totals StatCards, bucket <li> items, and GuardrailAnalyticsSparkline (data-testid=
 *    "guardrail-analytics-chart") are ALL rendered OUTSIDE the Tabs component so they
 *    persist in the DOM regardless of which tab is active.
 * 3. Tabs are CONTROLLED (value + onValueChange). Auto-switches to "breakdown" when
 *    groupBy is set and breakdown data arrives (React's "adjust state during render"
 *    escape hatch — same as SpendPage, not a useEffect).
 */

import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { Loading, ErrorState, Empty, StatCard, DataTable } from "@/components/ui";
import { GuardrailAnalyticsSparkline } from "./GuardrailAnalyticsSparkline";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";

type AnalyticsWindow = "day" | "week" | "month";
type GroupBy = "" | "guardrail" | "policy_source" | "key_id";

interface CountFields {
  evaluations: number;
  hits: number;
  blocked: number;
  masked: number;
  audited: number;
  passed: number;
  error: number;
  unchecked: number;
  budget_exceeded: number;
}

interface AnalyticsTotals extends CountFields {
  bucket_start: string;
  bucket_end: string;
}

interface AnalyticsBucket extends CountFields {
  bucket_start: string;
}

interface GuardrailBreakdownItem extends CountFields {
  guardrail: string;
}

interface PolicySourceBreakdownItem extends CountFields {
  policy_source: string;
}

interface KeyBreakdownItem extends CountFields {
  key_id: string;
}

type BreakdownItem = GuardrailBreakdownItem | PolicySourceBreakdownItem | KeyBreakdownItem;

interface AnalyticsResponse {
  window: AnalyticsWindow;
  bucket_size: AnalyticsWindow;
  totals: AnalyticsTotals;
  buckets: AnalyticsBucket[];
  breakdown: BreakdownItem[] | null;
}

interface KeyOpt {
  key_id: string;
  name: string;
  prefix?: string;
}

const GUARDRAIL_BREAKDOWN_COLUMNS: ColumnDef<GuardrailBreakdownItem>[] = [
  { accessorKey: "guardrail", header: "Guardrail" },
  { accessorKey: "evaluations", header: "Evaluations" },
  { accessorKey: "hits", header: "Hits" },
  { accessorKey: "blocked", header: "Blocked" },
  { accessorKey: "masked", header: "Masked" },
  { accessorKey: "audited", header: "Audited" },
];

const POLICY_SOURCE_BREAKDOWN_COLUMNS: ColumnDef<PolicySourceBreakdownItem>[] = [
  { accessorKey: "policy_source", header: "Policy source" },
  { accessorKey: "evaluations", header: "Evaluations" },
  { accessorKey: "hits", header: "Hits" },
  { accessorKey: "blocked", header: "Blocked" },
  { accessorKey: "masked", header: "Masked" },
  { accessorKey: "audited", header: "Audited" },
];

const KEY_BREAKDOWN_COLUMNS: ColumnDef<KeyBreakdownItem>[] = [
  { accessorKey: "key_id", header: "Key" },
  { accessorKey: "evaluations", header: "Evaluations" },
  { accessorKey: "hits", header: "Hits" },
  { accessorKey: "blocked", header: "Blocked" },
  { accessorKey: "masked", header: "Masked" },
  { accessorKey: "audited", header: "Audited" },
];

const BREAKDOWN_ARIA_LABEL: Record<Exclude<GroupBy, "">, string> = {
  guardrail: "Guardrail hits by pattern",
  policy_source: "Guardrail hits by policy",
  key_id: "Guardrail hits by key",
};

export function GuardrailAnalyticsPage() {
  const [analyticsWindow, setAnalyticsWindow] = useState<AnalyticsWindow>("month");
  const [groupBy, setGroupBy] = useState<GroupBy>("");
  const [keyId, setKeyId] = useState<string>("");

  // Controlled tab state. Derived via "adjust state during render" (same escape hatch
  // SpendPage uses) — NOT a useEffect — so react-hooks/set-state-in-effect stays clean.
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
  } = useQuery<AnalyticsResponse>({
    queryKey: ["admin-guardrails-analytics", analyticsWindow, groupBy, keyId],
    queryFn: () =>
      bffGet<AnalyticsResponse>(
        `/admin/guardrails/analytics?window=${analyticsWindow}` +
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

  const [lastGood, setLastGood] = useState<AnalyticsResponse | undefined>(undefined);
  if (!isError && data !== undefined && data !== lastGood) {
    setLastGood(data);
  }
  const viewData = isError ? lastGood : data;

  // "Adjust state during render" — auto-switch tab when breakdown data arrives or clears.
  if (groupBy !== "" && viewData?.breakdown != null && activeTab !== "breakdown") {
    setActiveTab("breakdown");
  }
  if (groupBy === "" && activeTab !== "overview") {
    setActiveTab("overview");
  }

  const isZeroState =
    !isLoading && !isError && viewData !== undefined && viewData.totals.evaluations === 0;

  const selects = (
    <div className="flex flex-wrap items-center gap-4">
      <div className="flex items-center gap-2">
        <label
          htmlFor="guardrail-analytics-window-selector"
          className="text-sm font-medium text-foreground"
        >
          Time window
        </label>
        <select
          id="guardrail-analytics-window-selector"
          data-testid="guardrail-analytics-window-selector"
          value={analyticsWindow}
          onChange={(e) => setAnalyticsWindow(e.target.value as AnalyticsWindow)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="day">day</option>
          <option value="week">week</option>
          <option value="month">month</option>
        </select>
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="guardrail-analytics-group-by-selector"
          className="text-sm font-medium text-foreground"
        >
          Group by
        </label>
        <select
          id="guardrail-analytics-group-by-selector"
          aria-label="Group by"
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as GroupBy)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">None</option>
          <option value="guardrail">Pattern</option>
          <option value="policy_source">Policy</option>
          <option value="key_id">Key</option>
        </select>
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="guardrail-analytics-key-filter-selector"
          className="text-sm font-medium text-foreground"
        >
          Filter by key
        </label>
        <select
          id="guardrail-analytics-key-filter-selector"
          aria-label="Filter by key"
          value={keyId}
          onChange={(e) => setKeyId(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">All keys</option>
          {(keys ?? []).map((k) => (
            <option key={k.key_id} value={k.key_id}>
              {k.name}
              {k.prefix ? ` (${k.prefix})` : ""}
            </option>
          ))}
        </select>
      </div>
    </div>
  );

  return (
    <div data-testid="guardrail-analytics-page" className="flex flex-col gap-6">
      <PageHeader
        title="Guardrail Analytics"
        description="Guardrail hit counts by pattern, policy, or key over time"
        actions={selects}
      />

      {isLoading && (
        <Loading
          label="Loading guardrail analytics"
          data-testid="guardrail-analytics-loading"
          className="animate-pulse"
        />
      )}

      {isError && !isLoading && (
        <div data-testid="guardrail-analytics-error">
          <ErrorState title={getErrorMessage(error)} />
        </div>
      )}

      {isZeroState && viewData !== undefined && (
        <div data-testid="guardrail-analytics-zero-state">
          <Empty
            title="No guardrail evaluations in this period"
            description={`${viewData.totals.evaluations} evaluations — ${viewData.totals.hits} hits`}
          />
        </div>
      )}

      {!isLoading && viewData !== undefined && !isZeroState && (
        <div data-testid="guardrail-analytics-data" className="flex flex-col gap-6">
          {/* GuardrailAnalyticsSparkline — OUTSIDE hero and tabs: always in DOM so
              guardrail-analytics-chart testid is accessible regardless of which tab
              is active. */}
          <GuardrailAnalyticsSparkline buckets={viewData.buckets} />

          {/* Hero = Totals section. */}
          <div data-testid="guardrail-analytics-hero" className="flex flex-col gap-3">
            <h2 className="text-lg font-medium text-foreground">
              Totals ({viewData.window})
            </h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatCard
                label="Evaluations"
                value={viewData.totals.evaluations}
                valueTestId="totals-evaluations"
              />
              <StatCard
                label="Hits"
                value={viewData.totals.hits}
                valueTestId="totals-hits"
              />
              <StatCard
                label="Blocked"
                value={viewData.totals.blocked}
                valueTestId="totals-blocked"
              />
              <StatCard
                label="Masked"
                value={viewData.totals.masked}
                valueTestId="totals-masked"
              />
            </div>
          </div>

          {/* Buckets list — OUTSIDE tabs: data-testid="guardrail-analytics-bucket" items
              must survive tab switches. */}
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
                      data-testid="guardrail-analytics-bucket"
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
              arrives. The breakdown tab holds the DataTable for the active group_by
              dimension; the overview tab is a lightweight placeholder. */}
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
                Select a group-by option to see a per-pattern, per-policy, or per-key
                breakdown.
              </p>
            </TabsContent>

            <TabsContent value="breakdown">
              <div className="flex flex-col gap-4">
                {!isError && viewData.breakdown != null && groupBy === "guardrail" && (
                  <DataTable
                    ariaLabel={BREAKDOWN_ARIA_LABEL.guardrail}
                    caption={BREAKDOWN_ARIA_LABEL.guardrail}
                    columns={GUARDRAIL_BREAKDOWN_COLUMNS}
                    data={viewData.breakdown as GuardrailBreakdownItem[]}
                  />
                )}

                {!isError && viewData.breakdown != null && groupBy === "policy_source" && (
                  <DataTable
                    ariaLabel={BREAKDOWN_ARIA_LABEL.policy_source}
                    caption={BREAKDOWN_ARIA_LABEL.policy_source}
                    columns={POLICY_SOURCE_BREAKDOWN_COLUMNS}
                    data={viewData.breakdown as PolicySourceBreakdownItem[]}
                  />
                )}

                {!isError && viewData.breakdown != null && groupBy === "key_id" && (
                  <DataTable
                    ariaLabel={BREAKDOWN_ARIA_LABEL.key_id}
                    caption={BREAKDOWN_ARIA_LABEL.key_id}
                    columns={KEY_BREAKDOWN_COLUMNS}
                    data={viewData.breakdown as KeyBreakdownItem[]}
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
