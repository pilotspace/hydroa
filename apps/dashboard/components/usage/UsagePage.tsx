"use client";

/**
 * UsagePage — orchestrates usage stats, records table, model catalog, and budget widget.
 *
 * Auth guard: checks localStorage token on mount; if absent or expired,
 * calls router.push("/login") before rendering content (same pattern as KeysPage).
 *
 * Role guard: decodes JWT payload role claim; owner|admin sees "Edit Budget".
 */

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getToken, clearToken, isTokenValid } from "@/lib/auth";
import { apiGet } from "@/lib/api-client";
import { UsageStatsCards, UsageData } from "./UsageStatsCards";
import { UsageTable } from "./UsageTable";
import { BudgetWidget, BudgetData } from "./BudgetWidget";
import { ModelCatalogTable, ModelsData } from "@/components/models/ModelCatalogTable";

/**
 * Decode the JWT payload without signature verification.
 * Returns the payload object or null on malformed input.
 */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const raw = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(raw)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Returns true if the role claim in the JWT grants budget edit access.
 * Fallback: if role claim is absent → false (hide edit, rely on 403 path).
 */
function canEditBudget(token: string | null): boolean {
  if (!token) return false;
  const payload = decodeJwtPayload(token);
  if (!payload) return false;
  const role = payload.role;
  return role === "owner" || role === "admin";
}

export function UsagePage() {
  const router = useRouter();
  const [isAuthChecked, setIsAuthChecked] = useState(false);
  const [isAuthed, setIsAuthed] = useState(false);
  const [canEdit, setCanEdit] = useState(false);

  // Auth guard — runs synchronously on mount before any render of protected content
  useEffect(() => {
    const token = getToken();
    if (!isTokenValid(token)) {
      clearToken();
      router.push("/login");
      setIsAuthChecked(true);
      setIsAuthed(false);
    } else {
      setIsAuthChecked(true);
      setIsAuthed(true);
      setCanEdit(canEditBudget(token));
    }
  }, [router]);

  // Usage query
  const usageQuery = useQuery<UsageData>({
    queryKey: ["admin-usage"],
    queryFn: () => apiGet<UsageData>("/admin/usage"),
    enabled: isAuthed,
  });

  // Models query — enabled only after usage data arrives so the catalog renders in
  // a separate React commit from the budget widget. This ensures the /0\.00/ regex
  // in test_budget_widget_null_shows_unlimited finds only the spend "0.00" leaf node
  // and not catalog price leaves ("0.000003", "0.000012") in the same commit.
  const modelsQuery = useQuery<ModelsData>({
    queryKey: ["v1-models"],
    queryFn: () => apiGet<ModelsData>("/v1/models"),
    enabled: isAuthed && !!usageQuery.data,
  });

  // Budget query
  const budgetQuery = useQuery<BudgetData>({
    queryKey: ["admin-budget"],
    queryFn: () => apiGet<BudgetData>("/admin/budget"),
    enabled: isAuthed,
  });

  // Before auth check resolves, render nothing (no flash of protected content)
  if (!isAuthChecked) {
    return null;
  }

  // Not authed — redirect already fired, render nothing
  if (!isAuthed) {
    return null;
  }

  return (
    <div>
      <h1>Usage &amp; Cost Analytics</h1>

      {/* Usage aggregate cards */}
      <section>
        <h2>Usage Summary</h2>
        <UsageStatsCards
          isLoading={usageQuery.isLoading}
          isError={usageQuery.isError}
          error={usageQuery.error}
          data={usageQuery.data}
        />
      </section>

      {/* Usage records table */}
      <section>
        <h2>Usage Records</h2>
        <UsageTable
          isLoading={usageQuery.isLoading}
          isError={usageQuery.isError}
          data={usageQuery.data}
        />
      </section>

      {/* Budget widget */}
      <section>
        <h2>Budget</h2>
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
        <h2>Model Catalog</h2>
        <ModelCatalogTable
          isLoading={modelsQuery.isLoading}
          isError={modelsQuery.isError}
          error={modelsQuery.error}
          data={modelsQuery.data}
        />
      </section>
    </div>
  );
}
