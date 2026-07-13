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

const TIERS: Tier[] = [
  {
    name: "Starter",
    price: "Free",
    qualifier: "for evaluation",
    description: "Single tenant, get a feel for the proxy.",
    features: [
      "1 tenant, up to 3 users",
      "Multi-provider routing",
      "Basic usage & cost tracking",
      "Community support",
    ],
    cta: { label: "Get started", href: "/signup" },
  },
  {
    name: "Team",
    price: "$99",
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
    price: "Contact us",
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

        <div className="mx-auto mt-14 grid max-w-6xl grid-cols-1 gap-6 lg:grid-cols-3">
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
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
