import { LoginForm } from "@/components/auth/LoginForm";
import { loginNextTarget } from "@/lib/bff-client";
import { AuthShell, ErrorState } from "@/components/ui";

export const metadata = { title: "Hydroa" };

/**
 * Login page. When the pre-auth OIDC callback relay bounces here with
 * `?sso_error=<hint>` (any SSO failure), surface a GENERIC alert — the raw
 * gateway hint is never shown to the user (it is only a log-safe enum token).
 *
 * A `?next=<same-origin relative>` (e.g. from the /activate cookie-guard bounce) is
 * VALIDATED here through loginNextTarget — an off-origin / scheme / `//host` value is
 * ignored and login falls back to the default destination (open-redirect guard, R-D).
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ sso_error?: string | string[]; next?: string | string[] }>;
}) {
  const { sso_error, next } = await searchParams;
  const ssoFailed = sso_error !== undefined;
  const rawNext = Array.isArray(next) ? next[0] : next;
  const nextPath = loginNextTarget(rawNext);

  return (
    <AuthShell>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Sign in to your account
      </h1>
      {ssoFailed ? (
        <ErrorState
          title="Single sign-on failed. Please try again or contact your administrator."
        />
      ) : null}
      <LoginForm nextPath={nextPath} />
    </AuthShell>
  );
}
