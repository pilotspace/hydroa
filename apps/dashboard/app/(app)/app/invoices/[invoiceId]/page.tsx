/**
 * /app/invoices/[invoiceId] — one tenant's invoice detail (billing-ui TASK.md §3 CONTRACT
 * M3). Follows the same Next.js 15+ Promise-based `params` convention established by
 * app/(app)/app/platform/tenants/[tenantId]/page.tsx (the first dynamic App Router segment
 * in this dashboard) — a thin Server Component wrapper; all data-fetching, loading/error
 * states, and RBAC-403 surfacing live in InvoiceDetailPage itself.
 */

import { InvoiceDetailPage } from "@/components/invoices/InvoiceDetailPage";

export const metadata = { title: "Hydroa" };

interface PageProps {
  params: Promise<{ invoiceId: string }>;
}

export default async function InvoiceDetailRoute({ params }: PageProps) {
  const { invoiceId } = await params;
  return <InvoiceDetailPage invoiceId={invoiceId} />;
}
