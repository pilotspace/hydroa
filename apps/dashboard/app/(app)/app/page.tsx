import { OverviewPage } from "@/components/overview/OverviewPage";

export const metadata = { title: "Hydroa" };

/**
 * /app — the gated Overview landing for authenticated users.
 *
 * Route guard: proxy.ts intercepts /app and /app/:path* — absent ai_proxy_session
 * cookie → 307 /login before this renders. Gateway still validates the JWT on every
 * proxied call (UX gate only; auth unchanged per frozen contract §3).
 *
 * The DashboardShell wrapper comes from the (app)/layout.tsx shared layout,
 * so this page is a simple content leaf.
 */
export default function AppOverviewPage() {
  return <OverviewPage />;
}
