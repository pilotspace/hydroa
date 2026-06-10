"use client";

/**
 * UsageStatsCards — four aggregate stat cards from usage data
 * States: loading (animate-pulse), error (problem+json title), success
 */

import { ApiError } from "@/lib/api-client";

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
  if (err instanceof ApiError) return err.problem.title;
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
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading usage statistics"
        data-testid="loading"
        className="animate-pulse"
      >
        <span>Loading…</span>
      </div>
    );
  }

  if (isError) {
    return <p role="alert">{getErrorTitle(error)}</p>;
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
        <div key={card.label} className="rounded border p-4">
          <p className="text-sm text-gray-500">{card.label}</p>
          <p className="text-2xl font-semibold">{card.value}</p>
        </div>
      ))}
    </div>
  );
}
