"use client";

/**
 * InvoiceEvidenceDrawer — the evidence drill-down for one invoice line
 * (billing-ui TASK.md §3 CONTRACT M4; DESIGN.md §1 rule 3 — "evidence drill-down over
 * cross-link"). Reuses LogDetailDrawer's Dialog/DrawerContent chrome + captured-trigger
 * focus-return idiom STRUCTURALLY (mirrors LogDetailDrawer.tsx:216-238 verbatim) — but the
 * body is a NEW paginated LIST (the frozen `UsageEvidenceItem` shape is a list, unlike
 * LogDetailDrawer's single-record fetch), and it deliberately never links out to Logs
 * Explorer (no `log_id` exists on this shape, and a billing_admin viewer may lack
 * LOGS_READ — a cross-link could 403 for a legitimate invoice viewer).
 *
 * `lineId === null` -> closed (mirrors LogDetailDrawerProps' `logId` convention exactly).
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  Dialog,
  DrawerContent,
  DialogTitle,
  DialogDescription,
  Loading,
  ErrorState,
  Empty,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { formatNumber, formatTimestamp, formatUsd } from "@/lib/format";

export interface UsageEvidenceItem {
  usage_record_id: string;
  created_at: string;
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  request_id: string;
}

interface EvidencePage {
  items: UsageEvidenceItem[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface InvoiceEvidenceDrawerProps {
  invoiceId: string;
  /** null = closed */
  lineId: string | null;
  onClose: () => void;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError && err.status === 404) return "Evidence not found";
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load evidence";
}

export function InvoiceEvidenceDrawer({ invoiceId, lineId, onClose }: InvoiceEvidenceDrawerProps) {
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const currentCursor = cursorStack[cursorStack.length - 1];

  // Reset pagination whenever a NEW line's drawer opens — never carries a stale cursor
  // from the previously viewed line. React's documented "adjust state during render"
  // escape hatch (same idiom as LogsExplorerPage's own lastGoodPage tracking) rather than
  // a useEffect, so react-hooks/set-state-in-effect stays clean: this is a pure,
  // self-terminating render-time comparison, not a subscription to an external system.
  const [lastOpenedLineId, setLastOpenedLineId] = useState<string | null>(null);
  if (lineId !== lastOpenedLineId) {
    setLastOpenedLineId(lineId);
    if (lineId !== null && cursorStack.length !== 1) {
      setCursorStack([undefined]);
    }
  }

  const evidenceQuery = useQuery<EvidencePage>({
    queryKey: ["admin-invoice-evidence", invoiceId, lineId, currentCursor],
    queryFn: () => {
      const qs = currentCursor ? `?cursor=${encodeURIComponent(currentCursor)}` : "";
      return bffGet<EvidencePage>(`/admin/invoices/${invoiceId}/lines/${lineId}/evidence${qs}`);
    },
    enabled: lineId !== null,
    retry: false,
  });

  // M4/M9: capture the triggering control at the instant the drawer opens (mirrors
  // LogDetailDrawer.tsx:216-225 verbatim), restored in onCloseAutoFocus on close.
  const triggerElementRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (lineId !== null && typeof document !== "undefined") {
      triggerElementRef.current = document.activeElement as HTMLElement | null;
    }
  }, [lineId]);

  const items = evidenceQuery.data?.items ?? [];

  return (
    <Dialog
      open={lineId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          triggerElementRef.current?.focus();
        }}
      >
        <DialogTitle>Evidence</DialogTitle>
        <DialogDescription className="sr-only">
          The underlying usage rows that produced this invoice line&apos;s amount.
        </DialogDescription>

        <div className="mt-4 flex flex-col gap-4">
          {evidenceQuery.isLoading ? (
            <Loading label="Loading evidence…" />
          ) : evidenceQuery.isError ? (
            <ErrorState title={getErrorTitle(evidenceQuery.error)} />
          ) : items.length === 0 && cursorStack.length === 1 ? (
            <Empty title="No usage rows found for this line" />
          ) : (
            <>
              <Table data-slot="invoice-evidence-table" aria-label="Usage evidence">
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Tokens (prompt/completion)</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead>Request ID</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={item.usage_record_id}>
                      <TableCell>{formatTimestamp(item.created_at)}</TableCell>
                      <TableCell>{item.model_id}</TableCell>
                      <TableCell className="font-mono tabular-nums">
                        {formatNumber(item.prompt_tokens)} / {formatNumber(item.completion_tokens)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{formatUsd(item.cost_usd)}</TableCell>
                      <TableCell className="font-mono text-xs">{item.request_id}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <div className="flex items-center justify-end gap-3 px-1 text-sm">
                <button
                  type="button"
                  onClick={() => setCursorStack((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack))}
                  disabled={cursorStack.length <= 1}
                  className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const next = evidenceQuery.data?.next_cursor;
                    if (next) setCursorStack((stack) => [...stack, next]);
                  }}
                  disabled={!evidenceQuery.data?.has_more}
                  className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Next
                </button>
              </div>
            </>
          )}
        </div>
      </DrawerContent>
    </Dialog>
  );
}
