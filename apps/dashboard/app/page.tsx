import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { DashboardShell } from "@/components/dashboard-shell";
import { OverviewPage } from "@/components/overview/OverviewPage";

export const metadata = { title: "Hydroa" };

/**
 * RootPage — the authenticated `/` landing. Mirrors proxy.ts's cookie-presence rule
 * server-side: no `ai_proxy_session` cookie ⇒ redirect to /login; otherwise render the
 * Overview inside the branded dashboard shell. The gateway still validates the JWT on every
 * proxied read, so this is a UX gate only (no auth weakening; proxy.ts is unchanged).
 */
export default async function RootPage() {
  const jar = await cookies();
  if (!jar.get("ai_proxy_session")) {
    redirect("/login");
  }
  return (
    <DashboardShell>
      <OverviewPage />
    </DashboardShell>
  );
}
