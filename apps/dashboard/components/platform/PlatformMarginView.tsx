"use client";

/**
 * PlatformMarginView — the margin-dashboard task's platform-console Margin page
 * (`/app/platform/margin`, TASK.md M12): summary tiles, a per-tenant/per-model
 * table, a trend chart, and a tie-out section against issued invoices.
 *
 * GET /admin/platform/margin/summary | /by-tenant-model | /trend | /tie-out
 * (CITED verbatim, margin-dashboard §3 CONTRACT — the DTO shapes are owned by the
 * already-shipped, frozen `margin_router.py`, confirmed by reading it directly
 * this session, not redefined here).
 *
 * Central honesty rule (M3), mirrored in this UI layer exactly as the backend
 * enforces it: a bucket with `has_provider_cost_data=false` NEVER renders a
 * dollar margin (never "$0.00", never a guessed figure) — it renders the
 * explicit "No cost data" badge instead. `formatMargin` is the ONE place this
 * task renders a margin value; every summary tile / table cell / trend point
 * routes through it so the rule cannot drift between call sites.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { LineChart, Line, CartesianGrid, XAxis, YAxis } from "recharts";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  StatCard,
  Loading,
  Empty,
  ErrorState,
} from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { ChartContainer } from "@/components/ui/chart";

export interface MarginSummaryResponse {
  window_from: string;
  window_to: string;
  provider_cost_total: string;
  billed_total: string;
  catalog_billed_total: string;
  margin: string | null;
  has_provider_cost_data: boolean;
  drift: string;
  unbilled_upstream_cost: string;
  unbilled_rows: number;
}

export interface TenantModelMarginItem {
  tenant_id: string;
  model_id: string;
  provider_cost_total: string;
  billed_total: string;
  catalog_billed_total: string;
  margin: string | null;
  has_provider_cost_data: boolean;
  unbilled_upstream_cost: string;
  unbilled_rows: number;
}

export interface ByTenantModelResponse {
  items: TenantModelMarginItem[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface MarginTrendPoint {
  bucket_start: string;
  provider_cost_total: string;
  billed_total: string;
  catalog_billed_total: string;
  margin: string | null;
  has_provider_cost_data: boolean;
}

export interface TrendResponse {
  granularity: string;
  points: MarginTrendPoint[];
}

export interface TieOutItem {
  tenant_id: string;
  invoice_id: string | null;
  invoice_status: "issued" | "pending_invoice";
  invoiced_total_usd: string | null;
  invoiced_raw_total_usd: string | null;
  ledger_billed_total_usd: string;
  provider_cost_total_usd: string;
  drift_invoiced_vs_ledger: string | null;
  tie_out_status: "matched" | "pending_invoice" | "drift_detected";
}

export interface TieOutResponse {
  period_start: string;
  period_end: string;
  items: TieOutItem[];
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

/** Every genuinely-known dollar amount renders through here — 2dp, thousands-free
 * (small operator-scale figures), tabular-nums applied by the caller's className. */
function formatUsd(value: string): string {
  return `$${Number(value).toFixed(2)}`;
}

/** THE single margin-rendering choke point (M3): `has_provider_cost_data=false` always
 * renders the "No cost data" badge — NEVER a dollar figure, never "$0.00", regardless of
 * what the `margin` field itself contains. */
function MarginValue({
  margin,
  hasProviderCostData,
  testId,
}: {
  margin: string | null;
  hasProviderCostData: boolean;
  testId?: string;
}) {
  if (!hasProviderCostData || margin === null) {
    return (
      <Badge variant="outline" data-testid={testId ? `${testId}-badge` : undefined}>
        No cost data
      </Badge>
    );
  }
  return (
    <span className="tabular-nums" data-testid={testId}>
      {formatUsd(margin)}
    </span>
  );
}

function currentPeriod(): string {
  const now = new Date();
  const month = String(now.getUTCMonth() + 1).padStart(2, "0");
  return `${now.getUTCFullYear()}-${month}`;
}

const TIE_OUT_STATUS_VARIANT: Record<TieOutItem["tie_out_status"], "success" | "warning" | "destructive"> = {
  matched: "success",
  pending_invoice: "warning",
  drift_detected: "destructive",
};

const TIE_OUT_STATUS_LABEL: Record<TieOutItem["tie_out_status"], string> = {
  matched: "Matched",
  pending_invoice: "Pending invoice",
  drift_detected: "Drift detected",
};

export function PlatformMarginView() {
  const period = useMemo(() => currentPeriod(), []);

  const summaryQuery = useQuery<MarginSummaryResponse>({
    queryKey: ["platform-margin-summary"],
    queryFn: () => bffGet<MarginSummaryResponse>("/admin/platform/margin/summary?window=month"),
    retry: false,
  });

  const byTenantModelQuery = useQuery<ByTenantModelResponse>({
    queryKey: ["platform-margin-by-tenant-model"],
    queryFn: () =>
      bffGet<ByTenantModelResponse>(
        "/admin/platform/margin/by-tenant-model?window=month&limit=50",
      ),
    retry: false,
  });

  const trendQuery = useQuery<TrendResponse>({
    queryKey: ["platform-margin-trend"],
    queryFn: () => bffGet<TrendResponse>("/admin/platform/margin/trend?window=month"),
    retry: false,
  });

  const tieOutQuery = useQuery<TieOutResponse>({
    queryKey: ["platform-margin-tie-out", period],
    queryFn: () =>
      bffGet<TieOutResponse>(`/admin/platform/margin/tie-out?period=${period}`),
    retry: false,
  });

  const queries = [summaryQuery, byTenantModelQuery, trendQuery, tieOutQuery];
  const isLoading = queries.some((q) => q.isLoading);
  const firstError = queries.find((q) => q.isError);

  const trendData = useMemo(
    () =>
      (trendQuery.data?.points ?? []).map((p) => ({
        bucket: p.bucket_start.slice(0, 10),
        billed: Number(p.billed_total),
        providerCost: Number(p.provider_cost_total),
      })),
    [trendQuery.data],
  );

  return (
    <section aria-labelledby="platform-margin-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Platform · Margin"
        titleId="platform-margin-heading"
        description="Provider cost vs billed vs invoiced — honest per-tenant, per-model margin, productizing the existing reconciliation substrate."
      />

      {isLoading && <Loading label="Loading margin data" className="animate-pulse" />}

      {!isLoading && firstError && (
        <ErrorState
          title={getErrorTitle(firstError.error)}
          onRetry={() => {
            for (const q of queries) void q.refetch();
          }}
        />
      )}

      {!isLoading && !firstError && (
        <>
          {/* Summary tiles */}
          {summaryQuery.data && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Billed (provider-basis)"
                value={<span className="tabular-nums">{formatUsd(summaryQuery.data.billed_total)}</span>}
              />
              <StatCard
                label="Provider cost"
                value={
                  <span className="tabular-nums">
                    {formatUsd(summaryQuery.data.provider_cost_total)}
                  </span>
                }
              />
              <StatCard
                label="Catalog billed"
                value={
                  <span className="tabular-nums">
                    {formatUsd(summaryQuery.data.catalog_billed_total)}
                  </span>
                }
              />
              <StatCard
                label="Margin"
                value={
                  <MarginValue
                    margin={summaryQuery.data.margin}
                    hasProviderCostData={summaryQuery.data.has_provider_cost_data}
                    testId="summary-margin-value"
                  />
                }
              />
            </div>
          )}

          {/* Per-tenant/per-model table */}
          <Card>
            <CardHeader>
              <CardTitle>Per-tenant, per-model margin</CardTitle>
            </CardHeader>
            <CardContent>
              {byTenantModelQuery.data && byTenantModelQuery.data.items.length === 0 ? (
                <Empty title="No usage in this window." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tenant</TableHead>
                      <TableHead>Model</TableHead>
                      <TableHead>Billed</TableHead>
                      <TableHead>Provider cost</TableHead>
                      <TableHead>Catalog billed</TableHead>
                      <TableHead>Margin</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byTenantModelQuery.data?.items.map((item) => (
                      <TableRow key={`${item.tenant_id}-${item.model_id}`}>
                        <TableCell className="font-mono text-xs">{item.tenant_id}</TableCell>
                        <TableCell>{item.model_id}</TableCell>
                        <TableCell className="tabular-nums">
                          {formatUsd(item.billed_total)}
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {formatUsd(item.provider_cost_total)}
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {formatUsd(item.catalog_billed_total)}
                        </TableCell>
                        <TableCell>
                          <MarginValue
                            margin={item.margin}
                            hasProviderCostData={item.has_provider_cost_data}
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {/* Trend */}
          <Card>
            <CardHeader>
              <CardTitle>Margin trend</CardTitle>
            </CardHeader>
            <CardContent>
              {trendData.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No data to show for the selected period.
                </p>
              ) : (
                <figure data-testid="margin-trend-chart" aria-label="Margin trend">
                  <figcaption className="sr-only">Billed vs provider cost trend over time</figcaption>
                  <ChartContainer
                    config={{
                      billed: { label: "Billed (USD)", color: "hsl(var(--color-chart-1))" },
                      providerCost: { label: "Provider cost (USD)", color: "hsl(var(--color-chart-2))" },
                    }}
                    className="aspect-auto"
                  >
                    <LineChart
                      width={640}
                      height={240}
                      data={trendData}
                      aria-hidden="true"
                      role="presentation"
                      accessibilityLayer={false}
                    >
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="bucket" />
                      <YAxis />
                      <Line type="monotone" dataKey="billed" isAnimationActive={false} dot={false} />
                      <Line
                        type="monotone"
                        dataKey="providerCost"
                        isAnimationActive={false}
                        dot={false}
                      />
                    </LineChart>
                  </ChartContainer>
                </figure>
              )}
            </CardContent>
          </Card>

          {/* Tie-out */}
          <Card>
            <CardHeader>
              <CardTitle>Invoice tie-out — {period}</CardTitle>
            </CardHeader>
            <CardContent>
              {tieOutQuery.data && tieOutQuery.data.items.length === 0 ? (
                <Empty title="No tenants with usage or an invoice this period." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tenant</TableHead>
                      <TableHead>Ledger billed</TableHead>
                      <TableHead>Invoiced</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tieOutQuery.data?.items.map((item) => (
                      <TableRow key={item.tenant_id}>
                        <TableCell className="font-mono text-xs">{item.tenant_id}</TableCell>
                        <TableCell className="tabular-nums">
                          {formatUsd(item.ledger_billed_total_usd)}
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {item.invoiced_total_usd !== null
                            ? formatUsd(item.invoiced_total_usd)
                            : "—"}
                        </TableCell>
                        <TableCell>
                          <Badge variant={TIE_OUT_STATUS_VARIANT[item.tie_out_status]}>
                            {TIE_OUT_STATUS_LABEL[item.tie_out_status]}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </section>
  );
}
