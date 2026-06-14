"use client";

/**
 * RoutingPage — /routing READ-ONLY health surface (owner/admin).
 *
 * Consumes GET /admin/routing (BFF verbatim, no {data} envelope):
 *   { retry_policy, cooldown, model_groups, candidates } | 403 | 500
 * No mutation exists on this contract — the page only reads + displays.
 *
 * SECURITY/UX: the response is secrets-free by the gateway contract. Circuit state
 * (open|half_open|closed|unknown) renders as a labelled Badge whose TEXT is the
 * state word, so state is NEVER conveyed by color alone (a11y). A settled 403 is
 * deterministic → retry:false (design-for-failure, no retry-storm). "unknown" is a
 * normal per-candidate fail-open value, NOT a page-level error.
 */

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  Badge,
  type BadgeProps,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
  Loading,
  Empty,
  ErrorState,
} from "@/components/ui";

type CircuitState = "open" | "half_open" | "closed" | "unknown";

interface RetryPolicy {
  max_retries: number;
  backoff_base_s: number;
}

interface Cooldown {
  enabled: boolean;
  threshold: number;
  ttl_s: number;
  window_s: number;
}

interface Candidate {
  model_id: string;
  alias: string;
  state: CircuitState;
}

interface RoutingConf {
  retry_policy: RetryPolicy;
  cooldown: Cooldown;
  model_groups: Record<string, string[]>;
  candidates: Candidate[];
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

/** Map circuit state → Badge variant. The state TEXT is the a11y contract; color is presentational. */
const STATE_VARIANT: Record<CircuitState, NonNullable<BadgeProps["variant"]>> = {
  closed: "success",
  half_open: "warning",
  open: "destructive",
  unknown: "secondary",
};

/** A labelled metric row: a term + its read-only value. */
function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

export function RoutingPage() {
  const { data, isLoading, isError, error } = useQuery<RoutingConf>({
    queryKey: ["admin-routing"],
    queryFn: () => bffGet<RoutingConf>("/admin/routing"),
    // design-for-failure: a 403/500 is deterministic for this snapshot GET — don't retry-storm
    retry: false,
  });

  if (isLoading) {
    return <Loading label="Loading routing health" />;
  }

  // 403 (member) / 500 / network — no config leaked, inline alert, no crash
  if (isError || !data) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const { retry_policy, cooldown, model_groups, candidates } = data;
  const aliases = Object.keys(model_groups);

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Routing health</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Read-only view of retry policy, circuit-breaker cooldown, model groups, and per-candidate
          circuit state.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* Retry policy */}
        <Card>
          <CardHeader>
            <CardTitle>Retry policy</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Metric label="Max retries" value={retry_policy.max_retries} />
            <Metric label="Backoff base (s)" value={retry_policy.backoff_base_s} />
          </CardContent>
        </Card>

        {/* Cooldown / circuit breaker */}
        <Card>
          <CardHeader>
            <CardTitle>Cooldown</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-4 text-sm">
              <span className="text-muted-foreground">Circuit breaker</span>
              <Badge variant={cooldown.enabled ? "success" : "secondary"}>
                {cooldown.enabled ? "Enabled" : "Disabled"}
              </Badge>
            </div>
            <Metric label="Failure threshold" value={cooldown.threshold} />
            <Metric label="TTL (s)" value={cooldown.ttl_s} />
            <Metric label="Window (s)" value={cooldown.window_s} />
          </CardContent>
        </Card>
      </div>

      {/* Model groups */}
      <Card>
        <CardHeader>
          <CardTitle>Model groups</CardTitle>
        </CardHeader>
        <CardContent>
          {aliases.length === 0 ? (
            <Empty title="No model groups configured" description="No routing aliases are defined for this tenant." />
          ) : (
            <ul className="flex flex-col gap-3">
              {aliases.map((alias) => (
                <li key={alias} className="flex flex-col gap-1">
                  <span className="text-sm font-semibold text-foreground">{alias}</span>
                  <ol className="ml-4 list-decimal text-sm text-muted-foreground">
                    {model_groups[alias].map((modelId) => (
                      <li key={modelId}>{modelId}</li>
                    ))}
                  </ol>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Candidates circuit state */}
      <Card>
        <CardHeader>
          <CardTitle>Candidate circuit state</CardTitle>
        </CardHeader>
        <CardContent>
          {candidates.length === 0 ? (
            <Empty
              title="No routing candidates"
              description="No model candidates are configured to display circuit state for."
            />
          ) : (
            <Table>
              <TableCaption>Routing candidates and their circuit state</TableCaption>
              <TableHeader>
                <TableRow>
                  <TableHead>Alias</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((c) => (
                  <TableRow key={`${c.alias}:${c.model_id}`}>
                    <TableCell className="font-medium text-foreground">{c.alias}</TableCell>
                    <TableCell className="text-foreground">{c.model_id}</TableCell>
                    <TableCell>
                      <Badge variant={STATE_VARIANT[c.state]}>{c.state}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
