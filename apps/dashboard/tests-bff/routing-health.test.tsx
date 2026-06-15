/**
 * tests-bff/routing-health.test.tsx — RED suite for v15 task routing-health-ui.
 *
 * A NEW owner/admin `/routing` READ-ONLY page over ONE frozen gateway contract
 * via the BFF seam (bffGet, verbatim plain object — NO {data} envelope):
 *   GET /admin/routing -> { retry_policy, cooldown, model_groups, candidates } | 403 | 500
 *
 * No mutation exists on this contract — the page only reads + displays. Circuit
 * state (open|half_open|closed|unknown) renders as a labelled Badge whose TEXT is
 * the state word (a11y: state is never color-only).
 *
 * RED before build: `@/components/routing/RoutingPage` does not exist → the file
 * fails at collect (MODULE_NOT_FOUND), the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/routing/RoutingPage.tsx ──────
import { RoutingPage } from "@/components/routing/RoutingPage";
import { AppShell } from "@/components/ui";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

const FULL = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { fast: ["vendor/a", "vendor/b"] },
  candidates: [
    { alias: "fast", model_id: "vendor/a", state: "closed" },
    { alias: "fast", model_id: "vendor/b", state: "open" },
  ],
};

const DISABLED = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: false, threshold: 0, ttl_s: 60, window_s: 120 },
  model_groups: { fast: ["vendor/a"] },
  candidates: [{ alias: "fast", model_id: "vendor/a", state: "closed" }],
};

const UNKNOWN = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { fast: ["vendor/a"] },
  candidates: [{ alias: "fast", model_id: "vendor/a", state: "unknown" }],
};

const HALF_OPEN = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { fast: ["vendor/a"] },
  candidates: [{ alias: "fast", model_id: "vendor/a", state: "half_open" }],
};

const EMPTY = {
  retry_policy: { max_retries: 4, backoff_base_s: 0.75 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: {},
  candidates: [],
};

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const routingGet = (body: unknown = FULL) =>
  http.get(`${APP}/api/gw/admin/routing`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

// ── full render ─────────────────────────────────────────────────────────────────
describe("RoutingPage — full config + candidates", () => {
  it("test_renders_full_config_and_candidates", async () => {
    server.use(routingGet());
    render(<RoutingPage />, { wrapper: Wrapper });

    // retry policy (heading, not the page description prose which also says "retry policy")
    expect(await screen.findByRole("heading", { name: /retry policy/i })).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // max_retries
    expect(screen.getByText("0.75")).toBeInTheDocument(); // backoff_base_s

    // cooldown enabled + numbers
    expect(screen.getByText(/^enabled$/i)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // threshold
    expect(screen.getByText("60")).toBeInTheDocument(); // ttl_s
    expect(screen.getByText("120")).toBeInTheDocument(); // window_s

    // model group block: the alias renders (in the groups list + the candidates table)
    expect(screen.getByRole("heading", { name: /model groups/i })).toBeInTheDocument();
    expect(screen.getAllByText("fast").length).toBeGreaterThanOrEqual(1);

    // candidates table with readable state text
    const table = screen.getByRole("table", { name: /candidate/i });
    expect(within(table).getByText("vendor/a")).toBeInTheDocument();
    expect(within(table).getByText("vendor/b")).toBeInTheDocument();
    expect(within(table).getByText(/^closed$/i)).toBeInTheDocument();
    expect(within(table).getByText(/^open$/i)).toBeInTheDocument();
  });

  it("test_cooldown_disabled_indicated", async () => {
    server.use(routingGet(DISABLED));
    render(<RoutingPage />, { wrapper: Wrapper });

    expect(await screen.findByText(/disabled/i)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /candidate/i });
    expect(within(table).getByText(/^closed$/i)).toBeInTheDocument();
  });

  it("test_state_is_readable_not_color_only", async () => {
    server.use(routingGet(FULL));
    render(<RoutingPage />, { wrapper: Wrapper });

    const table = await screen.findByRole("table", { name: /candidate/i });
    // the state word is rendered as TEXT (not conveyed by color alone)
    expect(within(table).getByText(/^open$/i)).toBeInTheDocument();
  });

  it("test_half_open_state_rendered", async () => {
    server.use(routingGet(HALF_OPEN));
    render(<RoutingPage />, { wrapper: Wrapper });

    const table = await screen.findByRole("table", { name: /candidate/i });
    // half_open is a contract enum value — readable as TEXT, no page error
    expect(within(table).getByText(/^half_open$/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_unknown_state_no_page_error", async () => {
    server.use(routingGet(UNKNOWN));
    render(<RoutingPage />, { wrapper: Wrapper });

    const table = await screen.findByRole("table", { name: /candidate/i });
    expect(within(table).getByText(/^unknown$/i)).toBeInTheDocument();
    // "unknown" is a normal fail-open value — the PAGE is not in an error state
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// ── error + empty ─────────────────────────────────────────────────────────────────
describe("RoutingPage — error + empty states", () => {
  it("test_member_forbidden_403", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/routing`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<RoutingPage />, { wrapper: Wrapper });

    expect(await screen.findByRole("alert")).toHaveTextContent(/forbidden/i);
    // NO config leaked on the role-denied path — not even an empty card shell
    expect(screen.queryByRole("heading", { name: /retry policy/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /cooldown/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /model groups/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /candidate circuit state/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("test_get_failure_500_alert", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/routing`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    render(<RoutingPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("test_empty_config_empty_state", async () => {
    server.use(routingGet(EMPTY));
    render(<RoutingPage />, { wrapper: Wrapper });

    // an Empty affordance for BOTH empty model_groups and (no) candidates — not a blank-table crash
    expect(await screen.findByText(/no routing candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/no model groups configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    // retry_policy block still renders (always present per contract)
    expect(screen.getByRole("heading", { name: /retry policy/i })).toBeInTheDocument();
  });
});

// ── a11y + nav ────────────────────────────────────────────────────────────────────
describe("RoutingPage — axe + nav", () => {
  it("test_routing_axe_clean", async () => {
    server.use(routingGet(FULL));
    const { container } = render(<RoutingPage />, { wrapper: Wrapper });
    await screen.findByRole("table", { name: /candidate/i });
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_nav_exposes_routing", () => {
    render(
      <AppShell activePath="/routing">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    const link = within(nav).getByRole("link", { name: /routing/i });
    expect(link).toHaveAttribute("href", "/routing");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
