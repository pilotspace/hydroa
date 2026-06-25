/**
 * feature-coverage-verify.test.tsx (BFF project) — v15 MILESTONE-EXIT verification.
 *
 * The consolidated cross-surface gate proving every NEW v15 surface meets the WCAG 2.2 AA
 * + four-state bar TOGETHER (each already gate-PASSed its own dedicated suite; this is the
 * milestone floor, the same shape as v13's ui-ux-verify.test.tsx).
 *
 * Frozen §3 bar (.add/tasks/feature-coverage-verify/TASK.md):
 *   - AppShell: skip-link FIRST focusable (href #main), Primary nav + main#main landmarks,
 *     all 7 nav links role=link + focusable, axe zero serious|critical.
 *   - Each new surface (ModelsPage · TeamsPage · RoutingPage · SettingsPage tabs) renders with
 *     msw data, is axe-clean (serious|critical, color-contrast EXCLUDED — jsdom/no-canvas), and
 *     exposes a representative keyboard-operable + labelled primary control.
 *   - Four-state spot-check: a surface shows role=status (loading) AND role=alert (error).
 *
 * DECLARED RESIDUE (not faked, not a gap fixed here):
 *   - browser-only axe color-contrast ratios + true viewport rendering (no jsdom canvas/layout)
 *     → shared real-browser axe+viewport follow-up carried from v13.
 *   - role-filtered NAV (a `member` sees admin-only links that 403 on navigate) → UX-only, the
 *     gateway enforces RBAC; follow-up task `nav-role-filter`.
 *
 * A real axe serious|critical finding here is a found-and-fixed a11y bug in the owning
 * component, NEVER a relaxed bar.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

import { AppShell } from "@/components/ui";
import { ModelsPage } from "@/components/models/ModelsPage";
import { TeamsPage } from "@/components/teams/TeamsPage";
import { RoutingPage } from "@/components/routing/RoutingPage";
import { SettingsPage } from "@/components/settings/SettingsPage";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

// ── axe helper: serious|critical only, color-contrast disabled (jsdom/no-canvas) ──
async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
}

// ── fixtures (match each surface's FROZEN admin contract, identical to its own suite) ──
const MODELS = {
  object: "list",
  data: [
    { id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000, enabled: true },
    { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", context_length: 200000, enabled: false },
  ],
};
const TEAMS = [
  {
    id: "team-a",
    name: "platform",
    tenant_id: "t1",
    created_at: "2026-01-01T00:00:00Z",
    member_count: 2,
    key_count: 1,
    team_budget_usd: "100.00",
  },
];
const ROUTING = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { fast: ["vendor/a", "vendor/b"] },
  candidates: [
    { alias: "fast", model_id: "vendor/a", state: "closed" },
    { alias: "fast", model_id: "vendor/b", state: "open" },
  ],
};
const CACHE_ON = { enabled: true, semantic_enabled: false };

const NAV_LABELS = ["Usage", "Spend", "API Keys", "Models", "Teams", "Routing", "Settings"];

// ─────────────────────────────────────────────────────────────────────────────
describe("v15 verify · AppShell a11y contract", () => {
  it("test_appshell_skiplink_is_first_focusable", () => {
    const { container } = render(
      <AppShell activePath="/app/models">
        <div>content</div>
      </AppShell>,
    );
    // The §1 Must is "skip-link is the FIRST FOCUSABLE element" — stronger than
    // "first <a>". Query ALL natively/explicitly focusable element types in DOM
    // order, so a preceding focusable <button>/<input>/[tabindex] would FAIL this
    // (it would no longer be the first keyboard stop).
    const focusable = container.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    const first = focusable[0];
    expect(first).toBeTruthy();
    expect(first).toHaveTextContent(/skip to main content/i);
    expect(first).toHaveAttribute("href", "#main");
    // and it resolves by its accessible name to the same node
    expect(screen.getByRole("link", { name: /skip to main content/i })).toBe(first);
  });

  it("test_appshell_landmarks_and_nav_links", () => {
    render(
      <AppShell activePath="/app/models">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(nav).toBeInTheDocument();
    expect(document.querySelector("main#main")).not.toBeNull();

    // all 7 nav links render as role=link, in order, each keyboard-focusable (real <a href>)
    for (const label of NAV_LABELS) {
      const link = within(nav).getByRole("link", { name: label });
      expect(link).toHaveAttribute("href");
      link.focus();
      expect(link).toHaveFocus();
    }
    // the active one carries aria-current=page; the others must NOT (a regression
    // that marked every link active would otherwise slip through)
    expect(within(nav).getByRole("link", { name: "Models" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const label of NAV_LABELS.filter((l) => l !== "Models")) {
      expect(within(nav).getByRole("link", { name: label })).not.toHaveAttribute(
        "aria-current",
      );
    }
  });

  it("test_appshell_axe_clean", async () => {
    const { container } = render(
      <AppShell activePath="/app/spend">
        <h1>Spend</h1>
      </AppShell>,
    );
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("v15 verify · ModelsPage surface", () => {
  it("test_models_surface_axe_and_control", async () => {
    server.use(http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(MODELS)));
    const { container } = render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("GPT-4o");
    expect(await axeSeriousCritical(container)).toEqual([]);

    // representative primary control: the enable/disable Switch — its FULL accessible
    // name is asserted (not just a substring), so degrading the label to a bare id
    // would fail; then it is keyboard-focusable.
    const sw = screen.getByRole("switch", { name: "Enable GPT-4o" });
    sw.focus();
    expect(sw).toHaveFocus();
  });
});

describe("v15 verify · TeamsPage surface", () => {
  it("test_teams_surface_axe_and_control", async () => {
    server.use(http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json(TEAMS)));
    const { container } = render(<TeamsPage />, { wrapper: Wrapper });

    await screen.findByText("platform");
    expect(await axeSeriousCritical(container)).toEqual([]);

    // representative primary control: the create-team action — reachable by name + focusable
    const create = screen.getByRole("button", { name: /create team/i });
    create.focus();
    expect(create).toHaveFocus();
  });
});

describe("v15 verify · RoutingPage surface (read-only)", () => {
  it("test_routing_surface_axe", async () => {
    server.use(http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(ROUTING)));
    const { container } = render(<RoutingPage />, { wrapper: Wrapper });

    // a representative region/heading is reachable
    expect(await screen.findByRole("heading", { name: /retry policy/i })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /candidate/i })).toBeInTheDocument();
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

describe("v15 verify · SettingsPage surface (tabs)", () => {
  it("test_settings_surface_axe_and_tabs", async () => {
    server.use(http.get(`${APP}/api/gw/admin/cache`, () => HttpResponse.json(CACHE_ON)));
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });

    const tablist = await screen.findByRole("tablist");
    expect(within(tablist).getByRole("tab", { name: /cache/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /guardrails/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /sso/i })).toBeInTheDocument();

    // the first (default) tab is reachable by role+name and focusable
    const cacheTab = within(tablist).getByRole("tab", { name: /cache/i });
    cacheTab.focus();
    expect(cacheTab).toHaveFocus();

    // its panel's primary control is labelled + reachable
    expect(await screen.findByRole("switch", { name: /response cache/i })).toBeInTheDocument();

    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("v15 verify · four-state floor (spot-check)", () => {
  it("test_state_patterns_spotcheck", async () => {
    // LOADING -> role=status at T=0, then it must RESOLVE to data (proving the
    // spinner is the query's loading phase, not a permanent role=status node)
    {
      server.use(http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(MODELS)));
      const { unmount } = render(<ModelsPage />, { wrapper: Wrapper });
      expect(screen.getByRole("status")).toBeInTheDocument();
      await screen.findByText("GPT-4o");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      unmount();
    }
    // ERROR -> role=alert + axe-clean in isolation
    {
      server.use(
        http.get(`${APP}/api/gw/admin/models`, () =>
          HttpResponse.json({ title: "boom", status: 500, code: "ERR_INTERNAL" }, { status: 500 }),
        ),
      );
      const { container, unmount } = render(<ModelsPage />, { wrapper: Wrapper });
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // EMPTY -> Empty pattern + axe-clean in isolation
    {
      server.use(
        http.get(`${APP}/api/gw/admin/models`, () =>
          HttpResponse.json({ object: "list", data: [] }),
        ),
      );
      const { container, unmount } = render(<ModelsPage />, { wrapper: Wrapper });
      expect(await screen.findByText(/no models/i)).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
  });
});
