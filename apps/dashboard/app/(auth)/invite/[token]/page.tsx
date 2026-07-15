import { AcceptInviteForm } from "@/components/auth/AcceptInviteForm";
import { AuthShell } from "@/components/ui";

export const metadata = { title: "Accept invite · Hydroa" };

/**
 * /invite/[token] — public (unauthenticated) invite-accept page. Lives in the
 * (auth) route group (URL is unaffected by the group) so it shares the same
 * AuthShell frame as /login and /signup. All fetch/accept/login logic lives in
 * AcceptInviteForm; this page only resolves the dynamic `token` param — Next.js
 * 15+ always passes `params` as a Promise (mirrors app/(auth)/login/page.tsx's
 * `searchParams` await).
 */
export default async function AcceptInvitePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  return (
    <AuthShell>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Join your team
      </h1>
      <AcceptInviteForm token={token} />
    </AuthShell>
  );
}
