import { AppShell } from "@/components/ui/app-shell";

/**
 * Layout for the (dashboard) route group — wraps the usage/spend/keys journeys in
 * the responsive, accessible AppShell (skip-link · Primary nav · main landmark).
 * Scoped to this group ON PURPOSE so the (auth) login/signup pages are NOT wrapped
 * in dashboard navigation. The root layout provides fonts + the QueryClient provider.
 */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
