"use client";

/**
 * InvoiceCorrectionsTable — append-only, signed-delta corrections for one invoice
 * (billing-ui TASK.md §3 CONTRACT — frozen signature: `{ corrections }` only; the
 * "Corrected total" footer is NOT part of this component's own contract — it is composed by
 * the parent InvoiceDetailPage from `corrected_total_usd` directly, alongside this table).
 *
 * `corrections.length === 0` renders "No corrections" (the section itself is never omitted —
 * M3 edge case); otherwise a table with the signed delta (up/down icon, mirrors StatCard's
 * own delta-tone convention), reason, created_by, created_at. The original invoice lines are
 * never touched by a correction's presence.
 */

import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { formatTimestamp, formatUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

export interface InvoiceCorrectionItem {
  id: string;
  delta_usd: string;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface InvoiceCorrectionsTableProps {
  corrections: InvoiceCorrectionItem[];
}

function SignedDelta({ deltaUsd }: { deltaUsd: string }) {
  const n = Number(deltaUsd);
  const negative = Number.isFinite(n) && n < 0;
  const Icon = negative ? ArrowDownRight : ArrowUpRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-mono tabular-nums",
        negative ? "text-destructive-text" : "text-success-text",
      )}
    >
      <Icon className="size-4" aria-hidden="true" />
      {formatUsd(deltaUsd)}
      <span className="sr-only">{negative ? "decrease" : "increase"}</span>
    </span>
  );
}

export function InvoiceCorrectionsTable({ corrections }: InvoiceCorrectionsTableProps) {
  if (corrections.length === 0) {
    return <p className="text-sm text-muted-foreground">No corrections</p>;
  }

  return (
    <Table data-slot="invoice-corrections-table" aria-label="Invoice corrections">
      <TableHeader>
        <TableRow>
          <TableHead>Amount</TableHead>
          <TableHead>Reason</TableHead>
          <TableHead>By</TableHead>
          <TableHead>When</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {corrections.map((c) => (
          <TableRow key={c.id}>
            <TableCell>
              <SignedDelta deltaUsd={c.delta_usd} />
            </TableCell>
            <TableCell>{c.reason}</TableCell>
            <TableCell>{c.created_by}</TableCell>
            <TableCell>{formatTimestamp(c.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
