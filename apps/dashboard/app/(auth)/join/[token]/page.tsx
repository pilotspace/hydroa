import { JoinByDomainForm } from "@/components/auth/JoinByDomainForm";
import { AuthShell } from "@/components/ui";

export const metadata = { title: "Join your team · Hydroa" };

/**
 * /join/[token] — public (unauthenticated) domain-invite-link redeem page.
 * Lives in the (auth) route group (URL is unaffected by the group) so it
 * shares the same AuthShell frame as /login, /signup, and /invite/[token].
 * All fetch/redeem/verify/auto-login logic lives in JoinByDomainForm; this
 * page only resolves the dynamic `token` param — Next.js 15+ always passes
 * `params` as a Promise (mirrors app/(auth)/invite/[token]/page.tsx).
 */
export default async function JoinByDomainPage({
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
      <JoinByDomainForm token={token} />
    </AuthShell>
  );
}
