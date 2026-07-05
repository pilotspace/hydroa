"use client";

/**
 * PlatformTenantDetail — Screen 2 of the admin-console-ui task: the
 * superadmin-only tenant-detail shell (/app/platform/tenants/[tenantId]).
 *
 * DOM order (M4, M11): (1) the safety banner, (2) a PageHeader whose <h1> is
 * the target tenant's FULL, un-truncated name, (3) a Tabs (Config · Budget ·
 * Keys · Members), each TabsContent mounting an independent panel with its OWN
 * useQuery — a failure in one tab's query never blocks another (R3, mirrors
 * SettingsPage.tsx's per-tab lazy-query shape).
 *
 * GET /admin/platform/tenants/{tenantId} -> TenantSummaryResponse
 * {id, name, kind, created_at} — a 404/403 renders a FULL-PAGE ErrorState;
 * nothing else (banner, tabs, panels) renders alongside it (R2, R5) — acting
 * on an unresolved tenant identity is the one thing this console must prevent.
 *
 * Tab state is URL state (M12): `?tab=config|budget|keys|members`, default
 * `config` on absent/unrecognized value. Seeded on mount via a lazy
 * useState initializer (so a deep link activates the right tab on FIRST
 * render, not after a click) and re-synced by an effect for real browser
 * back/forward navigation. Every switch is a router.replace (no history-stack
 * entry per click).
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PlatformSafetyBanner } from "./PlatformSafetyBanner";
import { PlatformConfigTab } from "./PlatformConfigTab";
import { PlatformBudgetTab } from "./PlatformBudgetTab";
import { PlatformKeysTab } from "./PlatformKeysTab";
import { PlatformMembersTab } from "./PlatformMembersTab";
import { PlatformPlanTab } from "./PlatformPlanTab";

export interface PlatformTenantSummary {
  id: string;
  name: string;
  kind: string;
  created_at: string;
}

const TAB_VALUES = ["config", "budget", "keys", "members", "plan"] as const;
type TabValue = (typeof TAB_VALUES)[number];

function isTabValue(v: string | null): v is TabValue {
  return v !== null && (TAB_VALUES as readonly string[]).includes(v);
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export interface PlatformTenantDetailProps {
  tenantId: string;
}

export function PlatformTenantDetail({ tenantId }: PlatformTenantDetailProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [activeTab, setActiveTab] = React.useState<TabValue>(() => {
    const fromUrl = searchParams.get("tab");
    return isTabValue(fromUrl) ? fromUrl : "config";
  });

  // Re-sync on real navigation (browser back/forward changing ?tab=) via the
  // "adjust state during render" pattern (React docs: "Storing information
  // from previous renders") rather than a setState-in-effect — mirrors
  // CacheSettings.tsx/GuardrailSettings.tsx's own seed/reseed-from-external-
  // source convention already used elsewhere in this codebase. A no-op in
  // tests, since the mocked useSearchParams returns a fixed reference for the
  // lifetime of one render tree.
  const [prevSearchParams, setPrevSearchParams] = React.useState(searchParams);
  if (searchParams !== prevSearchParams) {
    setPrevSearchParams(searchParams);
    const fromUrl = searchParams.get("tab");
    setActiveTab(isTabValue(fromUrl) ? fromUrl : "config");
  }

  const {
    data: tenant,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<PlatformTenantSummary>({
    queryKey: ["platform-tenant", tenantId],
    queryFn: () => bffGet<PlatformTenantSummary>(`/admin/platform/tenants/${tenantId}`),
    retry: false,
  });

  function handleTabChange(next: string) {
    if (!isTabValue(next)) return;
    setActiveTab(next);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`?${params.toString()}`);
  }

  if (isLoading) {
    return <Loading label="Loading tenant" className="animate-pulse" />;
  }

  if (isError || !tenant) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }

  return (
    <div className="flex flex-col gap-6">
      <PlatformSafetyBanner tenantName={tenant.name} kind={tenant.kind} />

      <PageHeader title={tenant.name} titleId="platform-tenant-detail-heading" />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="budget">Budget</TabsTrigger>
          <TabsTrigger value="keys">Keys</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
        </TabsList>

        <TabsContent value="config">
          <PlatformConfigTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="budget">
          <PlatformBudgetTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="keys">
          <PlatformKeysTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="members">
          <PlatformMembersTab tenantId={tenantId} tenantKind={tenant.kind} />
        </TabsContent>
        <TabsContent value="plan">
          <PlatformPlanTab tenantId={tenantId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
