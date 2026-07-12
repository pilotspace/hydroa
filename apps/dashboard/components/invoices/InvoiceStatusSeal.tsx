"use client";

/**
 * InvoiceStatusSeal — the ONE marker of an invoice's document state (billing-ui TASK.md §3
 * CONTRACT, financial-document idiom rule 2, DESIGN.md §1). An "issued" invoice is visibly
 * immutable: a success-tinted Badge + a lock icon + sr-only copy naming the document final.
 * A "draft" invoice renders a visually distinct, non-final Badge. Shared verbatim by the
 * Invoices list (status column) and the Invoice detail header (PageHeader `actions`).
 */

import { Lock } from "lucide-react";
import { Badge } from "@/components/ui";

export interface InvoiceStatusSealProps {
  status: "draft" | "issued";
}

export function InvoiceStatusSeal({ status }: InvoiceStatusSealProps) {
  if (status === "issued") {
    return (
      <Badge variant="success" className="gap-1">
        <Lock className="size-3" aria-hidden="true" />
        Issued
        <span className="sr-only"> — this document is final and cannot be edited</span>
      </Badge>
    );
  }
  return <Badge variant="secondary">Draft</Badge>;
}
