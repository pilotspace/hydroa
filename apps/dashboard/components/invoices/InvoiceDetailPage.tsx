"use client";

/**
 * InvoiceDetailPage — the statement-style invoice detail (billing-ui TASK.md §3 CONTRACT
 * M3/M5/M10; DESIGN.md §4). Renders GET /admin/invoices/{id}: a header carrying the
 * statement period + InvoiceStatusSeal (PageHeader's `actions` slot realizes the single-h1
 * "Statement — June 2026 [seal]" layout), InvoiceLinesTable + its own Total footer row
 * (composed here — Total is NOT part of InvoiceLinesTable's own frozen signature),
 * InvoiceCorrectionsTable + its own Corrected-total footer (same composition rule), the two
 * plain export `<a download>` links (M5 — zero new BFF code, the existing binary-passthrough
 * branch already forwards the response), and the InvoiceEvidenceDrawer.
 *
 * NO edit affordance is ever rendered — the frozen backend contract has no PUT/PATCH to back
 * one (financial-document idiom rule 2).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { formatTimestamp, formatUsd } from "@/lib/format";
import { InvoiceStatusSeal } from "./InvoiceStatusSeal";
import { InvoiceLinesTable, type InvoiceLineItem } from "./InvoiceLinesTable";
import { InvoiceCorrectionsTable, type InvoiceCorrectionItem } from "./InvoiceCorrectionsTable";
import { InvoiceEvidenceDrawer } from "./InvoiceEvidenceDrawer";

interface InvoiceDetail {
  id: string;
  tenant_id: string;
  period_start: string;
  period_end: string;
  status: "draft" | "issued";
  currency: string;
  total_usd: string;
  raw_total_usd: string;
  tax_usd: string;
  corrected_total_usd: string;
  issued_at: string | null;
  created_at: string;
  lines: InvoiceLineItem[];
  corrections: InvoiceCorrectionItem[];
}

export interface InvoiceDetailPageProps {
  invoiceId: string;
}

const PERIOD_FORMAT = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

function periodLabel(periodStart: string): string {
  const date = new Date(periodStart);
  if (Number.isNaN(date.getTime())) return periodStart;
  return PERIOD_FORMAT.format(date);
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError && err.status === 403) return "You don't have access to Invoices";
  if (err instanceof BffError && err.status === 404) return "Invoice not found";
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load this invoice";
}

export function InvoiceDetailPage({ invoiceId }: InvoiceDetailPageProps) {
  const [evidenceLineId, setEvidenceLineId] = useState<string | null>(null);

  const detailQuery = useQuery<InvoiceDetail>({
    queryKey: ["admin-invoice-detail", invoiceId],
    queryFn: () => bffGet<InvoiceDetail>(`/admin/invoices/${invoiceId}`),
    retry: false,
  });

  const detail = detailQuery.data;
  const hasCorrections = (detail?.corrections.length ?? 0) > 0;

  return (
    <section aria-labelledby="invoice-detail-heading" className="flex flex-col gap-6">
      <PageHeader
        title={detail ? `Statement — ${periodLabel(detail.period_start)}` : "Invoice"}
        titleId="invoice-detail-heading"
        description={detail?.issued_at ? `Issued ${formatTimestamp(detail.issued_at)}` : undefined}
        actions={detail ? <InvoiceStatusSeal status={detail.status} /> : undefined}
      />

      {detailQuery.isLoading ? (
        <Loading label="Loading invoice…" />
      ) : detailQuery.isError ? (
        <ErrorState title={getErrorTitle(detailQuery.error)} />
      ) : detail ? (
        <>
          <div className="flex flex-col gap-2">
            <InvoiceLinesTable lines={detail.lines} onViewEvidence={setEvidenceLineId} />
            <div className="flex justify-end border-t border-border px-3 py-2 text-sm font-medium">
              <span className="mr-2 text-muted-foreground">Total</span>
              <span className="font-mono tabular-nums text-foreground">{formatUsd(detail.total_usd)}</span>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold text-foreground">Corrections</h2>
            <InvoiceCorrectionsTable corrections={detail.corrections} />
            {hasCorrections ? (
              <div className="flex justify-end border-t border-border px-3 py-2 text-sm font-medium">
                <span className="mr-2 text-muted-foreground">Corrected total</span>
                <span className="font-mono tabular-nums text-foreground">
                  {formatUsd(detail.corrected_total_usd)}
                </span>
              </div>
            ) : null}
          </div>

          <div className="flex gap-2">
            <a
              href={`/api/gw/admin/invoices/${detail.id}/export?format=pdf`}
              download
              className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Download PDF
            </a>
            <a
              href={`/api/gw/admin/invoices/${detail.id}/export?format=csv`}
              download
              className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Download CSV
            </a>
          </div>
        </>
      ) : null}

      <InvoiceEvidenceDrawer
        invoiceId={invoiceId}
        lineId={evidenceLineId}
        onClose={() => setEvidenceLineId(null)}
      />
    </section>
  );
}
