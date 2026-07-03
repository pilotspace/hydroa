"use client";

/**
 * PlatformTenantDirectory — Screen 1 of the admin-console-ui task: a superadmin-only,
 * searchable/paginated directory of every tenant on the platform (/app/platform/tenants).
 *
 * GET /admin/platform/tenants?q=&limit=&offset= -> TenantDirectoryListResponse
 * { tenants: [{id, name, kind, created_at}], total } — platform_tenants_router.py,
 * FROZEN @ v1, cited verbatim (not redefined) by TASK.md §3 CONTRACT.
 *
 * Search/pagination is page-owned, SERVER-driven state (q/offset feed the queryKey)
 * rather than DataTable's client-side searchable/pageSizeOptions opt-in — this list
 * can be platform-wide, unlike every other existing list page's small, tenant-scoped
 * collection (TASK.md §1 Framings weighed, cited catalog gap). The rendered chrome is
 * deliberately styled to look like DataTable's own built-in search/paginate UI.
 */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { Badge, Card, CardContent, DataTable, Input, Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";

export interface PlatformTenantSummary {
  id: string;
  name: string;
  kind: string;
  created_at: string;
}

interface TenantDirectoryListResponse {
  tenants: PlatformTenantSummary[];
  total: number;
}

const PAGE_LIMIT = 20;
const SEARCH_DEBOUNCE_MS = 300;

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function PlatformTenantDirectory() {
  const [query, setQuery] = React.useState("");
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const [offset, setOffset] = React.useState(0);

  // Debounced search -> queryKey (page-owned, server-driven; see file header).
  React.useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // A new search always resets to the first page — via the "adjust state
  // during render" pattern (React docs) rather than a setState-in-effect,
  // mirroring CacheSettings.tsx/GuardrailSettings.tsx's own seed/reseed
  // convention already used elsewhere in this codebase.
  const [prevDebouncedQuery, setPrevDebouncedQuery] = React.useState(debouncedQuery);
  if (debouncedQuery !== prevDebouncedQuery) {
    setPrevDebouncedQuery(debouncedQuery);
    setOffset(0);
  }

  const { data, isLoading, isError, error } = useQuery<TenantDirectoryListResponse>({
    queryKey: ["platform-tenants", debouncedQuery, offset],
    queryFn: () =>
      bffGet<TenantDirectoryListResponse>(
        `/admin/platform/tenants?q=${encodeURIComponent(debouncedQuery)}&limit=${PAGE_LIMIT}&offset=${offset}`,
      ),
    retry: false,
  });

  const tenants = data?.tenants ?? [];
  const total = data?.total ?? 0;
  const hasMore = offset + tenants.length < total;

  const columns = React.useMemo<ColumnDef<PlatformTenantSummary>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <Link
            href={`/app/platform/tenants/${row.original.id}`}
            title={row.original.name}
            className="block max-w-64 truncate rounded font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {row.original.name}
          </Link>
        ),
      },
      {
        accessorKey: "kind",
        header: "Kind",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.kind === "platform" ? (
            <Badge variant="warning">Platform</Badge>
          ) : (
            <Badge variant="secondary">Standard</Badge>
          ),
      },
      {
        accessorKey: "created_at",
        header: "Created",
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {new Date(row.original.created_at).toLocaleDateString()}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <section aria-labelledby="platform-tenants-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Platform · Tenants"
        titleId="platform-tenants-heading"
        description="Search every tenant on the platform and open its cross-tenant admin console."
      />

      {isLoading && <Loading label="Loading tenants" className="animate-pulse" />}

      {isError && !isLoading && <ErrorState title={getErrorTitle(error)} />}

      {!isLoading && !isError && (
        <div className="flex flex-col gap-3">
          <Input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tenants…"
            aria-label="Search tenants"
            className="max-w-sm"
          />

          <Card variant="flat">
            <CardContent className="p-0">
              <DataTable
                columns={columns}
                data={tenants}
                ariaLabel="Tenants"
                emptyMessage="No tenants found."
              />
            </CardContent>
          </Card>

          {tenants.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 px-1 text-sm">
              <span aria-live="polite" className="text-muted-foreground">
                Showing {offset + 1}–{offset + tenants.length} of {total}
              </span>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_LIMIT))}
                  disabled={offset === 0}
                  className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => setOffset((o) => o + PAGE_LIMIT)}
                  disabled={!hasMore}
                  className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
