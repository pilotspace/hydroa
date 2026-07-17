/**
 * Focused, single-purpose layout for /activate (device-activate-page §3 A3).
 *
 * DELIBERATELY not the DashboardShell nav — the device-approval screen is a focused
 * consent surface reached from a headless app's instructions, so it renders a centered
 * card on a bare background. Placed directly under the (app) group (which adds no path
 * segment) so the route is the ROOT `/activate`, matching the RFC 8628 verification_uri,
 * WITHOUT inheriting the /app/* DashboardShell layout nested one level deeper.
 */
export default function ActivateLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      {children}
    </main>
  );
}
