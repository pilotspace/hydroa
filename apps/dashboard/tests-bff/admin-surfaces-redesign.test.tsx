/**
 * admin-surfaces-redesign.test.tsx — RED suite for the v23 admin-surfaces-redesign task.
 *
 * Frozen §3 (presentation-only): Models · Teams · Routing-candidates adopt the shared DataTable
 * block (reusing the ariaLabel + data-slot="data-table" seam shipped in console-surfaces-redesign)
 * WITHOUT changing any data seam or losing a frozen hook; Routing's metric cards + all of Settings
 * stay on the composed Card/forms (documented no-op).
 *
 * TRUE-RED REASON: ModelsPage/TeamsPage/RoutingPage still render raw <Table> (no data-slot), so the
 * [data-slot="data-table"] assertions fail until Build swaps them for DataTable.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { ModelsPage } from "@/components/models/ModelsPage";
import { TeamsPage } from "@/components/teams/TeamsPage";
import { RoutingPage } from "@/components/routing/RoutingPage";
import { SettingsPage } from "@/components/settings/SettingsPage";

function Wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const GW = "http://localhost:3000/api/gw";

describe("ModelsPage — via DataTable", () => {
  it("test_models_render_via_datatable", async () => {
    server.use(
      http.get(`${GW}/admin/models`, () =>
        HttpResponse.json({
          object: "list",
          data: [
            { id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000, enabled: true },
            { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", context_length: 200000, enabled: false },
          ],
        }),
      ),
    );
    const { container } = render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: "Enable GPT-4o" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(container.querySelector('[data-slot="data-table"]')).not.toBeNull();
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
  });

  it("test_models_error_renders_no_table", async () => {
    server.use(
      http.get(`${GW}/admin/models`, () =>
        HttpResponse.json({ type: "about:blank", title: "Forbidden", status: 403 }, { status: 403 }),
      ),
    );
    const { container } = render(<ModelsPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="data-table"]')).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

describe("TeamsPage — via DataTable", () => {
  it("test_teams_render_via_datatable", async () => {
    server.use(
      http.get(`${GW}/admin/teams`, () =>
        HttpResponse.json([
          { id: "t1", name: "platform", tenant_id: "x", created_at: "2026-06-01", member_count: 3, key_count: 2, team_budget_usd: "100.00" },
          { id: "t2", name: "research", tenant_id: "x", created_at: "2026-06-01", member_count: 1, key_count: 0, team_budget_usd: null },
        ]),
      ),
    );
    const { container } = render(<TeamsPage />, { wrapper: Wrapper });

    await screen.findByRole("button", { name: "platform" });
    expect(container.querySelector('[data-slot="data-table"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: /delete team platform/i })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /budget for research/i })).toBeInTheDocument();
    expect(screen.getByText("100.00")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("RoutingPage — candidates via named DataTable", () => {
  it("test_routing_candidates_render_via_datatable", async () => {
    server.use(
      http.get(`${GW}/admin/routing`, () =>
        HttpResponse.json({
          retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
          cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
          model_groups: { fast: ["vendor/a", "vendor/b"] },
          candidates: [
            { alias: "fast", model_id: "vendor/a", state: "closed" },
            { alias: "fast", model_id: "vendor/b", state: "open" },
          ],
        }),
      ),
    );
    render(<RoutingPage />, { wrapper: Wrapper });

    const table = await screen.findByRole("table", { name: /candidate/i });
    expect(table).toHaveAttribute("data-slot", "data-table");
    expect(within(table).getByText("vendor/a")).toBeInTheDocument();
    expect(within(table).getByText(/^open$/i)).toBeInTheDocument();
    // metric cards stay composed (not a table/stat-card)
    expect(screen.getByRole("heading", { name: /retry policy/i })).toBeInTheDocument();
  });

  it("test_routing_empty_candidates_no_table", async () => {
    server.use(
      http.get(`${GW}/admin/routing`, () =>
        HttpResponse.json({
          retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
          cooldown: { enabled: false, threshold: 3, ttl_s: 60, window_s: 120 },
          model_groups: {},
          candidates: [],
        }),
      ),
    );
    render(<RoutingPage />, { wrapper: Wrapper });
    expect(await screen.findByText(/no routing candidates/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("SettingsPage — stays composed (no DataTable)", () => {
  it("test_settings_keeps_shared_forms", async () => {
    server.use(
      http.get(`${GW}/admin/cache`, () => HttpResponse.json({ enabled: true, semantic_enabled: false })),
    );
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("switch", { name: /response cache/i })).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="data-table"]')).toBeNull();
  });
});
