"use client";

/**
 * HealthPage — redesigned with PageHeader + hero + Tabs (Overview / History)
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { UpstreamsTable, UpstreamHealthData } from "./UpstreamsTable";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";

export function HealthPage() {
  const healthQuery = useQuery<UpstreamHealthData>({
    queryKey: ["admin-upstream-health"],
    queryFn: () => bffGet<UpstreamHealthData>("/admin/health/upstreams"),
  });

  const upCount = healthQuery.data?.upstreams.filter((u) => u.status === "up").length ?? 0;
  const totalCount = healthQuery.data?.upstreams.length ?? 0;

  return (
    <section
      aria-labelledby="health-heading"
      className="flex flex-col gap-6"
    >
      <PageHeader
        title="Upstream Health"
        titleId="health-heading"
        description="Live status of your model providers"
      />

      {/* Hero region — N/M upstreams healthy */}
      {healthQuery.data && (
        <div
          data-testid="health-hero"
          className="rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Status
          </p>
          <p className="text-3xl font-semibold text-foreground">
            {upCount}/{totalCount} healthy
          </p>
        </div>
      )}

      {/* Page-level states */}
      {healthQuery.isLoading && <Loading label="Loading upstream health…" />}
      {healthQuery.isError && (
        <ErrorState
          title={
            healthQuery.error instanceof Error
              ? healthQuery.error.message
              : "Failed to load upstream health"
          }
        />
      )}

      {/* Data — tabbed */}
      {healthQuery.data && (
        <Tabs defaultValue="overview" className="flex flex-col gap-4">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>

          <TabsContent value="overview">
            <UpstreamsTable data={healthQuery.data} />
          </TabsContent>

          <TabsContent value="history">
            <Card>
              <CardHeader>
                <h2 className="text-sm font-semibold text-foreground">History</h2>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  not available yet
                </p>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </section>
  );
}
