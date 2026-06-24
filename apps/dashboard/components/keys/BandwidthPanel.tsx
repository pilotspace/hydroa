"use client";

/**
 * BandwidthPanel — a read-only "Bandwidth usage" panel on the /keys page (v37). Reads each key's
 * live, refill-adjusted per-key bandwidth bucket level from GET /admin/bandwidth (via the BFF
 * catch-all) and shows it against the configured capacity (burst).
 *
 * Read-only viewer: the four states (loading / error / empty / success) render like every other
 * dashboard surface — a twin of RatelimitsPanel. A null level means "unknown" (Redis unavailable,
 * the key is untouched, or pacing is disabled) and is rendered as "—", never "0" (which would
 * falsely read "fully throttled"). The capacity caption reads "{rate} tokens/sec · burst {burst}"
 * when pacing is enabled, or "Pacing disabled" when it is off (so an all-"—" table is not read as
 * a fault).
 *
 * Owner/admin only is enforced by the gateway (GET /admin/bandwidth is require_owner_or_admin);
 * this panel is rendered inside the admin-reachable /keys page.
 */

import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { DataTable, Loading, ErrorState } from "@/components/ui";

export interface BandwidthRow {
  key_id: string;
  name: string;
  level: number | null;
}

export interface BandwidthData {
  enabled: boolean;
  rate_per_sec: number;
  burst: number;
  keys: BandwidthRow[];
}

function levelVsCapacity(level: number | null, burst: number): string {
  // null level -> "—" (unknown), never "0" and never "— / 0"; a known level shows "level / burst".
  if (level === null || level === undefined) return "—";
  return `${level} / ${burst}`;
}

function makeColumns(burst: number): ColumnDef<BandwidthRow>[] {
  return [
    { accessorKey: "name", header: "Key" },
    {
      id: "level",
      header: "Level (current / capacity)",
      cell: ({ row }) => levelVsCapacity(row.original.level, burst),
    },
  ];
}

export function BandwidthPanel() {
  const query = useQuery<BandwidthData>({
    queryKey: ["admin-bandwidth"],
    queryFn: () => bffGet<BandwidthData>("/admin/bandwidth"),
  });

  const data = query.data;
  const caption = data
    ? data.enabled
      ? `${data.rate_per_sec} tokens/sec · burst ${data.burst}`
      : "Pacing disabled"
    : null;

  return (
    <section
      aria-labelledby="bandwidth-heading"
      className="flex flex-col gap-4"
    >
      <h2
        id="bandwidth-heading"
        className="text-lg font-semibold tracking-tight text-foreground"
      >
        Bandwidth usage
      </h2>

      {caption ? <p className="text-sm text-muted-foreground">{caption}</p> : null}

      {query.isLoading ? (
        <Loading label="Loading bandwidth usage…" />
      ) : query.isError ? (
        <ErrorState
          title={
            query.error instanceof Error ? query.error.message : "Failed to load bandwidth usage"
          }
        />
      ) : (
        <DataTable
          columns={makeColumns(data?.burst ?? 0)}
          data={data?.keys ?? []}
          ariaLabel="Bandwidth level per key"
          emptyMessage="No keys"
        />
      )}
    </section>
  );
}
