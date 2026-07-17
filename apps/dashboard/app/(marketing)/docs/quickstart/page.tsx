import { buildMetadata } from "@/lib/seo";
import { publicApiBaseUrl } from "@/lib/public-api-base-url";
import { QuickstartPanel } from "@/components/keys/QuickstartPanel";

export const metadata = buildMetadata({
  title: "Quickstart",
  description:
    "Point your OpenAI-compatible client at Hydroa and make your first proxied call — curl, Python, and JS examples.",
  path: "/docs/quickstart",
});

/**
 * Public /docs/quickstart page — activation-quickstart TASK.md §3 v1 #8, M9/R2.
 *
 * The ONE real content category carved out of docs/page.tsx's frozen §3 v1
 * "coming soon" scaffold, alongside the ai-act-compliance precedent — a
 * disclosed, scoped exception (the other 3 categories stay stubs).
 *
 * Frozen contract (§3 v1): PUBLIC, Server Component, no cookie, no client fetch.
 * Reuses the SAME QuickstartPanel component KeysPage mounts (M4) — never a
 * duplicated component — with a fixed, unambiguous placeholder key so no real
 * tenant secret is ever rendered on an unauthenticated page.
 */
const PLACEHOLDER_KEY = "sk-your-api-key";

export default function DocsQuickstartPage() {
  const baseUrl = publicApiBaseUrl();

  return (
    <main id="main">
      <section aria-labelledby="quickstart-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <h1 id="quickstart-heading" className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            Quickstart
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Hydroa speaks the OpenAI API shape — point your existing client at Hydroa&rsquo;s base URL
            with your own API key and start making calls. Sign up to get a real key; the examples below
            use a placeholder.
          </p>

          <div className="mt-10">
            <QuickstartPanel plaintextKey={PLACEHOLDER_KEY} baseUrl={baseUrl} />
          </div>
        </div>
      </section>
    </main>
  );
}
