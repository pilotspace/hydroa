"use client";

/**
 * AuditTable — audit event history rendered via the shared sortable DataTable block.
 * Columns: Actor (actor_email), Action (action), Target (target_type), Result (result),
 * When (created_at). Zero rows render the shared Empty state ("No audit events yet").
 */

import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";

export interface AuditRow {
  id: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuditData {
  items: AuditRow[];
  total: number;
}

const COLUMNS: ColumnDef<AuditRow>[] = [
  { accessorKey: "actor_email", header: "Actor" },
  { accessorKey: "action", header: "Action" },
  { accessorKey: "target_type", header: "Target" },
  { accessorKey: "result", header: "Result" },
  {
    accessorKey: "created_at",
    header: "When",
    cell: ({ row }) => formatTimestamp(row.original.created_at),
  },
];

interface AuditTableProps {
  data: AuditData | undefined;
}

export function AuditTable({ data }: AuditTableProps) {
  if (!data) return null;
  return (
    <DataTable
      columns={COLUMNS}
      data={data.items}
      ariaLabel="Audit events"
      emptyMessage="No audit events yet"
    />
  );
}
