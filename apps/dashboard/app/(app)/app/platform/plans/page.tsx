/**
 * /app/platform/plans — superadmin-only plan/tier catalog (Screen 1,
 * plan-admin-ui).
 *
 * A thin Server Component wrapper (mirrors platform/tenants/page.tsx
 * exactly): all data-fetching, loading/error/empty states, and the
 * superadmin-vs-other-role 403 surfacing live in `PlatformPlanCatalog`
 * itself. No client-side role gate is added here — the gateway's existing
 * `require_superadmin` dependency is the sole enforcement point (R1); a
 * direct URL hit by a non-superadmin surfaces the standard ErrorState, same
 * as every other admin surface in this dashboard.
 */

import { PlatformPlanCatalog } from "@/components/platform/PlatformPlanCatalog";

export default function PlatformPlansPage() {
  return <PlatformPlanCatalog />;
}
