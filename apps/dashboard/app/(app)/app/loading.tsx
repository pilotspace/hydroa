import { Loading } from "@/components/ui/states";

/**
 * loading.tsx — Suspense fallback for the dashboard subtree (v50). Shown during
 * navigation/data loading so the shell never flashes blank. Reuses the v13
 * Loading primitive (role="status" + aria-busy).
 */
export default function DashboardLoading() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center p-10">
      <Loading label="Loading…" />
    </div>
  );
}
