"use client";

/**
 * UsageTable — records table ≤50 rows
 * States: loading (no rows), empty ("No usage records yet"), error (no rows), success (table)
 * Uses real <table>/<tr> — only rendered on success so error/loading states have 0 role="row" elements.
 */

import { UsageData } from "./UsageStatsCards";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Empty,
} from "@/components/ui";

interface UsageTableProps {
  isLoading: boolean;
  isError: boolean;
  data: UsageData | undefined;
}

export function UsageTable({ isLoading, isError, data }: UsageTableProps) {
  // Loading or error: render nothing (0 role="row" elements)
  if (isLoading || isError) return null;

  if (!data) return null;

  if (data.records.length === 0) {
    return <Empty title="No usage records yet" />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Model</TableHead>
          <TableHead>Prompt Tokens</TableHead>
          <TableHead>Completion Tokens</TableHead>
          <TableHead>Cost (USD)</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Date</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.records.map((rec) => (
          <TableRow key={rec.id}>
            <TableCell>{rec.model_id}</TableCell>
            <TableCell>{rec.prompt_tokens}</TableCell>
            <TableCell>{rec.completion_tokens}</TableCell>
            <TableCell>{rec.cost_usd}</TableCell>
            <TableCell>{rec.status}</TableCell>
            <TableCell>{rec.created_at}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
