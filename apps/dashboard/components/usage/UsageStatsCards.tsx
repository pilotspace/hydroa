"use client";

/**
 * UsageStatsCards — four aggregate stat cards from usage data
 * States: loading (animate-pulse), error (problem+json title), success
 */

import { BffError } from "@/lib/bff-client";
import { Loading, ErrorState, StatCard } from "@/components/ui";

export interface UsageRecord {
  id: string;
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  status: number;
  created_at: string;
}

export interface UsageData {
  total_cost_usd: string;
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  records: UsageRecord[];
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

interface UsageStatsCardsProps {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data: UsageData | undefined;
}

export function UsageStatsCards({
  isLoading,
  isError,
  error,
  data,
}: UsageStatsCardsProps) {
  if (isLoading) {
    return (
      <Loading
        label="Loading usage statistics"
        data-testid="loading"
        className="animate-pulse"
      />
    );
  }

  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  if (!data) return null;

  const cards = [
    { label: "Total Requests", value: String(data.total_requests) },
    { label: "Total Prompt Tokens", value: String(data.total_prompt_tokens) },
    { label: "Total Completion Tokens", value: String(data.total_completion_tokens) },
    { label: "Total Cost (USD)", value: data.total_cost_usd },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {cards.map((card) => (
        <StatCard key={card.label} label={card.label} value={card.value} />
      ))}
    </div>
  );
}
