/**
 * tests/joined-workspace-callout.test.tsx — RED suite for the "you joined
 * {tenant}" onboarding confirmation (domain-claims-console TASK.md §3 CONTRACT
 * — FROZEN @ v1, M6, R3):
 * `components/onboarding/JoinedWorkspaceCallout.tsx`.
 *
 * landing shows JoinedWorkspaceCallout IFF (?joined=1 OR signup joined_existing_tenant)
 *   tenantName <- session/tenant context (already loaded; NO extra fetch)
 *   read-only · dismissible (local UI) · never mutates server state (R3)
 *
 * The lowest-confidence §1 assumption (M6 tenant-name source) is pinned here
 * against a MOCKED session context that carries the tenant display name (an
 * additive `tenant_name` field on the existing GET /api/auth/me shape) — the
 * build confirms the real context shape at first wiring (TASK.md §3 mitigation).
 *
 * RED failure mode: `@/components/onboarding/JoinedWorkspaceCallout` does not
 * exist yet -> the import fails at collect (MODULE_NOT_FOUND), the established
 * true-red convention in this repo.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { useSearchParams } from "next/navigation";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/onboarding/JoinedWorkspaceCallout.tsx ──
import { JoinedWorkspaceCallout } from "@/components/onboarding/JoinedWorkspaceCallout";

const APP = "http://localhost:3000";
const ME_URL = `${APP}/api/auth/me`;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

// Stabilize the shared next/navigation useSearchParams mock (tests/setup.ts) to a
// single instance per test — mirrors tests-bff/tenant-settings.test.tsx's own
// established precedent for a component that reads the URL search params, so a
// fresh-reference-per-call mock can't cause an unstable-dependency re-render loop.
function setJoinedParam(value: string | null) {
  const params = new URLSearchParams();
  if (value !== null) params.set("joined", value);
  vi.mocked(useSearchParams).mockReturnValue(params as ReturnType<typeof useSearchParams>);
}

beforeEach(() => {
  setJoinedParam(null);
});

describe("JoinedWorkspaceCallout — named confirmation (M6)", () => {
  it("test_joined_callout_named", async () => {
    // covers: M6
    setJoinedParam("1");
    let meCallCount = 0;
    server.use(
      http.get(ME_URL, () => {
        meCallCount += 1;
        return HttpResponse.json({
          user_id: "00000000-0000-0000-0000-000000000001",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "ada@acme.io",
          role: "member",
          exp: null,
          // additive field on the already-loaded session context — the source
          // the frozen contract names for the tenant display name (no extra fetch).
          tenant_name: "Acme, Inc.",
        });
      }),
    );

    render(<JoinedWorkspaceCallout />, { wrapper: Wrapper });

    const callout = await screen.findByText(/you joined the acme, inc\. workspace/i);
    expect(callout).toBeInTheDocument();

    const callsBeforeDismiss = meCallCount;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /dismiss|close/i }));

    // read-only — dismiss is local UI only, no server call, no state change.
    expect(meCallCount).toBe(callsBeforeDismiss);
    expect(
      screen.queryByText(/you joined the acme, inc\. workspace/i),
    ).not.toBeInTheDocument();
  });
});

describe("JoinedWorkspaceCallout — no signal, no callout (R3)", () => {
  it("test_no_signal_no_callout", async () => {
    // covers: R3
    setJoinedParam(null);
    let meCallCount = 0;
    server.use(
      http.get(ME_URL, () => {
        meCallCount += 1;
        return HttpResponse.json({
          user_id: "00000000-0000-0000-0000-000000000001",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "ada@acme.io",
          role: "member",
          exp: null,
          tenant_name: "Acme, Inc.",
        });
      }),
    );

    render(<JoinedWorkspaceCallout />, { wrapper: Wrapper });

    // give any (wrongly) fired async fetch a tick to resolve before asserting absence.
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.queryByText(/you joined/i)).not.toBeInTheDocument();
    // R3 / advisory-only: absent the signal, the surface never even reads the
    // session context — no fetch, no re-POST, no routing influence.
    expect(meCallCount).toBe(0);
  });
});
