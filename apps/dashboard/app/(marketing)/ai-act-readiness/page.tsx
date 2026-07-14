import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "EU AI Act readiness",
  description:
    "Audit-readiness support for the EU AI Act's Art. 101 GPAI penalty regime — residency, zero data retention, and Art. 12 record-keeping evidence, in place before Aug 2, 2026.",
  path: "/ai-act-readiness",
});

/**
 * Public /ai-act-readiness page — ai-act-marketing-page TASK.md.
 *
 * Frozen contract (§3 v1): PUBLIC — no cookie check, no authed fetch. Server
 * Component (no client directive, no browser-only APIs). Placement: footer link
 * (marketing-shell.tsx FOOTER_COLUMNS) + a pricing-page cross-link only — no
 * MarketingShell nav widening (Tin freeze decision).
 *
 * Signature element = a STATIC 3-tile Art. 101 fact-anchor strip (3% / €15M /
 * Aug 2, 2026, each tile citing "Art. 101" in the same visible text node,
 * tabular-nums styling) — deliberately NOT a live/client countdown (R7).
 *
 * Every legal figure traces to the Tin-approved 2026-07-14 roadmap /
 * tmp/r1-design-context.md fact sheet: Art. 101 GPAI penalty powers apply
 * 2026-08-02 — 3% of global turnover or €15M, whichever is higher — NEVER
 * the Art. 99 general-infringement figure (€35M / 7%, R1).
 *
 * Copy floor (M3/R2): every compliance-adjacent sentence uses "record-keeping
 * support" / "audit-readiness support" — never "AI Act compliant" / "makes
 * you compliant" / "GPAI compliance".
 *
 * M4a (corrected — RetentionZdrSettings.tsx is live on `main` via PR #69):
 * the residency section links to the real, shipped /app/settings "Data &
 * residency" tab, but never claims the link lands pre-selected on that tab
 * (SettingsPage's Tabs defaultValue is hardcoded to "cache").
 */
export default function AiActReadinessPage() {
  return (
    <main id="main">
      {/* HERO + Art.101 fact-anchor strip */}
      <section aria-labelledby="ai-act-hero-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <Badge variant="secondary" className="px-3 py-1">
            EU AI Act &middot; GPAI readiness
          </Badge>
          <h1
            id="ai-act-hero-heading"
            className="mt-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl"
          >
            EU AI Act readiness, before the deadline
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Residency, zero data retention, and audit-readiness support for GPAI deployers —
            in place before Art. 101 enforcement begins.
          </p>
        </div>

        <div className="mx-auto mt-14 grid max-w-4xl grid-cols-1 gap-4 sm:grid-cols-3">
          <Card>
            <CardContent className="p-6 text-center">
              <p className="font-mono text-3xl font-semibold tabular-nums tracking-tight text-foreground">
                3% (Art. 101)
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                of global annual turnover — whichever is higher than the fixed penalty floor
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-6 text-center">
              <p className="font-mono text-3xl font-semibold tabular-nums tracking-tight text-foreground">
                €15M (Art. 101)
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                fixed penalty floor — whichever is higher than the turnover percentage
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-6 text-center">
              <p className="font-mono text-3xl font-semibold tabular-nums tracking-tight text-foreground">
                Aug 2, 2026 (Art. 101)
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                GPAI penalty powers become enforceable
              </p>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* RESIDENCY */}
      <section
        aria-labelledby="ai-act-residency-heading"
        className="border-t border-border bg-muted/30 px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl">
          <h2
            id="ai-act-residency-heading"
            className="text-3xl font-bold tracking-tight text-foreground"
          >
            Residency: fail-closed by design
          </h2>
          <p className="mt-4 text-muted-foreground">
            Pin inference to a region. A request that cannot run in your pinned region is
            refused, never silently rerouted — the same fail-closed policy described on our{" "}
            <Link href="/pricing" className="font-medium text-primary hover:underline">
              pricing page
            </Link>
            .
          </p>
          <p className="mt-4 text-muted-foreground">
            Signed-in tenants can configure their residency pin in Settings &rarr;{" "}
            <Link href="/app/settings" className="font-medium text-primary hover:underline">
              Data &amp; residency
            </Link>
            . Opening that link lands you on Settings generally — it does not jump straight
            to that tab.
          </p>
        </div>
      </section>

      {/* ZDR */}
      <section aria-labelledby="ai-act-zdr-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <h2 id="ai-act-zdr-heading" className="text-3xl font-bold tracking-tight text-foreground">
            Zero data retention
          </h2>
          <p className="mt-4 text-muted-foreground">
            Zero data retention (ZDR) is a tenant opt-in, confirm-gated control: once a tenant
            enables it, the choice is irreversible. Data minimization applies only after that
            opt-in is confirmed — it is not a silent default.
          </p>
        </div>
      </section>

      {/* AUDIT / ART.12 */}
      <section
        aria-labelledby="ai-act-audit-heading"
        className="border-t border-border bg-muted/30 px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl">
          <h2 id="ai-act-audit-heading" className="text-3xl font-bold tracking-tight text-foreground">
            Audit-readiness support for Art. 12 record-keeping
          </h2>
          <p className="mt-4 text-muted-foreground">
            Every request contributes to a dated, Art. 12-mapped record-keeping export you can
            hand to your own compliance process — audit-readiness support, never a claim that
            using Hydroa itself satisfies your organization&rsquo;s AI Act obligations.
          </p>
        </div>
      </section>

      {/* VENDOR RISK / FAILOVER */}
      <section aria-labelledby="ai-act-vendor-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <h2
            id="ai-act-vendor-heading"
            className="text-3xl font-bold tracking-tight text-foreground"
          >
            Multi-provider failover reduces single-vendor risk
          </h2>
          <p className="mt-4 text-muted-foreground">
            Claude Fable 5 was suspended by a US export-control directive from Jun 12 to Jun
            30, 2026 — a real precedent for single-vendor exposure. Anthropic still has no
            first-party EU inference: <code>inference_geo</code> accepts only{" "}
            <code>us</code> or <code>global</code>, with a 1.1x price premium to pin to the US
            region, and a +10% premium for hyperscaler-regional routing (Bedrock/Vertex).
            Multi-provider routing lets you fail over instead of going dark.
          </p>
        </div>
      </section>

      {/* CTA */}
      <section
        aria-labelledby="ai-act-cta-heading"
        className="border-t border-border bg-gradient-to-br from-brand-from to-brand-to px-4 py-24 text-center shadow-lg sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-2xl">
          <h2 id="ai-act-cta-heading" className="text-3xl font-bold tracking-tight text-primary-foreground">
            Get ready before Aug 2, 2026
          </h2>
          <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
            <Button asChild variant="secondary" size="lg">
              <Link href="/signup">Get started</Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link href="/docs/ai-act-compliance">Read the docs</Link>
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
