"use client";

/**
 * UsageTable — records table ≤50 rows
 * States: loading (no rows), empty ("No usage records yet"), error (no rows), success (table)
 * Uses real <table>/<tr> — only rendered on success so error/loading states have 0 role="row" elements.
 */

import { UsageData } from "./UsageStatsCards";

interface UsageTableProps {
  isLoading: boolean;
  isError: boolean;
  data: UsageData | undefined;
}

export function UsageTable({ isLoading, isError, data }: UsageTableProps) {
  // Loading or error: render nothing (0 role="row" elements)
  if (isLoading || isError) return null;

  if (!data) return null;

  if (data.records.length === 0) {
    return <p>No usage records yet</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Prompt Tokens</th>
          <th>Completion Tokens</th>
          <th>Cost (USD)</th>
          <th>Status</th>
          <th>Date</th>
        </tr>
      </thead>
      <tbody>
        {data.records.map((rec) => (
          <tr key={rec.id}>
            <td>{rec.model_id}</td>
            <td>{rec.prompt_tokens}</td>
            <td>{rec.completion_tokens}</td>
            <td>{rec.cost_usd}</td>
            <td>{rec.status}</td>
            <td>{rec.created_at}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
