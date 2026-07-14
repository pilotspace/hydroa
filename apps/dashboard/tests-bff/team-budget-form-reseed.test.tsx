/**
 * tests-bff/team-budget-form-reseed.test.tsx — RED/GREEN for the second half of the
 * TeamBudgetForm stale-save defect (audit-remediation, Dashboard item 1).
 *
 * TeamBudgetForm.tsx:37 seeded `draft` from a plain mount-only useState(team.team_
 * budget_usd ?? ""), so once mounted the input never re-reads a changed `team` prop.
 * Paired with TeamsPage's DataTable getRowId fix (see tests/design-system/data-table-
 * get-row-id.test.tsx, which covers the row-IDENTITY half of this defect), this file
 * covers the remaining SAME-identity half: the server value for the SAME team changes
 * underneath the form (e.g. another admin's edit lands via a background refetch)
 * while this admin's row never remounts. Two behaviors are pinned:
 *
 *   1. If the admin has NOT started an in-flight edit, the form must re-seed to the
 *      new server value — otherwise a later click of Save PATCHes the STALE value,
 *      silently clobbering the other admin's more recent change ("stale save").
 *   2. If the admin HAS started typing an unsaved edit, an unrelated external value
 *      change must NEVER clobber it (design-for-failure: re-seed only on an
 *      untouched value, never mid-keystroke).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import { TeamBudgetForm } from "@/components/teams/TeamBudgetForm";
import type { TeamResponse } from "@/components/teams/types";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

const TEAM: TeamResponse = {
  id: "team-a",
  name: "platform",
  tenant_id: "t1",
  created_at: "2026-01-01T00:00:00Z",
  member_count: 1,
  key_count: 0,
  team_budget_usd: "100.00",
};

describe("TeamBudgetForm — re-seed on external value change (stale-save fix)", () => {
  it("test_untouched_input_reseeds_to_a_new_server_value", () => {
    const { rerender } = render(<TeamBudgetForm team={TEAM} />, { wrapper: Wrapper });
    const input = screen.getByRole("textbox", { name: /budget for platform/i });
    expect(input).toHaveValue("100.00");

    // The SAME team's server value changes externally (another admin's edit landing
    // via a background refetch) — the admin viewing this row never touched the field.
    rerender(<TeamBudgetForm team={{ ...TEAM, team_budget_usd: "250.00" }} />);

    // The untouched field must reflect the new authoritative value, not the stale
    // one it mounted with — otherwise a later Save would PATCH the old figure back.
    expect(input).toHaveValue("250.00");
  });

  it("test_in_flight_edit_survives_an_unrelated_external_value_change", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TeamBudgetForm team={TEAM} />, { wrapper: Wrapper });
    const input = screen.getByRole("textbox", { name: /budget for platform/i });

    // The admin starts typing a NEW draft — has NOT saved yet.
    await user.clear(input);
    await user.type(input, "42.00");
    expect(input).toHaveValue("42.00");

    // An unrelated external change lands for the SAME team while the admin is mid-edit.
    rerender(<TeamBudgetForm team={{ ...TEAM, team_budget_usd: "250.00" }} />);

    // The admin's own in-flight keystrokes must NEVER be clobbered by that re-render.
    expect(input).toHaveValue("42.00");
  });
});
