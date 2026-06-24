import type { ReactNode } from "react";

/**
 * LegalPage — shared presentational wrapper for the public legal routes
 * (terms/privacy/security). Frozen contract (§3 v1): PUBLIC, no cookie/fetch.
 *
 * Renders the <main id="main"> landmark, the single h1, a "Last updated" line,
 * a clearly-labelled TEMPLATE notice, and the policy body (h2 sections as children).
 */
export function LegalPage({
  title,
  lastUpdated,
  children,
}: {
  title: string;
  lastUpdated: string;
  children: ReactNode;
}) {
  return (
    <main id="main">
      <article className="mx-auto max-w-3xl px-4 py-20 sm:px-6 lg:px-8">
        <h1 className="text-4xl font-bold tracking-tight text-foreground">
          {title}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">
          Last updated {lastUpdated}
        </p>
        <p
          role="note"
          className="mt-6 rounded-md border border-border bg-muted/40 p-4 text-sm text-muted-foreground"
        >
          <strong className="text-foreground">Template notice:</strong> this is a
          placeholder policy scaffold and is <strong>not legal advice</strong>.
          Replace with counsel-reviewed text before public launch.
        </p>
        <div className="mt-10 space-y-10 text-muted-foreground">{children}</div>
      </article>
    </main>
  );
}

/** A titled prose section (h2 + body) used inside LegalPage. */
export function LegalSection({
  id,
  heading,
  children,
}: {
  id: string;
  heading: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="text-2xl font-semibold text-foreground">
        {heading}
      </h2>
      <div className="mt-3 space-y-3 leading-relaxed">{children}</div>
    </section>
  );
}
