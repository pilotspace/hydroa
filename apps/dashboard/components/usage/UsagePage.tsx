"use client";

/**
 * UsagePage — orchestrates usage stats, records table, model catalog, and budget widget.
 *
 * Auth guard: middleware.ts handles cookie-presence check server-side.
 * Role guard: useCurrentUser() fetches role from /api/auth/me — no JWT decode client-side.
 *
 * Redesign (monitoring §3 v1): PageHeader + hero + Tabs (Overview / Records / Catalog / Trends)
 *
 * CRITICAL layout contract (enforced by tests):
 *   - BudgetWidget is rendered OUTSIDE the tab panel so its "Edit Budget" button
 *     stays in the DOM even when the Records/Catalog/Trends tab is active.
 *     (usage.test.tsx checks for the button after clicking the Records tab.)
 *   - The hero region owns total_cost_usd — the Overview stat cards show only
 *     requests/prompt/completion tokens so cost never appears twice in the DOM
 *     (which would break getByText queries expecting a single match).
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { UsageData } from "./UsageStatsCards";
import { UsageTable } from "./UsageTable";
import { UsageTrend } from "./UsageTrend";
import { BudgetWidget, BudgetData } from "./BudgetWidget";
import { ModelCatalogTable, ModelsData } from "@/components/models/ModelCatalogTable";
import { Card, CardHeader, CardContent, Loading, ErrorState, StatCard } from "@/components/ui";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { BffError } from "@/lib/bff-client";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function UsagePage() {
  // Role from /api/auth/me — no JWT decode client-side
  const { data: currentUser } = useCurrentUser();
  const canEdit = currentUser?.role === "owner" || currentUser?.role === "admin";

  // Usage query — middleware guarantees session cookie is present when this renders
  const usageQuery = useQuery<UsageData>({
    queryKey: ["admin-usage"],
    queryFn: () => bffGet<UsageData>("/admin/usage"),
  });

  // Models query — reads the catalog over the JWT/control-plane twin
  // GET /admin/catalog/models, NOT the data-plane GET /v1/models.
  const modelsQuery = useQuery<ModelsData>({
    queryKey: ["catalog-models"],
    queryFn: () => bffGet<ModelsData>("/admin/catalog/models"),
    enabled: !!usageQuery.data,
  });

  // Budget query
  const budgetQuery = useQuery<BudgetData>({
    queryKey: ["admin-budget"],
    queryFn: () => bffGet<BudgetData>("/admin/budget"),
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Usage & Cost Analytics"
        description="Track spend, tokens, and budget across your tenant"
      />

      {/* Hero region — headline total cost. This is the SOLE place total_cost_usd
          is rendered as text, so getByText(/cost/) finds exactly one match. */}
      {usageQuery.data && (
        <div
          data-testid="usage-hero"
          className="rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Total Cost (USD)
          </p>
          <p className="text-3xl font-semibold text-foreground">
            {usageQuery.data.total_cost_usd}
          </p>
        </div>
      )}

      {/* BudgetWidget — OUTSIDE tabs so "Edit Budget" button stays in the DOM
          regardless of which tab is active. usage.test.tsx checks for the button
          after clicking the Records tab; TabsContent returns null for inactive
          panels, so placing it inside any TabsContent would hide it. */}
      <BudgetWidget
        isLoading={budgetQuery.isLoading}
        isError={budgetQuery.isError}
        error={budgetQuery.error}
        data={budgetQuery.data}
        canEdit={canEdit}
      />

      <Tabs defaultValue="overview" className="flex flex-col gap-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="records">Records</TabsTrigger>
          <TabsTrigger value="catalog">Catalog</TabsTrigger>
          <TabsTrigger value="trends">Trends</TabsTrigger>
        </TabsList>

        {/* Overview: aggregate stats (requests/tokens only, NOT cost — hero owns cost).
            Records (and their model IDs) live on the Records tab; the catalog on the
            Catalog tab. Tests that assert a record/model id navigate to that tab first. */}
        <TabsContent value="overview">
          <div className="flex flex-col gap-6">
            <section>
              <h2 className="mb-3 text-lg font-medium text-foreground">Usage Summary</h2>
              {usageQuery.isLoading && (
                <Loading
                  label="Loading usage statistics"
                  data-testid="loading"
                  className="animate-pulse"
                />
              )}
              {usageQuery.isError && (
                <ErrorState title={getErrorTitle(usageQuery.error)} />
              )}
              {usageQuery.data && (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <StatCard
                    label="Total Requests"
                    value={String(usageQuery.data.total_requests)}
                  />
                  <StatCard
                    label="Total Prompt Tokens"
                    value={String(usageQuery.data.total_prompt_tokens)}
                  />
                  <StatCard
                    label="Total Completion Tokens"
                    value={String(usageQuery.data.total_completion_tokens)}
                  />
                </div>
              )}
            </section>
          </div>
        </TabsContent>

        {/* Records: usage table */}
        <TabsContent value="records">
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold leading-none tracking-tight text-foreground">
                Usage Records
              </h2>
            </CardHeader>
            <CardContent>
              <UsageTable
                isLoading={usageQuery.isLoading}
                isError={usageQuery.isError}
                data={usageQuery.data}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Catalog: model catalog */}
        <TabsContent value="catalog">
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold leading-none tracking-tight text-foreground">
                Model Catalog
              </h2>
            </CardHeader>
            <CardContent>
              <ModelCatalogTable
                isLoading={modelsQuery.isLoading}
                isError={modelsQuery.isError}
                error={modelsQuery.error}
                data={modelsQuery.data}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Trends: per-day cost chart */}
        <TabsContent value="trends">
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold leading-none tracking-tight text-foreground">
                Cost Trend
              </h2>
            </CardHeader>
            <CardContent>
              {usageQuery.data ? (
                <UsageTrend records={usageQuery.data.records} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  No data to show for the selected period.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
