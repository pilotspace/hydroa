"use client";

/**
 * UpstreamsTable — per-upstream health rendered via the shared sortable DataTable block.
 * Columns: Upstream (name), Status (Up vs Down, from the `status` field), Last event
 * (last_event_type + last_event_at, or "—" when no incident was ever recorded). Zero rows
 * render the shared Empty state ("No upstreams monitored"). Only genuinely-monitored upstreams
 * appear — the backend never fabricates fake-green rows for unpinged providers.
 */

import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/ui";

export interface UpstreamRow {
  name: string;
  status: "up" | "down";
  last_event_at: string | null;
  last_event_type: string | null;
}

export interface UpstreamHealthData {
  checked_at: string;
  upstreams: UpstreamRow[];
}

const COLUMNS: ColumnDef<UpstreamRow>[] = [
  { accessorKey: "name", header: "Upstream" },
  {
    id: "status",
    header: "Status",
    cell: ({ row }) => (row.original.status === "down" ? "Down" : "Up"),
  },
  {
    id: "last_event",
    header: "Last event",
    cell: ({ row }) => {
      const { last_event_type, last_event_at } = row.original;
      if (!last_event_type) return "—";
      return last_event_at ? `${last_event_type} (${last_event_at})` : last_event_type;
    },
  },
];

interface UpstreamsTableProps {
  data: UpstreamHealthData | undefined;
}

export function UpstreamsTable({ data }: UpstreamsTableProps) {
  if (!data) return null;
  return (
    <DataTable
      columns={COLUMNS}
      data={data.upstreams}
      ariaLabel="Upstream health"
      emptyMessage="No upstreams monitored"
    />
  );
}
