"use client";

/**
 * OverviewPage — the v23 `/` landing. Aggregates the EXISTING reads (no new BFF route):
 *   GET /admin/spend?window={day|week|month}  → KPI totals + the usage-over-time series
 *   GET /admin/usage                          → the recent-activity records
 *   GET /admin/budget                         → the monthly-spend KPI
 * into ≥4 KPI StatCards (trend deltas), a ChartCard with a day/week/month range toggle, and a
 * recent-activity DataTable. Presentation-only — the data seam is byte-identical.
 *
 * Trend deltas are derived client-side (latest bucket vs previous bucket); with <2 buckets they
 * honestly degrade to neutral "—" (no period-over-period field exists in the reads).
 */

import * as React from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Activity, CreditCard, Coins, Wallet } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, XAxis, YAxis } from "recharts";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet } from "@/lib/bff-client";
import { cn } from "@/lib/cn";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ChartCard,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  DataTable,
  StatCard,
  type ChartConfig,
  type StatDelta,
} from "@/components/ui";

type SpendWindow = "day" | "week" | "month";

interface SpendBucket {
  bucket_start: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
}
interface SpendTotals {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
}
interface SpendWindowResponse {
  window: SpendWindow;
  totals: SpendTotals;
  buckets: SpendBucket[];
}
interface UsageRecord {
  id: string;
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  status: number;
  created_at: string;
}
interface UsageData {
  total_cost_usd: string;
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  records: UsageRecord[];
}
interface BudgetData {
  budget_usd_monthly: string | null;
  spent_usd_month: string;
}

const RANGES: SpendWindow[] = ["day", "week", "month"];
const MAX_ROWS = 10;
const num = (n: number) => n.toLocaleString("en-US");

/**
 * Latest-bucket vs previous-bucket trend, per the frozen §3 formula:
 *   pct = prev>0 ? (latest-prev)/prev*100 : 0 ;  direction = pct>0 up | pct<0 down | else neutral.
 * Degrades to neutral "—" when there is nothing to compare (<2 buckets OR a zero prior) — never a
 * fabricated percentage.
 */
function trendDelta(buckets: SpendBucket[], pick: (b: SpendBucket) => number): StatDelta {
  if (buckets.length < 2) return { direction: "neutral", text: "—" };
  const latest = pick(buckets[buckets.length - 1]);
  const prev = pick(buckets[buckets.length - 2]);
  const pct = prev > 0 ? ((latest - prev) / prev) * 100 : 0;
  if (pct === 0) return { direction: "neutral", text: "—" };
  const direction: StatDelta["direction"] = pct > 0 ? "up" : "down";
  const sign = pct > 0 ? "+" : "";
  return { direction, text: `${sign}${pct.toFixed(1)}% vs prev` };
}

const ACTIVITY_COLUMNS: ColumnDef<UsageRecord>[] = [
  { accessorKey: "model_id", header: "Model" },
  {
    id: "tokens",
    header: "Tokens",
    accessorFn: (r) => r.prompt_tokens + r.completion_tokens,
    cell: (info) => num(info.getValue() as number),
  },
  { accessorKey: "cost_usd", header: "Cost", cell: (info) => `$${info.getValue() as string}` },
  { accessorKey: "status", header: "Status" },
  {
    accessorKey: "created_at",
    header: "Time",
    cell: (info) => new Date(info.getValue() as string).toLocaleString("en-US"),
  },
];

const CHART_CONFIG: ChartConfig = { requests: { label: "Requests", color: "var(--color-chart-1)" } };

export function OverviewPage() {
  const [range, setRange] = React.useState<SpendWindow>("month");

  const spendQuery = useQuery<SpendWindowResponse>({
    queryKey: ["overview-spend", range],
    queryFn: () => bffGet<SpendWindowResponse>(`/admin/spend?window=${range}`),
    placeholderData: keepPreviousData,
  });
  const usageQuery = useQuery<UsageData>({
    queryKey: ["admin-usage"],
    queryFn: () => bffGet<UsageData>("/admin/usage"),
  });
  const budgetQuery = useQuery<BudgetData>({
    queryKey: ["admin-budget"],
    queryFn: () => bffGet<BudgetData>("/admin/budget"),
  });

  const isLoading = spendQuery.isLoading || usageQuery.isLoading || budgetQuery.isLoading;
  const isError = spendQuery.isError || usageQuery.isError || budgetQuery.isError;

  if (isLoading) {
    return (
      <div role="status" aria-busy="true" className="flex min-h-64 items-center justify-center text-muted-foreground">
        <span className="size-5 animate-spin rounded-full border-2 border-muted border-t-foreground" aria-hidden="true" />
        <span className="sr-only">Loading overview…</span>
      </div>
    );
  }
  if (isError) {
    return (
      <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        Failed to load overview metrics. Please try again.
      </div>
    );
  }

  const totals = spendQuery.data?.totals;
  const buckets = spendQuery.data?.buckets ?? [];
  const usage = usageQuery.data;
  const budget = budgetQuery.data;

  const tokens = (totals?.prompt_tokens ?? 0) + (totals?.completion_tokens ?? 0);
  const chartData = buckets.map((b) => ({ label: b.bucket_start, requests: b.requests }));
  const budgetCeiling = budget?.budget_usd_monthly === null ? "Unlimited" : `$${budget?.budget_usd_monthly}`;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
          <p className="text-sm text-muted-foreground">Usage, cost, and recent activity at a glance.</p>
        </div>
        <div role="group" aria-label="Select time range" className="inline-flex rounded-md border border-border p-0.5">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              aria-pressed={range === r}
              onClick={() => setRange(r)}
              className={cn(
                "rounded px-3 py-1.5 text-sm font-medium capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                range === r
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <section aria-label="Key metrics" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total Requests"
          value={num(totals?.requests ?? 0)}
          delta={trendDelta(buckets, (b) => b.requests)}
          icon={<Activity className="size-4" />}
        />
        <StatCard
          label="Total Cost"
          value={`$${totals?.cost_usd ?? "0.00"}`}
          delta={trendDelta(buckets, (b) => Number(b.cost_usd))}
          icon={<CreditCard className="size-4" />}
        />
        <StatCard
          label="Total Tokens"
          value={num(tokens)}
          delta={trendDelta(buckets, (b) => b.prompt_tokens + b.completion_tokens)}
          icon={<Coins className="size-4" />}
        />
        <StatCard
          label="Monthly Spend"
          value={`$${budget?.spent_usd_month ?? "0.00"}`}
          delta={{ direction: "neutral", text: `of ${budgetCeiling}` }}
          icon={<Wallet className="size-4" />}
          footer="Current billing month"
        />
      </section>

      <ChartCard
        title="Usage over time"
        description={`Requests per ${range}`}
        config={CHART_CONFIG}
        headingLevel={2}
      >
        <ChartContainer config={CHART_CONFIG}>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} className="stroke-border" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} className="text-xs" />
              <YAxis tickLine={false} axisLine={false} width={40} className="text-xs" />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Area
                type="monotone"
                dataKey="requests"
                stroke="var(--color-requests)"
                fill="var(--color-requests)"
                fillOpacity={0.15}
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartContainer>
      </ChartCard>

      <Card>
        <CardHeader>
          <CardTitle asChild>
            <h2>Recent activity</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={ACTIVITY_COLUMNS}
            data={(usage?.records ?? []).slice(0, MAX_ROWS)}
            caption="Most recent requests"
            emptyMessage="No recent activity"
          />
        </CardContent>
      </Card>
    </div>
  );
}
