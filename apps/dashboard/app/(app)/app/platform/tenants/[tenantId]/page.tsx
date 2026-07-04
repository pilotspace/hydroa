/**
 * /app/platform/tenants/[tenantId] — superadmin-only tenant-detail (Screen 2).
 *
 * The FIRST dynamic App Router segment in this dashboard (TASK.md §1 ⚠
 * Assumption #2 — no existing page precedent to mirror). Follows the same
 * Next.js 15+ Promise-based `params` convention already established by
 * app/api/gw/[...path]/route.ts (`await context.params`) — confirmed by
 * reading that file directly this session.
 *
 * A thin Server Component wrapper: all data-fetching, loading/error states,
 * and the superadmin-vs-other-role 403 surfacing live in `PlatformTenantDetail`
 * itself. No client-side role gate is added here — the gateway's existing
 * `require_superadmin` + `authorize_tenant_scope` dependencies are the sole
 * enforcement point (R5); a direct URL hit by a non-superadmin, or a
 * bad/deleted tenant_id, surfaces the standard ErrorState (R2/R5), same as
 * every other admin surface in this dashboard.
 */

import { PlatformTenantDetail } from "@/components/platform/PlatformTenantDetail";

interface PageProps {
  params: Promise<{ tenantId: string }>;
}

export default async function PlatformTenantDetailPage({ params }: PageProps) {
  const { tenantId } = await params;
  return <PlatformTenantDetail tenantId={tenantId} />;
}
