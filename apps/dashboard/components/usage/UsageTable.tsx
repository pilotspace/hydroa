"use client";

/**
 * UsageTable — records table ≤50 rows, rendered via the shared sortable DataTable block.
 * States: loading (no rows), empty ("No usage records yet"), error (no rows), success (table).
 * The loading/error null-guard is kept ABOVE the DataTable render so those states still emit
 * 0 role="row" elements (frozen usage suite). Cell text is byte-identical to the prior table.
 */

import type { ColumnDef } from "@tanstack/react-table";
import { UsageRecord, UsageData } from "./UsageStatsCards";
import { DataTable } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";

const COLUMNS: ColumnDef<UsageRecord>[] = [
  { accessorKey: "model_id", header: "Model" },
  { accessorKey: "prompt_tokens", header: "Prompt Tokens" },
  { accessorKey: "completion_tokens", header: "Completion Tokens" },
  { accessorKey: "cost_usd", header: "Cost (USD)" },
  { accessorKey: "status", header: "Status" },
  {
    accessorKey: "created_at",
    header: "Date",
    cell: ({ row }) => formatTimestamp(row.original.created_at),
  },
];

interface UsageTableProps {
  isLoading: boolean;
  isError: boolean;
  data: UsageData | undefined;
}

export function UsageTable({ isLoading, isError, data }: UsageTableProps) {
  // Loading or error: render nothing (0 role="row" elements)
  if (isLoading || isError) return null;

  if (!data) return null;

  return <DataTable columns={COLUMNS} data={data.records} emptyMessage="No usage records yet" />;
}
