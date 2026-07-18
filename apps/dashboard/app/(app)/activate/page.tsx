import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { ActivationCard } from "@/components/agent-activation/ActivationCard";
import { buildLoginBounceUrl } from "@/lib/bff-client";

export const metadata = { title: "Approve device access" };

/**
 * /activate — the authed device-approval screen (device-activate-page §3, M1).
 *
 * Own cookie guard (A3: focused screen, NOT under the proxy.ts /app matcher): an
 * unauthenticated visit round-trips through /login and RETURNS here with the same
 * user_code preserved via a VALIDATED same-origin `next` (buildLoginBounceUrl encodes
 * the return path; login re-validates it with sanitizeNext before redirecting). A
 * present cookie is a UX gate only — the gateway still authenticates every BFF call.
 */
export default async function ActivatePage({
  searchParams,
}: {
  searchParams: Promise<{ user_code?: string | string[] }>;
}) {
  const { user_code } = await searchParams;
  const code = Array.isArray(user_code) ? (user_code[0] ?? "") : (user_code ?? "");

  const cookieStore = await cookies();
  if (!cookieStore.has("ai_proxy_session")) {
    const returnTo = code ? `/activate?user_code=${encodeURIComponent(code)}` : "/activate";
    redirect(buildLoginBounceUrl(returnTo));
  }

  return <ActivationCard initialUserCode={code} />;
}
