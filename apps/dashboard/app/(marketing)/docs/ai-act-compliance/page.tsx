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
  title: "AI Act compliance docs",
  description:
    "How Hydroa's Art. 12 record-keeping export supports EU AI Act deployer audit-readiness — scope, timing, and what the export covers.",
  path: "/docs/ai-act-compliance",
});

/**
 * Public /docs/ai-act-compliance page — ai-act-marketing-page TASK.md.
 *
 * The ONE real content category carved out of docs/page.tsx's frozen §3 v1
 * "coming soon" scaffold — a disclosed, scoped exception (the other 4
 * categories stay stubs, per that page's own deferred-content contract note).
 *
 * Frozen contract (§3 v1): PUBLIC, Server Component, no cookie/authed fetch.
 * Describes the Art. 12 bundle in OUTCOME terms only — no manifest/field
 * shape asserted (that shape is owned by the sibling, not-yet-frozen
 * art12-record-keeping-preset contract, per R6/BUNDLE_SHAPE_PREEMPTED).
 */
export default function AiActComplianceDocsPage() {
  return (
    <main id="main">
      <section aria-labelledby="ai-act-docs-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <h1
            id="ai-act-docs-heading"
            className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl"
          >
            EU AI Act record-keeping export
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Audit-readiness support for the EU AI Act&rsquo;s Art. 101 GPAI penalty regime,
            enforceable from Aug 2, 2026 (Art. 101: 3% of global annual turnover or €15M,
            whichever is higher).
          </p>

          <div className="mt-10 space-y-8">
            <Card>
              <CardHeader>
                <CardTitle asChild className="text-xl">
                  <h2>What the export is for</h2>
                </CardTitle>
                <CardDescription>
                  A dated, Art. 12-mapped record-keeping export — described here in outcome
                  terms only, since its exact shape ships as a separate release.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  Hydroa is a GPAI deployer&rsquo;s proxy, not the upstream model provider —
                  the Art. 12 record-keeping obligation sits on upstream GPAI providers. This
                  export gives your own compliance process a dated body of evidence to draw
                  on: it is audit-readiness support, never a claim that using Hydroa itself
                  satisfies your organization&rsquo;s AI Act obligations.
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle asChild className="text-xl">
                  <h2>What it covers</h2>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  The export draws on the same append-only, tenant-scoped audit trail every
                  request already produces — request activity, model/provider routing
                  decisions, and usage lineage — assembled into one dated bundle you can hand
                  to your own compliance or legal team.
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle asChild className="text-xl">
                  <h2>Residency and zero data retention</h2>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  Pair the export with a residency pin (fail-closed — a request that cannot
                  run in your pinned region is refused, never silently rerouted) and, if your
                  tenant has opted in, zero data retention — an irreversible, confirm-gated
                  control once enabled.
                </p>
              </CardContent>
            </Card>
          </div>

          <p className="mt-10">
            <Link
              href="/ai-act-readiness"
              className="text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              &larr; Back to EU AI Act readiness
            </Link>
          </p>
        </div>
      </section>
    </main>
  );
}
