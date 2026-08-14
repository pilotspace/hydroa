/**
 * /app/evals/[setId]/runs/[runId] — a run's verdict, leading with <VerdictBanner>
 * before the per-case diff drill-down (the signature element of this console). Same
 * thin Server Component wrapper convention as the sibling evals routes.
 */

import { RunVerdictPage } from "@/components/evals/RunVerdictPage";

export const metadata = { title: "Hydroa" };

interface PageProps {
  params: Promise<{ setId: string; runId: string }>;
}

export default async function EvalRunVerdictRoute({ params }: PageProps) {
  const { setId, runId } = await params;
  return <RunVerdictPage setId={setId} runId={runId} />;
}
