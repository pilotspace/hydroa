"use client";

/**
 * AlertsTable — alert/event history rendered via the shared sortable DataTable block.
 * Columns: Type (event_type), When (created_at, humanized), Status (Delivered vs Pending,
 * derived from the `delivered` flag), Payload (the event detail JSON). Zero rows render the
 * shared Empty state ("No alerts yet").
 */

import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";

export interface AlertRow {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
  delivered: boolean;
  delivered_at: string | null;
}

export interface AlertsData {
  items: AlertRow[];
  total: number;
}

const COLUMNS: ColumnDef<AlertRow>[] = [
  { accessorKey: "event_type", header: "Type" },
  {
    accessorKey: "created_at",
    header: "When",
    cell: ({ row }) => formatTimestamp(row.original.created_at),
  },
  {
    id: "status",
    header: "Status",
    cell: ({ row }) => (row.original.delivered ? "Delivered" : "Pending"),
  },
  {
    id: "payload",
    header: "Payload",
    cell: ({ row }) => {
      const payload = row.original.payload;
      const text =
        payload && Object.keys(payload).length > 0 ? JSON.stringify(payload) : "—";
      return (
        <code
          className="block max-w-xs truncate font-mono text-xs text-muted-foreground"
          title={text}
        >
          {text}
        </code>
      );
    },
  },
];

interface AlertsTableProps {
  data: AlertsData | undefined;
}

export function AlertsTable({ data }: AlertsTableProps) {
  if (!data) return null;
  return (
    <DataTable
      columns={COLUMNS}
      data={data.items}
      ariaLabel="Alert events"
      emptyMessage="No alerts yet"
    />
  );
}
