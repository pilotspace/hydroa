/**
 * tests/audit.test.tsx — RED suite for the audit-log-surface dashboard page.
 *
 * AuditPage fetches GET /admin/audit (via the BFF catch-all /api/gw/admin/audit) and renders
 * the tenant-scoped audit event history in a table: Actor, Action, Target, Result, When.
 * It handles four states: success / empty / error / loading.
 *
 * RED before Build: `@/components/audit/AuditPage` does not exist yet → MODULE_NOT_FOUND, the
 * established true-red convention. Frozen contract (§3): response is { items:[...], total:int }.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { AuditPage } from "@/components/audit/AuditPage";

const APP = "http://localhost:3000";

const AUDIT_RESPONSE = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000002",
      actor_email: "admin@example.com",
      action: "api_key.create",
      target_type: "api_key",
      target_id: "key-abc",
      result: "success",
      metadata: { key_name: "prod-key" },
      created_at: "2026-06-10T12:05:00Z",
    },
    {
      id: "00000000-0000-0000-0000-000000000001",
      actor_email: "owner@example.com",
      action: "user.invite",
      target_type: "user",
      target_id: "user-xyz",
      result: "success",
      metadata: {},
      created_at: "2026-06-10T12:00:00Z",
    },
  ],
  total: 2,
};

const AUDIT_EMPTY_RESPONSE = { items: [], total: 0 };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderAudit() {
  return render(<AuditPage />, { wrapper: Wrapper });
}

/** Scope assertions to the audit section (CONVENTIONS.md within(<section>) rule). */
function section() {
  return screen.getByRole("region", { name: /audit/i });
}

describe("AuditPage", () => {
  it("test_audit_renders_table", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/audit`, () => HttpResponse.json(AUDIT_RESPONSE)),
    );

    renderAudit();

    await waitFor(() => {
      expect(within(section()).getByText(/api_key\.create/i)).toBeInTheDocument();
    });
    const sec = section();
    // both rows present
    expect(within(sec).getByText(/user\.invite/i)).toBeInTheDocument();
    // actor emails visible
    expect(within(sec).getByText(/admin@example\.com/i)).toBeInTheDocument();
    expect(within(sec).getByText(/owner@example\.com/i)).toBeInTheDocument();
  });

  it("test_audit_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/audit`, () => HttpResponse.json(AUDIT_EMPTY_RESPONSE)),
    );

    renderAudit();

    await waitFor(() => {
      expect(within(section()).getByText(/no audit events yet/i)).toBeInTheDocument();
    });
    // empty is not an error
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_audit_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/audit`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderAudit();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    // no data rows on error
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_audit_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/audit`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderAudit();

    await waitFor(() => {
      const spinner =
        within(section()).queryByRole("status") ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_audit_one_h1", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/audit`, () => HttpResponse.json(AUDIT_RESPONSE)),
    );

    const { container } = renderAudit();

    await waitFor(() => {
      expect(within(section()).getByText(/api_key\.create/i)).toBeInTheDocument();
    });

    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
  });

  it("test_audit_axe_no_violations", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/audit`, () => HttpResponse.json(AUDIT_RESPONSE)),
    );

    const { container } = renderAudit();

    await waitFor(() => {
      expect(within(section()).getByText(/api_key\.create/i)).toBeInTheDocument();
    });

    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const violations = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(violations).toHaveLength(0);
  });
});
