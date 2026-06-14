import { LoginForm } from "@/components/auth/LoginForm";
import { ErrorState } from "@/components/ui";

export const metadata = { title: "Hydroa" };

/**
 * Login page. When the pre-auth OIDC callback relay bounces here with
 * `?sso_error=<hint>` (any SSO failure), surface a GENERIC alert — the raw
 * gateway hint is never shown to the user (it is only a log-safe enum token).
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ sso_error?: string | string[] }>;
}) {
  const { sso_error } = await searchParams;
  const ssoFailed = sso_error !== undefined;

  return (
    <main>
      <h1>Sign in to your account</h1>
      {ssoFailed ? (
        <ErrorState
          title="Single sign-on failed. Please try again or contact your administrator."
        />
      ) : null}
      <LoginForm />
    </main>
  );
}
