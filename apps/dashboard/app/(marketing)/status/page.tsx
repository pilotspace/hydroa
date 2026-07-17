import { Badge } from "@/components/ui/badge";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "System status",
  description:
    "Live operational status and uptime for the Hydroa AI proxy — API, routing, and provider connectivity.",
  path: "/status",
});

/**
 * Public /status — trust + component status + SLA statement.
 *
 * Frozen contract (§3 v1): PUBLIC Server Component — no cookie, no authed fetch.
 * Status is PRESENTATIONAL (static "Operational" baseline). It deliberately does
 * NOT call the gated admin health endpoint from a public route — live wiring needs
 * a new PUBLIC health-summary endpoint (SPEC delta). Status is conveyed by TEXT
 * label (not color alone) for WCAG-AA.
 */
const COMPONENTS = [
  { name: "Proxy / data plane", status: "Operational", detail: "OpenAI-compatible API and provider routing." },
  { name: "Dashboard", status: "Operational", detail: "Admin console and BFF." },
  { name: "Upstream providers", status: "Operational", detail: "OpenAI · Anthropic · Gemini · Bedrock · Azure." },
];

export default function StatusPage() {
  return (
    <main id="main">
      <section aria-labelledby="status-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <h1 id="status-heading" className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            System status
          </h1>
          <p className="mt-4 text-lg text-muted-foreground" role="status">
            All systems operational.
          </p>

          <ul role="list" className="mt-12 divide-y divide-border rounded-lg border border-border">
            {COMPONENTS.map((c) => (
              <li key={c.name} className="flex items-center justify-between gap-4 p-4">
                <div>
                  <h2 className="text-base font-semibold text-foreground">{c.name}</h2>
                  <p className="text-sm text-muted-foreground">{c.detail}</p>
                </div>
                <Badge variant="secondary" className="shrink-0">
                  <span aria-hidden="true" className="mr-1.5 inline-block size-2 rounded-full bg-success" />
                  {c.status}
                </Badge>
              </li>
            ))}
          </ul>

          <section aria-labelledby="sla-heading" className="mt-16">
            <h2 id="sla-heading" className="text-2xl font-semibold text-foreground">
              Service-level commitment
            </h2>
            <p className="mt-4 leading-relaxed text-muted-foreground">
              We target <strong className="text-foreground">99.9% monthly uptime</strong> for the
              proxy data plane. The gateway is built for failure — bounded timeouts, retries on
              idempotent calls, and a circuit breaker on each upstream — so a single provider
              degrading does not take the service down. Enterprise plans include a contractual SLA
              and dedicated support.
            </p>
            <p className="mt-4 text-sm text-muted-foreground">
              This page reflects current operational posture. Live per-component health is on the
              roadmap.
            </p>
          </section>
        </div>
      </section>
    </main>
  );
}
