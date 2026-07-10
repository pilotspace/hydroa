/**
 * tests/usage-number-format.test.tsx — polish: the usage KPI count cards route
 * through the shared thousands-separator formatter, so real 6–7 digit volumes read
 * "3,840,211" not "3840211" (consistent with the overview KPIs). The frozen
 * console-surfaces/usage suites use small values (3/300/150) that separate to
 * themselves, so this lives in a separate file and asserts the LARGE-value behavior.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { UsageStatsCards, type UsageData } from "@/components/usage/UsageStatsCards";

const BIG: UsageData = {
  total_cost_usd: "184.32",
  total_requests: 15234,
  total_prompt_tokens: 3840211,
  total_completion_tokens: 1920442,
  records: [],
};

describe("UsageStatsCards — count formatting", () => {
  it("renders large counts with locale thousands separators", () => {
    render(<UsageStatsCards isLoading={false} isError={false} error={null} data={BIG} />);
    expect(screen.getByText("15,234")).toBeInTheDocument();
    expect(screen.getByText("3,840,211")).toBeInTheDocument();
    expect(screen.getByText("1,920,442")).toBeInTheDocument();
    // the raw, unseparated forms must be gone
    expect(screen.queryByText("3840211")).not.toBeInTheDocument();
  });
});
