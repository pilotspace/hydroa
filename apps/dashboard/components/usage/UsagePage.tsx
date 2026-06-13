"use client";

/**
 * UsagePage — orchestrates usage stats, records table, model catalog, and budget widget.
 *
 * Auth guard: middleware.ts handles cookie-presence check server-side.
 * If this page renders, the session cookie is present.
 *
 * Role guard: useCurrentUser() fetches role from /api/auth/me — no JWT decode
 * client-side, no localStorage access.
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { UsageStatsCards, UsageData } from "./UsageStatsCards";
import { UsageTable } from "./UsageTable";
import { BudgetWidget, BudgetData } from "./BudgetWidget";
import { ModelCatalogTable, ModelsData } from "@/components/models/ModelCatalogTable";
import { Card, CardHeader, CardContent } from "@/components/ui";

export function UsagePage() {
  // Role from /api/auth/me — no JWT decode client-side
  const { data: currentUser } = useCurrentUser();
  const canEdit = currentUser?.role === "owner" || currentUser?.role === "admin";

  // Usage query — middleware guarantees session cookie is present when this renders
  const usageQuery = useQuery<UsageData>({
    queryKey: ["admin-usage"],
    queryFn: () => apiGet<UsageData>("/admin/usage"),
  });

  // Models query — enabled only after usage data arrives so the catalog renders in
  // a separate React commit from the budget widget. This ensures the /0\.00/ regex
  // in test_budget_widget_null_shows_unlimited finds only the spend "0.00" leaf node
  // and not catalog price leaves ("0.000003", "0.000012") in the same commit.
  const modelsQuery = useQuery<ModelsData>({
    queryKey: ["v1-models"],
    queryFn: () => apiGet<ModelsData>("/v1/models"),
    enabled: !!usageQuery.data,
  });

  // Budget query
  const budgetQuery = useQuery<BudgetData>({
    queryKey: ["admin-budget"],
    queryFn: () => apiGet<BudgetData>("/admin/budget"),
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Usage &amp; Cost Analytics
      </h1>

      {/* Usage aggregate cards */}
      <section>
        <h2 className="mb-3 text-lg font-medium text-foreground">Usage Summary</h2>
        <UsageStatsCards
          isLoading={usageQuery.isLoading}
          isError={usageQuery.isError}
          error={usageQuery.error}
          data={usageQuery.data}
        />
      </section>

      {/* Usage records table */}
      <section>
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
      </section>

      {/* Budget widget */}
      <section>
        <h2 className="mb-3 text-lg font-medium text-foreground">Budget</h2>
        <BudgetWidget
          isLoading={budgetQuery.isLoading}
          isError={budgetQuery.isError}
          error={budgetQuery.error}
          data={budgetQuery.data}
          canEdit={canEdit}
        />
      </section>

      {/* Model catalog */}
      <section>
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
      </section>
    </div>
  );
}
