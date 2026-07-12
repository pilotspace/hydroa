/**
 * /app/platform/margin — superadmin-only operator margin dashboard (margin-dashboard
 * TASK.md M12).
 *
 * A thin Server Component wrapper (mirrors platform/plans/page.tsx exactly): all
 * data-fetching, loading/error/empty states, and the superadmin-vs-other-role 403
 * surfacing live in `PlatformMarginView` itself. No client-side role gate is added
 * here — the gateway's existing `require_superadmin` dependency (on every one of the
 * 4 GET /admin/platform/margin/* routes) is the sole enforcement point; a direct URL
 * hit by a non-superadmin surfaces the standard ErrorState, same as every other admin
 * surface in this dashboard.
 */

import { PlatformMarginView } from "@/components/platform/PlatformMarginView";

export default function PlatformMarginPage() {
  return <PlatformMarginView />;
}
