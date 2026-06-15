import { DashboardShell } from "@/components/dashboard-shell";

/**
 * Layout for the (dashboard) route group — wraps the usage/spend/keys journeys in
 * the responsive, accessible AppShell (skip-link · Primary nav · main landmark).
 * Scoped to this group ON PURPOSE so the (auth) login/signup pages are NOT wrapped
 * in dashboard navigation. The root layout provides fonts + the QueryClient provider.
 *
 * DashboardShell (client) feeds the current user's role into AppShell so a `member`
 * does not see admin-only nav links (UX-only; the gateway still enforces RBAC).
 */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <DashboardShell>{children}</DashboardShell>;
}
