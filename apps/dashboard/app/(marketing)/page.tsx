import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FeatureCard } from "@/components/marketing/feature-card";
import { Reveal } from "@/components/ui";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "Hydroa — AI Proxy for Enterprise Teams",
  description:
    "One OpenAI-compatible endpoint for every provider — multi-provider routing, per-tenant cost tracking, key governance, and rate limiting for enterprise teams.",
  path: "/",
  absoluteTitle: true,
});

/**
 * Public landing page — the `/` entry point.
 *
 * Frozen contract (§3 v1): PUBLIC — no cookie check, no authed fetch, no redirect.
 * Server Component — no client directive, no browser-only APIs.
 *
 * Sections (in order, per contract):
 *   HERO      — h1 + subhead + [Get started → /signup] [Log in → /login]
 *   #product  — h2 "Features" + ≥4 Card tiles
 *   #pricing  — h2 teaser + link → /pricing
 *   #docs     — h2 teaser + link → /docs
 *   TRUST     — h2 "Built for enterprise" band
 *   CTA       — h2 final call-to-action + [Get started → /signup]
 *
 * a11y: exactly one h1; h2 per section; monotonic headings; no level skips.
 */

const FEATURES = [
  {
    title: "Multi-provider routing",
    description:
      "Intelligently route AI requests across OpenAI, Anthropic, Google Gemini, AWS Bedrock, and Azure OpenAI with configurable fallback and load-balancing strategies.",
    icon: "🔀",
  },
  {
    title: "Per-tenant cost tracking",
    description:
      "Track spend per tenant with real-time billing reconciliation. Understand exactly what each team costs — with provider-side validation and audit-ready records.",
    icon: "💰",
  },
  {
    title: "Key governance & BYOK",
    description:
      "Bring your own keys with encrypted at-rest storage per tenant and provider. Full key lifecycle management with admin API and SSO-scoped access.",
    icon: "🔑",
  },
  {
    title: "Rate limiting & bandwidth pacing",
    description:
      "Per-key token-bucket rate limiting with atomic Redis enforcement. Protect upstream quotas and guarantee fair per-tenant throughput under concurrent load.",
    icon: "⚡",
  },
  {
    title: "Spend analytics",
    description:
      "Unified usage dashboard with cost breakdowns by tenant, model, and provider. Identify waste, forecast budgets, and stay ahead of cost overruns.",
    icon: "📊",
  },
  {
    title: "Alerting & drift detection",
    description:
      "Configurable spend alerts and reconciliation drift checks. Get notified when upstream charges diverge from billed amounts before they become surprises.",
    icon: "🔔",
  },
] as const;

const TRUST_PILLARS = [
  {
    title: "Multi-tenant isolation",
    description:
      "Strict tenant boundary enforcement at every layer. Keys, usage data, and billing are scoped per tenant with no cross-contamination.",
  },
  {
    title: "Audit-ready logs",
    description:
      "Every AI request is logged with cost, provider, model, latency, and tenant. Full audit trail available for compliance and incident review.",
  },
  {
    title: "SSO & access control",
    description:
      "OIDC-based SSO with role-based admin access. Owner and admin roles control key governance, routing config, and cost visibility.",
  },
] as const;

export default function MarketingRootPage() {
  return (
    <main id="main">
      {/* ── HERO ─────────────────────────────────────────────────────────── */}
      {/* Reveal = progressive entrance (motion-safe); renders as the same
          <section> with content always visible (reduced-motion safe via the net). */}
      <Reveal
        as="section"
        aria-labelledby="hero-heading"
        className="flex min-h-[60vh] flex-col items-center justify-center gap-8 px-4 py-24 text-center"
      >
        <Badge variant="secondary" className="px-3 py-1">
          Multi-tenant AI proxy
        </Badge>
        <h1
          id="hero-heading"
          className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl lg:text-6xl"
        >
          The AI Proxy Built for Enterprise Teams
        </h1>
        <p className="max-w-2xl text-lg text-muted-foreground">
          Hydroa routes, budgets, and monitors every AI request across your organisation —
          giving you control, cost visibility, and audit-ready logs from day one.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/signup">Get started</Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link href="/login">Log in</Link>
          </Button>
        </div>
      </Reveal>

      {/* ── FEATURES (#product) ──────────────────────────────────────────── */}
      <section
        id="product"
        aria-labelledby="product-heading"
        className="border-t border-border bg-muted/30 px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-7xl">
          <div className="mb-12 text-center">
            <h2
              id="product-heading"
              className="text-3xl font-bold tracking-tight text-foreground"
            >
              Features
            </h2>
            <p className="mt-3 text-muted-foreground">
              Everything you need to operate AI at enterprise scale.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <FeatureCard
                key={feature.title}
                title={feature.title}
                description={feature.description}
                icon={feature.icon}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ── PRICING TEASER (#pricing) ─────────────────────────────────────── */}
      <section
        id="pricing"
        aria-labelledby="pricing-heading"
        className="px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl text-center">
          <h2
            id="pricing-heading"
            className="text-3xl font-bold tracking-tight text-foreground"
          >
            Simple, transparent pricing
          </h2>
          <p className="mt-4 text-muted-foreground">
            From solo teams to enterprise deployments. Usage-based tiers with no
            hidden fees — you pay for what you proxy.
          </p>
          <div className="mt-8">
            <Button asChild variant="outline" size="lg">
              <Link href="/pricing">View pricing plans</Link>
            </Button>
          </div>
        </div>
      </section>

      {/* ── DOCS TEASER (#docs) ───────────────────────────────────────────── */}
      <section
        id="docs"
        aria-labelledby="docs-heading"
        className="border-t border-border bg-muted/30 px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl text-center">
          <h2
            id="docs-heading"
            className="text-3xl font-bold tracking-tight text-foreground"
          >
            Docs &amp; integration guides
          </h2>
          <p className="mt-4 text-muted-foreground">
            Drop-in OpenAI-compatible API. Full provider guides, admin API reference,
            and BYOK setup walkthroughs — so you&apos;re productive in minutes.
          </p>
          <div className="mt-8">
            <Button asChild variant="outline" size="lg">
              <Link href="/docs">Read the docs</Link>
            </Button>
          </div>
        </div>
      </section>

      {/* ── TRUST BAND ───────────────────────────────────────────────────── */}
      <section
        aria-labelledby="trust-heading"
        className="px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-7xl">
          <div className="mb-12 text-center">
            <h2
              id="trust-heading"
              className="text-3xl font-bold tracking-tight text-foreground"
            >
              Built for enterprise
            </h2>
            <p className="mt-3 text-muted-foreground">
              Security, compliance, and reliability at every layer.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
            {TRUST_PILLARS.map((pillar) => (
              <div key={pillar.title} className="text-center">
                <h3 className="text-lg font-semibold text-foreground">
                  {pillar.title}
                </h3>
                <p className="mt-2 text-sm text-muted-foreground">
                  {pillar.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FINAL CTA ────────────────────────────────────────────────────── */}
      <section
        aria-labelledby="cta-heading"
        className="border-t border-border bg-primary px-4 py-20 text-center sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-2xl">
          <h2
            id="cta-heading"
            className="text-3xl font-bold tracking-tight text-primary-foreground"
          >
            Ready to take control of your AI spend?
          </h2>
          <p className="mt-4 text-primary-foreground/80">
            Start routing, tracking, and governing AI usage across your organisation today.
          </p>
          <div className="mt-8">
            <Button asChild variant="secondary" size="lg">
              <Link href="/signup">Get started</Link>
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
