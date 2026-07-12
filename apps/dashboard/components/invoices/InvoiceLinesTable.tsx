"use client";

/**
 * InvoiceLinesTable — the grouped, tabular-nums line-item table for one invoice
 * (billing-ui TASK.md §3 CONTRACT — frozen signature: `{ lines, onViewEvidence }`, no
 * total/footer prop — the Total footer row is composed by the parent InvoiceDetailPage from
 * `total_usd` directly, since a footer total is not part of this component's own contract).
 *
 * Columns: Model · Team · Key · Tags · Requests · Tokens (prompt/completion) · Amount
 * (tabular-nums, formatUsd) · a "View evidence" icon-button per row (M3, M4).
 */

import { Search } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { formatNumber, formatUsd } from "@/lib/format";

export interface InvoiceLineItem {
  id: string;
  model_id: string;
  team_id: string | null;
  key_id: string;
  tags: Record<string, string>;
  amount_usd: string;
  prompt_tokens: number;
  completion_tokens: number;
  request_count: number;
  line_type: string;
}

export interface InvoiceLinesTableProps {
  lines: InvoiceLineItem[];
  onViewEvidence: (lineId: string) => void;
}

function formatTags(tags: Record<string, string>): string {
  const entries = Object.entries(tags ?? {});
  if (entries.length === 0) return "—";
  return entries.map(([k, v]) => `${k}:${v}`).join(", ");
}

export function InvoiceLinesTable({ lines, onViewEvidence }: InvoiceLinesTableProps) {
  return (
    <Table data-slot="invoice-lines-table" aria-label="Invoice lines">
      <TableHeader>
        <TableRow>
          <TableHead>Model</TableHead>
          <TableHead>Team</TableHead>
          <TableHead>Key</TableHead>
          <TableHead>Tags</TableHead>
          <TableHead>Requests</TableHead>
          <TableHead>Tokens (prompt/completion)</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead>
            <span className="sr-only">Evidence</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line) => (
          <TableRow key={line.id}>
            <TableCell>{line.model_id}</TableCell>
            <TableCell>{line.team_id ?? "—"}</TableCell>
            <TableCell>{line.key_id}</TableCell>
            <TableCell>{formatTags(line.tags)}</TableCell>
            <TableCell className="font-mono tabular-nums">{formatNumber(line.request_count)}</TableCell>
            <TableCell className="font-mono tabular-nums">
              {formatNumber(line.prompt_tokens)} / {formatNumber(line.completion_tokens)}
            </TableCell>
            <TableCell className="text-right font-mono tabular-nums">{formatUsd(line.amount_usd)}</TableCell>
            <TableCell>
              <button
                type="button"
                aria-label={`View evidence for ${line.model_id}`}
                onClick={() => onViewEvidence(line.id)}
                className="inline-flex size-8 items-center justify-center rounded-md border border-border bg-card text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Search className="size-4" aria-hidden="true" />
              </button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
