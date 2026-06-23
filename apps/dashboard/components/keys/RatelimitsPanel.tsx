"use client";

/**
 * RatelimitsPanel — a read-only "Rate-limit usage" panel on the /keys page. Reads the live
 * per-key rpm/tpm consumption from GET /admin/ratelimits (via the BFF catch-all) and shows
 * each key's current consumption against its configured limit.
 *
 * Read-only viewer: the four states (loading / error / empty / success) render like every other
 * dashboard surface. Counters may be null when Redis is unavailable — rendered as "—" (unknown),
 * never as 0 (which would falsely read "no usage"). Limits that are null mean "no limit" (∞).
 *
 * Owner/admin only is enforced by the gateway (GET /admin/ratelimits is require_owner_or_admin);
 * this panel is rendered inside the admin-reachable /keys page.
 */

import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { DataTable, Loading, ErrorState } from "@/components/ui";

export interface RatelimitRow {
  key_id: string;
  name: string;
  rpm_limit: number | null;
  tpm_limit: number | null;
  rpm_current: number | null;
  tpm_current: number | null;
}

export interface RatelimitsData {
  keys: RatelimitRow[];
}

function consumption(current: number | null, limit: number | null): string {
  const cur = current === null || current === undefined ? "—" : String(current);
  const lim = limit === null || limit === undefined ? "∞" : String(limit);
  return `${cur} / ${lim}`;
}

const COLUMNS: ColumnDef<RatelimitRow>[] = [
  { accessorKey: "name", header: "Key" },
  {
    id: "rpm",
    header: "RPM (current / limit)",
    cell: ({ row }) => consumption(row.original.rpm_current, row.original.rpm_limit),
  },
  {
    id: "tpm",
    header: "TPM (current / limit)",
    cell: ({ row }) => consumption(row.original.tpm_current, row.original.tpm_limit),
  },
];

export function RatelimitsPanel() {
  const query = useQuery<RatelimitsData>({
    queryKey: ["admin-ratelimits"],
    queryFn: () => bffGet<RatelimitsData>("/admin/ratelimits"),
  });

  return (
    <section
      aria-labelledby="ratelimits-heading"
      className="flex flex-col gap-4"
    >
      <h2
        id="ratelimits-heading"
        className="text-lg font-semibold tracking-tight text-foreground"
      >
        Rate-limit usage
      </h2>

      {query.isLoading ? (
        <Loading label="Loading rate-limit usage…" />
      ) : query.isError ? (
        <ErrorState
          title={
            query.error instanceof Error ? query.error.message : "Failed to load rate-limit usage"
          }
        />
      ) : (
        <DataTable
          columns={COLUMNS}
          data={query.data?.keys ?? []}
          ariaLabel="Rate-limit usage per key"
          emptyMessage="No keys"
        />
      )}
    </section>
  );
}
