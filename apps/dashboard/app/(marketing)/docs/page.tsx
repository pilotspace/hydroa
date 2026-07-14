import Link from "next/link";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "Documentation",
  description:
    "Get started with Hydroa — authenticate, route requests across providers, and manage keys, budgets, and rate limits via the OpenAI-compatible API.",
  path: "/docs",
});

/**
 * Public /docs index scaffold — the landing #docs teaser links here.
 * Frozen contract (§3 v1): PUBLIC, no cookie/fetch. STRUCTURE only — full doc
 * content + an MDX pipeline are deferred (SPEC delta). Categories link to
 * "coming soon" anchors so nav resolves without dangling.
 */
const CATEGORIES: Array<{ title: string; blurb: string; href?: string }> = [
  { title: "Quickstart", blurb: "Point your OpenAI-compatible client at Hydroa and make your first proxied call." },
  { title: "Providers", blurb: "Route to OpenAI, Anthropic, Gemini, Bedrock, and Azure with fallback and load balancing." },
  { title: "Admin API", blurb: "Manage keys, routing config, budgets, and tenants programmatically." },
  { title: "BYOK", blurb: "Bring your own provider keys, encrypted at rest per tenant." },
  // ai-act-marketing-page TASK.md §3 v1 M9: the ONE real content category
  // carved out of this page's frozen "coming soon" scaffold (disclosed,
  // scoped exception — the 4 categories above are untouched stubs).
  {
    title: "AI Act readiness",
    blurb: "Audit-readiness support for the EU AI Act's Art. 101 GPAI regime — residency, ZDR, and Art. 12 record-keeping.",
    href: "/docs/ai-act-compliance",
  },
];

export default function DocsPage() {
  return (
    <main id="main">
      <section aria-labelledby="docs-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <h1 id="docs-heading" className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            Documentation
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Guides and references for integrating and operating Hydroa. Full content is on its way.
          </p>
        </div>
        <div className="mx-auto mt-14 grid max-w-5xl grid-cols-1 gap-6 sm:grid-cols-2">
          {CATEGORIES.map((c) => (
            <Card key={c.title}>
              <CardHeader>
                <CardTitle asChild className="text-xl">
                  <h2>{c.title}</h2>
                </CardTitle>
                <CardDescription>{c.blurb}</CardDescription>
              </CardHeader>
              <CardContent>
                <Link
                  href={c.href ?? "#coming-soon"}
                  className="text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {c.href ? "Read more →" : "Coming soon →"}
                </Link>
              </CardContent>
            </Card>
          ))}
        </div>
        <p id="coming-soon" className="mx-auto mt-12 max-w-3xl text-center text-sm text-muted-foreground">
          Detailed documentation is being written. In the meantime, the dashboard&rsquo;s admin
          surfaces are self-describing.
        </p>
      </section>
    </main>
  );
}
