/**
 * tests/design-system/governance-redesign.test.tsx — RED structural suite.
 *
 * Verifies the governance-redesign build contract (spec §3 v1, frozen):
 *  - PageHeader primitive adopted by every governance page (one h1 inside <header>)
 *  - KeysPage: data-testid="keys-hero" (active-key count) + tablist Keys/Rate limits/Bandwidth
 *  - RoutingPage: data-testid="routing-hero" (strategy + N/M healthy) + tablist Overview/Editor
 *  - SettingsPage: PageHeader h1 wrapper (h1 inside a <div> inside <header>) + 4-tab structure unchanged
 *  - Members / Alerts / Audit: PageHeader wraps h1 in <header>; no hero testids
 *
 * RED before build:
 *   - test_keys_hero_and_tabs, test_routing_hero_and_tabs_owner → no keys-hero / routing-hero
 *     testids, no tablist ("Unable to find role=tablist")
 *   - test_each_governance_page_uses_page_header (members/alerts/audit) → h1 is NOT inside a
 *     <header> element (bare h1 in <section>); test fails on `.closest("header")` being null
 *   - test_keys_dialogs_outside_tabs, test_routing_editor_tab_hidden_for_viewer → depend on
 *     tablist that does not yet exist
 *   - test_settings_pageheader_keeps_four_tabs → h1.parentElement is <header> not <div>
 *     (hand-rolled header; PageHeader would wrap h1 in a flex <div>)
 *
 * Once the builder adds PageHeader + hero testids + tabbed IA to each page,
 * these tests will turn GREEN without any modification to this file.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

// ── PageHeader already exists — import succeeds ───────────────────────────────
import { PageHeader } from "@/components/ui/page-header";

// ── Governance page components (already built; new structure not yet added) ───
import { KeysPage } from "@/components/keys/KeysPage";
import { MembersPage } from "@/components/members/MembersPage";
import { RoutingPage } from "@/components/routing/RoutingPage";
import { AlertsPage } from "@/components/alerts/AlertsPage";
import { AuditPage } from "@/components/audit/AuditPage";
import { SettingsPage } from "@/components/settings/SettingsPage";

const APP = "http://localhost:3000";

// ── MSW response fixtures (shapes from frozen contracts) ──────────────────────

const KEYS_NOMINAL = [
  {
    key_id: "kid-1",
    name: "prod-key",
    prefix: "sk-1a2b",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
  },
];

const RL_NOMINAL = {
  keys: [
    {
      key_id: "00000000-0000-0000-0000-000000000001",
      name: "prod-key",
      rpm_limit: 60,
      tpm_limit: 1000,
      rpm_current: 3,
      tpm_current: 150.0,
    },
  ],
};

const BW_NOMINAL = {
  enabled: true,
  rate_per_sec: 100,
  burst: 200,
  keys: [
    { key_id: "00000000-0000-0000-0000-000000000001", name: "prod-key", level: 150 },
  ],
};

// Routing fixture — includes all fields used by RoutingPage + RoutingEditor
const ROUTING_NOMINAL = {
  routing_strategy: "ordered",
  retry_policy: { max_retries: 3, backoff_base_s: 0.5 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { gpt: ["vendor/a"] },
  candidates: [
    { alias: "gpt", model_id: "vendor/a", state: "closed" },
  ],
  deployments: {
    gpt: [{ model_id: "vendor/a", weight: 1, rpm_limit: null, tpm_limit: null }],
  },
};

const USERS_NOMINAL = {
  users: [
    { id: "00000000-0000-0000-0000-000000000001", email: "alice@acme.io", role: "owner" },
    { id: "00000000-0000-0000-0000-000000000002", email: "bob@acme.io", role: "member" },
  ],
};

const ALERTS_NOMINAL = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      event_type: "circuit_breaker_open",
      payload: { provider: "anthropic" },
      created_at: "2026-06-10T12:05:00Z",
      delivered: false,
      delivered_at: null,
    },
  ],
  total: 1,
};

const AUDIT_NOMINAL = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      actor_email: "admin@example.com",
      action: "api_key.create",
      target_type: "api_key",
      target_id: "key-abc",
      result: "success",
      metadata: {},
      created_at: "2026-06-10T12:05:00Z",
    },
  ],
  total: 1,
};

const CACHE_NOMINAL = { enabled: true, semantic_enabled: false };

// ── Harness helpers ───────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>
  );
}

/** /api/auth/me handler for RoutingPage's useCurrentUser hook */
function meHandler(role: string) {
  return http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "u-1",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      email: `${role}@acme.io`,
      role,
      exp: Math.floor(Date.now() / 1000) + 86400,
    }),
  );
}

/** Set up all handlers needed by KeysPage (keys + panels rendered flat today) */
function useKeysHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json(KEYS_NOMINAL)),
    http.get(`${APP}/api/gw/admin/ratelimits`, () => HttpResponse.json(RL_NOMINAL)),
    http.get(`${APP}/api/gw/admin/bandwidth`, () => HttpResponse.json(BW_NOMINAL)),
  );
}

function useRoutingHandlers(role: string) {
  server.use(
    meHandler(role),
    http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(ROUTING_NOMINAL)),
  );
}

function useMembersHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_NOMINAL)),
  );
}

function useAlertsHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/alerts`, () => HttpResponse.json(ALERTS_NOMINAL)),
  );
}

function useAuditHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/audit`, () => HttpResponse.json(AUDIT_NOMINAL)),
  );
}

function useSettingsHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/cache`, () => HttpResponse.json(CACHE_NOMINAL)),
  );
}

// ── PageHeader smoke (primitive already exists) ───────────────────────────────

describe("PageHeader primitive — governance baseline", () => {
  it("test_page_header_renders_h1_with_title_and_description", () => {
    render(
      <PageHeader
        title="API Keys"
        description="Manage your tenant API keys, rate limits and bandwidth."
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "API Keys" })).toBeInTheDocument();
    expect(
      screen.getByText(/manage your tenant api keys/i),
    ).toBeInTheDocument();
    expect(document.querySelectorAll("h1")).toHaveLength(1);
  });

  it("test_page_header_actions_slot_renders_children", () => {
    render(
      <PageHeader
        title="API Keys"
        actions={<button type="button">Create key</button>}
      />,
    );
    expect(screen.getByRole("button", { name: "Create key" })).toBeInTheDocument();
  });

  it("test_page_header_titleId_sets_id_on_h1", () => {
    render(<PageHeader title="Members" titleId="members-heading" />);
    const h1 = screen.getByRole("heading", { level: 1, name: "Members" });
    expect(h1).toHaveAttribute("id", "members-heading");
  });
});

// ── Each governance page uses PageHeader (RED for members / alerts / audit) ───
//
// KeysPage, RoutingPage, SettingsPage already have h1 inside a hand-rolled
// <header> element, so those sub-tests pass green; but the suite as a whole is
// RED because MembersPage / AlertsPage / AuditPage place h1 directly inside
// <section> with NO intervening <header> element — PageHeader adds that wrapper.
//
// After build: all 6 pages render h1 via PageHeader → h1 is always inside <header>.

describe("test_each_governance_page_uses_page_header", () => {
  it("keys — exactly one h1 'API Keys' inside a <header>", async () => {
    useKeysHandlers();
    render(<KeysPage />, { wrapper: Wrapper });

    const h1 = await screen.findByRole("heading", { level: 1, name: /api keys/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // PageHeader wraps h1 in <header>. Currently hand-rolled header already wraps it.
    expect(h1.closest("header")).not.toBeNull();
  });

  it("members — exactly one h1 'Members' inside a <header> [RED today: bare h1 in <section>]", async () => {
    useMembersHandlers();
    render(<MembersPage callerRole="owner" />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /members/i })).toBeInTheDocument();
    });

    const h1 = screen.getByRole("heading", { level: 1, name: /members/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // RED today: MembersPage renders bare <h1> inside <section>, not inside <header>.
    // After build: PageHeader wraps the h1 in <header> inside the section.
    expect(h1.closest("header")).not.toBeNull();
  });

  it("routing — exactly one h1 'Routing health' inside a <header>", async () => {
    useRoutingHandlers("owner");
    render(<RoutingPage />, { wrapper: Wrapper });

    const h1 = await screen.findByRole("heading", { level: 1, name: /routing health/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    expect(h1.closest("header")).not.toBeNull();
  });

  it("alerts — exactly one h1 'Alerts' inside a <header> [RED today: bare h1 in <section>]", async () => {
    useAlertsHandlers();
    render(<AlertsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /^alerts$/i })).toBeInTheDocument();
    });

    const h1 = screen.getByRole("heading", { level: 1, name: /^alerts$/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // RED today: AlertsPage renders bare <h1> inside <section>.
    expect(h1.closest("header")).not.toBeNull();
  });

  it("audit — exactly one h1 'Audit Log' inside a <header> [RED today: bare h1 in <section>]", async () => {
    useAuditHandlers();
    render(<AuditPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /audit log/i })).toBeInTheDocument();
    });

    const h1 = screen.getByRole("heading", { level: 1, name: /audit log/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // RED today: AuditPage renders bare <h1> inside <section>.
    expect(h1.closest("header")).not.toBeNull();
  });

  it("settings — exactly one h1 'Settings' with PageHeader flex-div wrapper [RED today: hand-rolled]", async () => {
    useSettingsHandlers();
    render(<SettingsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /^settings$/i })).toBeInTheDocument();
    });

    const h1 = screen.getByRole("heading", { level: 1, name: /^settings$/i });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // PageHeader wraps h1 in <div className="flex flex-wrap items-center justify-between gap-4">
    // inside <header>. Hand-rolled header places h1 as a DIRECT child of <header> (no <div>).
    // RED today: h1.parentElement is <header>, not <div>.
    // After build (PageHeader): h1.parentElement is <div> (the flex row inside the header).
    expect(h1.parentElement?.tagName.toLowerCase()).toBe("div");
  });
});

// ── Keys: hero + tabbed IA ────────────────────────────────────────────────────

describe("test_keys_hero_and_tabs", () => {
  it("keys-hero testid present with active-key count, tablist with 3 triggers", async () => {
    const user = userEvent.setup();
    useKeysHandlers();
    render(<KeysPage />, { wrapper: Wrapper });

    // Wait for page to settle — h1 is unique and always present
    await screen.findByRole("heading", { level: 1, name: /api keys/i });

    // RED: keys-hero testid does not exist yet.
    const hero = await screen.findByTestId("keys-hero");
    // Hero shows the active-key count (1 non-revoked key in KEYS_NOMINAL)
    expect(within(hero).getByText(/1/)).toBeInTheDocument();

    // RED: no tablist on KeysPage yet.
    const tablist = screen.getByRole("tablist");
    expect(tablist).toBeInTheDocument();

    // Keys tab is selected by default
    const keysTab = within(tablist).getByRole("tab", { name: /^keys$/i });
    expect(keysTab).toHaveAttribute("aria-selected", "true");

    // Click Rate limits tab → RatelimitsPanel region mounts
    const rlTab = within(tablist).getByRole("tab", { name: /rate limits/i });
    await user.click(rlTab);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /rate-limit/i })).toBeInTheDocument();
    });

    // Click Bandwidth tab → BandwidthPanel region mounts
    const bwTab = within(tablist).getByRole("tab", { name: /bandwidth/i });
    await user.click(bwTab);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /bandwidth/i })).toBeInTheDocument();
    });
  });
});

// ── Keys: dialogs always accessible regardless of active tab ──────────────────

describe("test_keys_dialogs_outside_tabs", () => {
  it("Create-key dialog opens from a non-default tab (Rate limits)", async () => {
    const user = userEvent.setup();
    useKeysHandlers();
    render(<KeysPage />, { wrapper: Wrapper });

    // Wait for page to settle (keys table or empty state)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create key/i })).toBeInTheDocument();
    });

    // RED: no tablist yet; this assertion makes the test fail for the right reason.
    const tablist = await screen.findByRole("tablist");

    // Navigate away from default Keys tab to Rate limits
    await user.click(within(tablist).getByRole("tab", { name: /rate limits/i }));

    // Create key button must still be visible (it lives outside the tab panels)
    const createBtn = screen.getByRole("button", { name: /create key/i });
    expect(createBtn).toBeInTheDocument();

    // Opening the dialog works regardless of active tab
    await user.click(createBtn);
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});

// ── Routing: hero + tabbed IA (owner) ────────────────────────────────────────

describe("test_routing_hero_and_tabs_owner", () => {
  it("routing-hero testid + tablist Overview(default)/Editor; Editor shows RoutingEditor", async () => {
    const user = userEvent.setup();
    useRoutingHandlers("owner");
    render(<RoutingPage />, { wrapper: Wrapper });

    // Wait for routing data (h1 rendered only after data loads)
    await screen.findByRole("heading", { level: 1, name: /routing health/i });

    // RED: routing-hero testid does not exist yet.
    const hero = await screen.findByTestId("routing-hero");

    // Hero shows the routing_strategy value and a healthy/candidates summary
    expect(within(hero).getByText(/ordered/i)).toBeInTheDocument();
    // "N healthy / M candidates" — 1 closed candidate → "1 / 1"
    expect(within(hero).getByText(/1\s*\/\s*1/)).toBeInTheDocument();

    // RED: no tablist on RoutingPage yet.
    const tablist = screen.getByRole("tablist");

    // Overview tab is selected by default
    const overviewTab = within(tablist).getByRole("tab", { name: /overview/i });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");

    // Editor tab present (owner role)
    const editorTab = within(tablist).getByRole("tab", { name: /editor/i });
    expect(editorTab).toBeInTheDocument();

    // Click Editor → RoutingEditor mounts (strategy select becomes available)
    await user.click(editorTab);
    await waitFor(() => {
      expect(screen.getByLabelText(/routing strategy/i)).toBeInTheDocument();
    });
  });
});

// ── Routing: Editor tab hidden for viewer ─────────────────────────────────────

describe("test_routing_editor_tab_hidden_for_viewer", () => {
  it("member/viewer sees tablist with only Overview; no Editor tab; Overview cards render", async () => {
    useRoutingHandlers("member");
    render(<RoutingPage />, { wrapper: Wrapper });

    // Wait for routing data to load
    await screen.findByRole("heading", { level: 1, name: /routing health/i });

    // RED: no tablist on RoutingPage yet.
    const tablist = screen.getByRole("tablist");
    expect(tablist).toBeInTheDocument();

    // Overview present (default)
    expect(
      within(tablist).getByRole("tab", { name: /overview/i }),
    ).toBeInTheDocument();

    // No Editor tab for viewer
    expect(
      within(tablist).queryByRole("tab", { name: /editor/i }),
    ).not.toBeInTheDocument();

    // Overview candidate table still resolves (inside Overview tab panel)
    await waitFor(() => {
      expect(
        screen.getByRole("table", { name: /routing candidates/i }),
      ).toBeInTheDocument();
    });
  });
});

// ── Settings: PageHeader swap keeps 4-tab structure ──────────────────────────

describe("test_settings_pageheader_keeps_four_tabs", () => {
  it("Settings h1 rendered via PageHeader (h1 inside flex div) + Cache/Guardrails/SSO/Provider Keys tabs", async () => {
    useSettingsHandlers();
    render(<SettingsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /^settings$/i }),
      ).toBeInTheDocument();
    });

    // PageHeader description present
    expect(
      screen.getByText(/manage cache, guardrails, sso, and provider keys/i),
    ).toBeInTheDocument();

    // RED: hand-rolled <header> places h1 as direct child of <header>;
    // PageHeader wraps it in a flex <div> inside <header>.
    const h1 = screen.getByRole("heading", { level: 1, name: /^settings$/i });
    expect(h1.parentElement?.tagName.toLowerCase()).toBe("div");

    // The 4 tabs are preserved
    const tablist = screen.getByRole("tablist");
    expect(within(tablist).getByRole("tab", { name: /cache/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /guardrails/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /^sso$/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /provider keys/i })).toBeInTheDocument();

    // Cache tab selected by default
    expect(
      within(tablist).getByRole("tab", { name: /cache/i }),
    ).toHaveAttribute("aria-selected", "true");
  });
});

// ── Simple pages: no hero testid, region still resolves ───────────────────────
//
// Non-regression suite: members / alerts / audit must NEVER gain a hero section.
// These pass both before and after build — they guard against fabricated metrics.

describe("test_simple_pages_no_hero", () => {
  it("MembersPage has no members-hero testid; region still resolves", async () => {
    useMembersHandlers();
    render(<MembersPage callerRole="owner" />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("region", { name: /member/i })).toBeInTheDocument();
    });

    expect(screen.queryByTestId("members-hero")).toBeNull();
  });

  it("AlertsPage has no alerts-hero testid; region still resolves", async () => {
    useAlertsHandlers();
    render(<AlertsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("region", { name: /alert/i })).toBeInTheDocument();
    });

    expect(screen.queryByTestId("alerts-hero")).toBeNull();
  });

  it("AuditPage has no audit-hero testid; region still resolves", async () => {
    useAuditHandlers();
    render(<AuditPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("region", { name: /audit/i })).toBeInTheDocument();
    });

    expect(screen.queryByTestId("audit-hero")).toBeNull();
  });
});

// ── Accessibility: 0 serious/critical violations (color-contrast disabled) ───
//
// These may pass pre-build if the page is already a11y-clean.
// The STRUCTURE tests above carry the primary RED signal.

describe("a11y — governance pages (serious/critical only)", () => {
  it("KeysPage axe-clean on default Keys tab", async () => {
    useKeysHandlers();
    const { container } = render(<KeysPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /api keys/i })).toBeInTheDocument();
    });

    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });

  it("RoutingPage (owner) axe-clean on Overview", async () => {
    useRoutingHandlers("owner");
    const { container } = render(<RoutingPage />, { wrapper: Wrapper });

    await screen.findByRole("heading", { level: 1, name: /routing health/i });

    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });

  it("SettingsPage axe-clean on Cache tab", async () => {
    useSettingsHandlers();
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /^settings$/i })).toBeInTheDocument();
    });

    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });
});
