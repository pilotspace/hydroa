/**
 * /app/evals/[setId] — one eval set's cases/runs/baseline. Follows the same Next.js
 * 15+ Promise-based `params` convention as app/(app)/app/invoices/[invoiceId]/page.tsx
 * — a thin Server Component wrapper; all data-fetching, states, and RBAC surfacing
 * live in SetDetailPage itself.
 */

import { SetDetailPage } from "@/components/evals/SetDetailPage";

export const metadata = { title: "Hydroa" };

interface PageProps {
  params: Promise<{ setId: string }>;
}

export default async function EvalSetDetailRoute({ params }: PageProps) {
  const { setId } = await params;
  return <SetDetailPage setId={setId} />;
}
