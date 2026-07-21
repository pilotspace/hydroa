import { SignupForm } from "@/components/auth/SignupForm";
import { AuthShell } from "@/components/ui";

/**
 * Signup page.
 *
 * SignupForm reads `?account_type=` / `?email=` via useSearchParams (the one-shot
 * intent seeds from the marketing CTAs and from /login's "Create a workspace"
 * link). A client `useSearchParams()` under a PRERENDERED route triggers Next's
 * CSR-bailout error and fails `next build` outright — which is exactly what
 * happened once those seeds landed, and which no unit test can catch because
 * vitest renders the component directly and never runs Next's prerender.
 *
 * Awaiting `searchParams` here opts the route into dynamic rendering, which
 * resolves it. This deliberately mirrors the sibling /login page's shipped
 * shape rather than inventing a second pattern.
 *
 * A Suspense boundary would also satisfy the build, but it was REJECTED: it
 * leaves the route static and defers the whole form — including the
 * unconditional alt-routes panel that is this milestone's no-dead-end
 * guarantee — to client-only rendering, so a visitor sees an empty card until
 * JS hydrates. Dynamic rendering keeps the panel in the server HTML.
 */
export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ account_type?: string | string[]; email?: string | string[] }>;
}) {
  await searchParams;

  return (
    <AuthShell>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Create your account
      </h1>
      <SignupForm />
    </AuthShell>
  );
}
