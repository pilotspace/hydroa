import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatBasePrice, getPricingCatalogEntry } from "@/lib/pricing-catalog";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "Pricing",
  description:
    "Simple, transparent pricing for the Hydroa AI proxy — per-tenant budgets, usage-based cost tracking, and no per-seat lock-in.",
  path: "/pricing",
});

/**
 * Public /pricing page — the landing #pricing teaser and the shell footer
 * "Pricing" link both target this route.
 *
 * Frozen contract (§3 v1): PUBLIC — no cookie check, no authed fetch, no redirect.
 * Server Component — no client directive. Presentational only (no checkout).
 *
 * Prices are representative placeholders (no commercial model finalised) — copy,
 * not a commitment; see the §1 lowest-confidence flag.
 */

type Tier = {
  name: string;
  price: string;
  qualifier: string;
  description: string;
  features: string[];
  cta: { label: string; href: string };
  featured?: boolean;
};

// pricing-tier-ladder TASK.md §3 (FROZEN @ v1) — CHANGE REQUEST to plan-tiers-and-
// base-fee TASK.md §3's "still 3 rendered cards" clause (every other clause there —
// Schema/ORM/Invoice/Signup/the no-drift MECHANISM — is unchanged and still governs).
// All 5 PRICING_CATALOG tiers now render, ascending-price / personal-then-business
// order: Free, Starter, Pro, Team, Enterprise. Every price/qualifier is still
// formatBasePrice(getPricingCatalogEntry(<name>).basePriceUsd, <nullLabel>) — never a
// re-hardcoded literal (M1). The card that used to be mislabeled "Starter" while
// actually binding to the catalog's `free` entry is renamed "Free" (matching
// PRICING_CATALOG's own displayName) so the catalog's real `starter` ($1) tier can
// finally own the "Starter" label — resolving the naming collision (M2).
const TIERS: Tier[] = [
  {
    name: "Free",
    // Null base price renders as "$0" (not the word "Free") so it never collides with the
    // card's own "Free" title — a card that reads "Free / Free" is redundant, and the
    // duplicate leaf text made the by-text price assertion ambiguous. Tin-decided 2026-07-21.
    price: formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "$0"),
    qualifier: "for evaluation",
    description: "Single tenant, get a feel for the proxy.",
    features: [
      "1 tenant, up to 1 user",
      "Multi-provider routing",
      "Basic usage & cost tracking",
      "Community support",
    ],
    cta: { label: "Get started", href: "/signup" },
  },
  {
    name: "Starter",
    price: formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, "Free"),
    qualifier: "per month",
    description: "For a single builder ready to move past evaluation.",
    features: [
      "1 tenant, up to 1 user",
      "Multi-provider routing",
      "Usage & cost tracking",
      "Community support",
    ],
    cta: { label: "Get started", href: "/signup" },
  },
  {
    name: "Pro",
    price: formatBasePrice(getPricingCatalogEntry("pro").basePriceUsd, "Free"),
    qualifier: "per month",
    description: "For a power user who wants more headroom.",
    features: [
      "1 tenant, up to 1 user",
      "Multi-provider routing",
      "Usage & cost tracking",
      "Email support",
    ],
    cta: { label: "Get started", href: "/signup" },
  },
  {
    name: "Team",
    price: formatBasePrice(getPricingCatalogEntry("team").basePriceUsd, "Free"),
    qualifier: "per month + usage",
    description: "For teams running AI in production.",
    features: [
      "Unlimited users",
      "BYOK + key governance",
      "Rate limiting & bandwidth pacing",
      "Spend analytics & alerting",
      "Priority service tier (optional, usage-priced)",
      "Email support",
    ],
    cta: { label: "Get started", href: "/signup" },
    featured: true,
  },
  {
    name: "Enterprise",
    price: formatBasePrice(getPricingCatalogEntry("enterprise").basePriceUsd, "Contact us"),
    qualifier: "custom",
    description: "SSO, audit, and compliance at scale.",
    features: [
      "SSO/OIDC + role-based access",
      "Audit-ready logs & data retention",
      "Per-tenant SLOs & observability",
      "Data residency: pin inference to US or EU",
      "Dedicated support & SLA",
    ],
    cta: { label: "Talk to us", href: "/signup" },
  },
];

export default function PricingPage() {
  return (
    <main id="main">
      <section
        aria-labelledby="pricing-heading"
        className="px-4 py-20 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl text-center">
          <Badge variant="secondary" className="px-3 py-1">
            Usage-based pricing
          </Badge>
          <h1
            id="pricing-heading"
            className="mt-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl"
          >
            Pricing
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            From evaluation to enterprise scale. You pay for what you proxy — no
            hidden fees.
          </p>
        </div>

        <div className="mx-auto mt-14 grid max-w-7xl grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {TIERS.map((tier) => {
            const tierId = `tier-${tier.name.toLowerCase()}`;
            return (
            <article key={tier.name} aria-labelledby={tierId}>
            <Card
              className={
                tier.featured
                  ? "flex h-full flex-col border-primary shadow-md"
                  : "flex h-full flex-col"
              }
            >
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle asChild className="text-xl">
                    <h2 id={tierId}>{tier.name}</h2>
                  </CardTitle>
                  {tier.featured ? <Badge>Most popular</Badge> : null}
                </div>
                <CardDescription>{tier.description}</CardDescription>
                <p className="mt-4">
                  <span className="text-3xl font-bold text-foreground">
                    {tier.price}
                  </span>{" "}
                  <span className="text-sm text-muted-foreground">
                    {tier.qualifier}
                  </span>
                </p>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col justify-between gap-6">
                <ul role="list" className="space-y-2 text-sm text-muted-foreground">
                  {tier.features.map((f) => (
                    <li key={f} className="flex items-start gap-2">
                      <span aria-hidden="true" className="text-primary">
                        ✓
                      </span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
                <Button
                  asChild
                  variant={tier.featured ? "default" : "outline"}
                  className="w-full"
                >
                  <Link href={tier.cta.href}>{tier.cta.label}</Link>
                </Button>
              </CardContent>
            </Card>
            </article>
            );
          })}
        </div>

        {/* residency-tiers-ui TASK.md §3 M11: residency + priority story, static copy
            only — matches the page's own frozen "representative placeholders, not a
            commitment" posture. Zero fetch; still a Server Component. */}
        <div className="mx-auto mt-14 max-w-3xl">
          <Card>
            <CardHeader>
              <CardTitle asChild className="text-lg">
                <h2>Data residency & priority routing</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Pin inference to a region — EU or US — with a fail-closed policy: a
                request that cannot run in your pinned region is refused, never silently
                rerouted. Need priority throughput? Priority-tier keys get preference
                under contention, with Standard traffic never starved.
              </p>
              {/* ai-act-marketing-page TASK.md §3 v1: one cross-link sentence, no
                  TIERS change — this Card's frozen v1 shape is otherwise untouched. */}
              <p className="mt-3 text-sm text-muted-foreground">
                Preparing for the EU AI Act?{" "}
                <Link href="/ai-act-readiness" className="font-medium text-primary hover:underline">
                  See our AI Act readiness page
                </Link>{" "}
                for the Art. 101 details.
              </p>
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
