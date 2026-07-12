"use client";

/**
 * CreditsHistoryTable — keyset-paginated prepaid-ledger history (billing-ui TASK.md §3
 * CONTRACT M6). Columns: Date · Type badge · Amount (signed, tabular-nums) · Balance after.
 * entry_type tone: topup=success, hold=warning, settle=default, release=default,
 * correction=warning (component reuse inventory).
 */

import { Badge, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { formatTimestamp, formatUsd } from "@/lib/format";
import type { BadgeProps } from "@/components/ui";

export interface CreditsHistoryEntry {
  id: string;
  entry_type: "topup" | "hold" | "settle" | "release" | "correction";
  amount_usd: string;
  balance_after_usd: string;
  reference_type: string | null;
  reference_id: string | null;
  request_id: string | null;
  created_at: string;
}

export interface CreditsHistoryTableProps {
  entries: CreditsHistoryEntry[];
}

const ENTRY_TYPE_VARIANT: Record<CreditsHistoryEntry["entry_type"], NonNullable<BadgeProps["variant"]>> = {
  topup: "success",
  hold: "warning",
  settle: "default",
  release: "default",
  correction: "warning",
};

function formatSignedUsd(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return formatUsd(value);
  return n >= 0 ? `+${formatUsd(value)}` : formatUsd(value);
}

export function CreditsHistoryTable({ entries }: CreditsHistoryTableProps) {
  return (
    <Table data-slot="credits-history-table" aria-label="Credit history">
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead className="text-right">Balance after</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map((entry) => (
          <TableRow key={entry.id}>
            <TableCell>{formatTimestamp(entry.created_at)}</TableCell>
            <TableCell>
              <Badge variant={ENTRY_TYPE_VARIANT[entry.entry_type]}>{entry.entry_type}</Badge>
            </TableCell>
            <TableCell className="text-right font-mono tabular-nums">{formatSignedUsd(entry.amount_usd)}</TableCell>
            <TableCell className="text-right font-mono tabular-nums">{formatUsd(entry.balance_after_usd)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
