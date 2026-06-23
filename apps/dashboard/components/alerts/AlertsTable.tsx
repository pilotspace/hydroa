"use client";

/**
 * AlertsTable — alert/event history rendered via the shared sortable DataTable block.
 * Columns: Type (event_type), When (created_at), Status (Delivered vs Pending, derived from
 * the `delivered` flag). Zero rows render the shared Empty state ("No alerts yet").
 */

import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/ui";

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
  { accessorKey: "created_at", header: "When" },
  {
    id: "status",
    header: "Status",
    cell: ({ row }) => (row.original.delivered ? "Delivered" : "Pending"),
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
