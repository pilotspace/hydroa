import { MarketingShell } from "@/components/marketing-shell";

/**
 * (marketing) route group layout — wraps all public marketing routes in MarketingShell.
 *
 * Contract (frozen §3 v1):
 *   - Public: NO cookie check, NO DashboardShell, NO authenticated fetch
 *   - Uses Hydroa design tokens from globals.css (inherited via root layout)
 *   - Provides header nav + footer via MarketingShell
 *   - WCAG-AA: skip-link, header/nav/main/footer landmarks, heading discipline
 *
 * The root layout (app/layout.tsx) is UNCHANGED — it provides Inter font, themeScript,
 * and Providers. This layout adds the public marketing chrome on top.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return <MarketingShell>{children}</MarketingShell>;
}
