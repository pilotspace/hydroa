import Link from "next/link";

export const metadata = { title: "Hydroa — AI Proxy for Enterprise Teams" };

/**
 * Public landing page — the new `/` entry point (formerly the authed redirect).
 *
 * Frozen contract (§3 v1): public, no cookie check, no redirect.
 * Full landing CONTENT is a separate later task (`landing-page`).
 * This shell ships the ROUTE SPLIT only — a minimal accessible placeholder.
 *
 * a11y: heading h1 + two CTAs (Log in / Sign up) pointing to the auth routes.
 */
export default function MarketingRootPage() {
  return (
    <main id="main">
      <section
        aria-labelledby="hero-heading"
        className="flex min-h-[60vh] flex-col items-center justify-center gap-8 px-4 py-24 text-center"
      >
        <h1
          id="hero-heading"
          className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl"
        >
          The AI Proxy Built for Enterprise Teams
        </h1>
        <p className="max-w-2xl text-lg text-muted-foreground">
          Hydroa routes, budgets, and monitors every AI request across your organisation
          — giving you control, cost visibility, and audit-ready logs.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Link
            href="/signup"
            className="inline-flex h-11 items-center justify-center rounded-md bg-primary px-8 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Get started
          </Link>
          <Link
            href="/login"
            className="inline-flex h-11 items-center justify-center rounded-md border border-border bg-background px-8 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Log in
          </Link>
        </div>
      </section>
    </main>
  );
}
