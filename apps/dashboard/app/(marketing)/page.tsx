import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FeatureCard } from "@/components/marketing/feature-card";
import { BaseUrlSwap } from "@/components/marketing/base-url-swap";
import { Reveal } from "@/components/ui";
import { buildMetadata } from "@/lib/seo";
import { publicApiBaseUrl } from "@/lib/public-api-base-url";
import { formatBasePrice, getPricingCatalogEntry } from "@/lib/pricing-catalog";

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
        className="relative isolate flex min-h-[68vh] flex-col items-center justify-center gap-8 overflow-hidden px-4 py-28 text-center"
      >
        {/* Decorative Aurora backdrop — soft indigo→violet radial wash + dot grid. */}
        <div
          data-slot="hero-aurora"
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 -z-10"
        >
          <div className="absolute inset-0 bg-[radial-gradient(60%_55%_at_50%_0%,var(--color-accent-soft),transparent_70%)]" />
          <div className="absolute left-1/2 top-[-12%] size-[36rem] -translate-x-1/2 rounded-full bg-accent-soft opacity-60 blur-3xl" />
          <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(to_right,var(--color-border)_1px,transparent_1px),linear-gradient(to_bottom,var(--color-border)_1px,transparent_1px)] [background-size:40px_40px] [mask-image:radial-gradient(60%_50%_at_50%_30%,black,transparent)]" />
        </div>
        <Badge
          variant="secondary"
          className="border border-accent-soft-border bg-accent-soft px-3 py-1 text-accent-soft-foreground"
        >
          Multi-tenant AI proxy
        </Badge>
        <h1
          id="hero-heading"
          className="max-w-4xl text-balance text-4xl font-extrabold tracking-tighter text-foreground sm:text-5xl lg:text-hero"
        >
          The AI Proxy Built for{" "}
          <span className="bg-gradient-to-r from-brand-from to-brand-to bg-clip-text text-transparent">
            Enterprise Teams
          </span>
        </h1>
        <p className="max-w-2xl text-pretty text-lg leading-relaxed text-muted-foreground">
          Hydroa routes, budgets, and monitors every AI request across your organisation —
          giving you control, cost visibility, and audit-ready logs from day one.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg" className="shadow-md">
            <Link href="/signup">Get started</Link>
          </Button>
          <Button asChild variant="ghost" size="lg">
            <Link href="/signup?account_type=business">For your team</Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link href="/login">Log in</Link>
          </Button>
        </div>
        <BaseUrlSwap baseUrl={publicApiBaseUrl()} />
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
          <p
            data-slot="price-anchor"
            className="mt-3 text-sm font-medium text-foreground"
          >
            {formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free")}{" "}
            to start · plans from{" "}
            {formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, "Free")}
            /mo
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
        className="border-t border-border bg-gradient-to-br from-brand-from to-brand-to px-4 py-24 text-center shadow-lg sm:px-6 lg:px-8"
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
          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
            <Button asChild variant="secondary" size="lg">
              <Link href="/signup">Get started</Link>
            </Button>
            <Button asChild variant="ghost" size="lg">
              <Link href="/signup?account_type=business">For your team</Link>
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
