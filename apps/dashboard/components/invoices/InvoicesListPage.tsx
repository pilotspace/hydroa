"use client";

/**
 * InvoicesListPage — the console Invoices list (billing-ui TASK.md §3 CONTRACT M2/M9/R1/R2).
 * Orchestrator: owns a client cursor stack (mirrors LogsExplorerPage's own idiom verbatim in
 * shape), wires the list query to GET /admin/invoices (via the BFF catch-all), and renders
 * the page-level four states around it. A row click navigates to the invoice's detail page.
 *
 * INVOICES_READ is gated server-side (owner/admin/billing_admin/superadmin; operator/viewer/
 * member -> 403). The "Invoices" nav entry (minRole:"admin" in app-shell.tsx) is UX-only —
 * this page's own R1/R2 handling is the defense-in-depth for a direct URL visit or a shown
 * nav link with an insufficient role (M9).
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, Empty, ErrorState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { formatUsd } from "@/lib/format";
import { InvoiceStatusSeal } from "./InvoiceStatusSeal";

export interface InvoiceListItem {
  id: string;
  period_start: string;
  period_end: string;
  status: "draft" | "issued";
  total_usd: string;
  currency: string;
  issued_at: string | null;
}

interface InvoicesPage {
  items: InvoiceListItem[];
  next_cursor: string | null;
  has_more: boolean;
}

const PERIOD_FORMAT = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

function periodLabel(periodStart: string): string {
  const date = new Date(periodStart);
  if (Number.isNaN(date.getTime())) return periodStart;
  return PERIOD_FORMAT.format(date);
}

function buildInvoicesQuery(cursor: string | undefined): string {
  return cursor ? `/admin/invoices?cursor=${encodeURIComponent(cursor)}` : "/admin/invoices";
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError && err.status === 403) return "You don't have access to Invoices";
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load invoices";
}

export function InvoicesListPage() {
  const router = useRouter();
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const currentCursor = cursorStack[cursorStack.length - 1];

  const invoicesQuery = useQuery<InvoicesPage>({
    queryKey: ["admin-invoices", currentCursor],
    queryFn: () => bffGet<InvoicesPage>(buildInvoicesQuery(currentCursor)),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const items = invoicesQuery.data?.items ?? [];

  return (
    <section aria-labelledby="invoices-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Invoices"
        titleId="invoices-heading"
        description="Monthly statements derived from this tenant's usage — immutable once issued."
      />

      {invoicesQuery.isLoading ? (
        <Loading label="Loading invoices…" />
      ) : invoicesQuery.isError ? (
        <ErrorState title={getErrorTitle(invoicesQuery.error)} />
      ) : items.length === 0 ? (
        <Empty
          title="No invoices yet"
          description="A monthly statement appears after your first billable usage."
        />
      ) : (
        <div className="flex flex-col gap-3">
          <Table data-slot="invoices-table" aria-label="Invoices">
            <TableHeader>
              <TableRow>
                <TableHead>Period</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow
                  key={item.id}
                  tabIndex={0}
                  aria-label={`Invoice ${periodLabel(item.period_start)} · ${formatUsd(item.total_usd)}`}
                  onClick={() => router.push(`/app/invoices/${item.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(`/app/invoices/${item.id}`);
                    }
                  }}
                  className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                >
                  <TableCell>{periodLabel(item.period_start)}</TableCell>
                  <TableCell>
                    <InvoiceStatusSeal status={item.status} />
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{formatUsd(item.total_usd)}</TableCell>
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
                const next = invoicesQuery.data?.next_cursor;
                if (next) setCursorStack((stack) => [...stack, next]);
              }}
              disabled={!invoicesQuery.data?.has_more}
              className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
