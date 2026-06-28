"use client";

/**
 * RoutingPage — /routing health + config-edit surface.
 *
 * Consumes GET /admin/routing (BFF verbatim, no {data} envelope):
 *   { routing_strategy, retry_policy, cooldown, model_groups, deployments, candidates } | 403 | 500
 *
 * READ-ONLY health cards: visible to all roles that can reach the endpoint.
 * EDITOR section: visible only to owner/admin (gated via useCurrentUser).
 *
 * SECURITY/UX: the response is secrets-free by the gateway contract. Circuit state
 * (open|half_open|closed|unknown) renders as a labelled Badge whose TEXT is the
 * state word, so state is NEVER conveyed by color alone (a11y). A settled 403 is
 * deterministic → retry:false (design-for-failure, no retry-storm). "unknown" is a
 * normal per-candidate fail-open value, NOT a page-level error.
 */

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { RoutingEditor, type RoutingStrategy, type DeploymentRow } from "./RoutingEditor";
import {
  Badge,
  type BadgeProps,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  DataTable,
  Loading,
  Empty,
  ErrorState,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";

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
  routing_strategy: RoutingStrategy;
  retry_policy: RetryPolicy;
  cooldown: Cooldown;
  model_groups: Record<string, string[]>;
  /** Object-form deployment rows — present in v32 contract. */
  deployments: Record<string, DeploymentRow[]>;
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

/** Candidate circuit-state columns — pure display, non-sortable (behavior-preserving vs the
    prior <Table>). State renders as a labelled Badge (text is the a11y contract, color reinforces). */
const CANDIDATE_COLUMNS: ColumnDef<Candidate>[] = [
  {
    accessorKey: "alias",
    header: "Alias",
    enableSorting: false,
    cell: ({ row }) => <span className="font-medium text-foreground">{row.original.alias}</span>,
  },
  {
    accessorKey: "model_id",
    header: "Model",
    enableSorting: false,
    cell: ({ row }) => <span className="text-foreground">{row.original.model_id}</span>,
  },
  {
    id: "state",
    header: "State",
    enableSorting: false,
    cell: ({ row }) => <Badge variant={STATE_VARIANT[row.original.state]}>{row.original.state}</Badge>,
  },
];

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
  // Role-gate: editor section is owner/admin only
  const { data: currentUser } = useCurrentUser();
  const canEdit = currentUser?.role === "owner" || currentUser?.role === "admin";

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

  const { routing_strategy, retry_policy, cooldown, model_groups, deployments, candidates } = data;
  const aliases = Object.keys(model_groups);
  // Hero metric — circuit health: candidates not currently tripped open. Derived only
  // from the fetched candidates (never fabricated). "open" = tripped; everything else healthy.
  const healthyCount = candidates.filter((c) => c.state !== "open").length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Routing health"
        description="Read-only view of retry policy, circuit-breaker cooldown, model groups, and per-candidate circuit state."
      />

      {/* Hero — routing strategy + circuit health summary. */}
      <div
        data-testid="routing-hero"
        className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-muted/30 p-4"
      >
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Routing strategy
          </p>
          <p className="text-2xl font-semibold capitalize text-foreground">
            {routing_strategy}
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Healthy candidates
          </p>
          <p className="text-2xl font-semibold text-foreground">
            {healthyCount} / {candidates.length}
          </p>
        </div>
      </div>

      <Tabs defaultValue="overview" className="flex flex-col gap-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {canEdit && <TabsTrigger value="editor">Editor</TabsTrigger>}
        </TabsList>

        {/* Overview: the read-only health cards. */}
        <TabsContent value="overview">
          <div className="flex flex-col gap-6">
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
            <DataTable
              ariaLabel="Routing candidates and their circuit state"
              caption="Routing candidates and their circuit state"
              columns={CANDIDATE_COLUMNS}
              data={candidates}
            />
          )}
        </CardContent>
      </Card>
          </div>
        </TabsContent>

        {/* Editor — owner/admin only: the tab + panel exist only when canEdit. */}
        {canEdit && (
          <TabsContent value="editor">
            <RoutingEditor
              serverStrategy={routing_strategy}
              serverDeployments={deployments ?? {}}
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
