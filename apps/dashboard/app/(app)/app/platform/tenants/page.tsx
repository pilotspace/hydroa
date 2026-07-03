/**
 * /app/platform/tenants — superadmin-only cross-tenant directory (Screen 1).
 *
 * A thin Server Component wrapper (mirrors the other `(app)/app/*` route files):
 * all data-fetching, loading/error/empty states, and the superadmin-vs-other-role
 * 403 surfacing live in `PlatformTenantDirectory` itself. No client-side role gate
 * is added here — the gateway's existing `require_superadmin` dependency is the
 * sole enforcement point (R5); a direct URL hit by a non-superadmin surfaces the
 * standard ErrorState, same as every other admin surface in this dashboard.
 */

import { PlatformTenantDirectory } from "@/components/platform/PlatformTenantDirectory";

export default function PlatformTenantsPage() {
  return <PlatformTenantDirectory />;
}
