"use client";

/**
 * BundleEvidenceSeal — the Art. 12 bundle's own visual assertion that a rendered
 * bundle is a fixed, generated snapshot (compliance-report-center TASK.md §3
 * CONTRACT — FROZEN @ v1). Modeled on InvoiceStatusSeal.tsx's "issued" branch
 * (financial-document idiom) — a bundle has no draft/issued distinction, every
 * response is already final, so there is no prop to branch on.
 */

import { Lock } from "lucide-react";
import { Badge } from "@/components/ui";

export function BundleEvidenceSeal() {
  return (
    <Badge variant="success" className="gap-1">
      <Lock className="size-3" aria-hidden="true" />
      Generated &amp; pinned
      <span className="sr-only"> — this bundle snapshot is fixed and will not change</span>
    </Badge>
  );
}
