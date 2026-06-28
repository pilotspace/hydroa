/**
 * tests/table-timestamps.test.tsx — RED for human-readable timestamps across the
 * admin read tables, plus the AlertsTable payload column.
 *
 * Before build: Alerts/Audit/Usage rendered `created_at` via a bare accessorKey =
 * raw ISO 8601; AlertsTable fetched `payload` but never showed it. These assert the
 * When/Date cells are humanized (raw ISO gone) and that the alert payload is surfaced.
 *
 * Pure render tests — the tables take their data as a prop (no MSW / no router).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { AlertsTable } from "@/components/alerts/AlertsTable";
import { AuditTable } from "@/components/audit/AuditTable";
import { UsageTable } from "@/components/usage/UsageTable";

describe("admin read tables — human-readable timestamps", () => {
  it("AlertsTable humanizes When and surfaces the payload", () => {
    render(
      <AlertsTable
        data={{
          items: [
            {
              id: "a1",
              event_type: "circuit_breaker_open",
              payload: { provider: "anthropic" },
              created_at: "2026-06-10T12:05:00Z",
              delivered: false,
              delivered_at: null,
            },
          ],
          total: 1,
        }}
      />,
    );
    // payload is now visible (was fetched-but-hidden)
    expect(screen.getByText(/anthropic/)).toBeInTheDocument();
    // timestamp humanized; raw ISO gone
    expect(screen.queryByText("2026-06-10T12:05:00Z")).toBeNull();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });

  it("AuditTable humanizes the When column", () => {
    render(
      <AuditTable
        data={{
          items: [
            {
              id: "x1",
              actor_email: "owner@example.com",
              action: "key.create",
              target_type: "api_key",
              target_id: "k1",
              result: "success",
              metadata: {},
              created_at: "2026-06-10T12:05:00Z",
            },
          ],
          total: 1,
        }}
      />,
    );
    expect(screen.queryByText("2026-06-10T12:05:00Z")).toBeNull();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });

  it("UsageTable humanizes the Date column", () => {
    render(
      <UsageTable
        isLoading={false}
        isError={false}
        data={{
          total_cost_usd: "0.01",
          total_requests: 1,
          total_prompt_tokens: 1,
          total_completion_tokens: 1,
          records: [
            {
              id: "u1",
              model_id: "openai/gpt-4o",
              prompt_tokens: 1,
              completion_tokens: 1,
              cost_usd: "0.01",
              status: 200,
              created_at: "2026-06-10T00:00:00Z",
            },
          ],
        }}
      />,
    );
    expect(screen.queryByText("2026-06-10T00:00:00Z")).toBeNull();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });
});
