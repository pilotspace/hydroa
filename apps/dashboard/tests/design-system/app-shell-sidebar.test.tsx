/**
 * app-shell-sidebar (v23) — RED render+a11y contract for the live enterprise shell:
 * AppShell rebuilt on the v23 Sidebar parts (branded collapsible rail · mobile sheet ·
 * theme toggle · user footer) and DashboardShell wiring usePathname() + the identity query.
 *
 * RED before build: AppShell is still the flat v13 shell — it does NOT yet render the brand,
 * a collapse trigger with data-state, a mobile-sheet hamburger, a theme toggle, or a user
 * footer, and DashboardShell does NOT yet forward activePath/userEmail. The brand / collapse /
 * sheet / theme-toggle / active-from-URL assertions fail until Build. (The frozen invariants —
 * skip-link, main#main, single Primary nav, lg:flex-row — are green-by-design preservation.)
 *
 * Mirrors enterprise-ext.test.tsx: axe scoped to serious|critical with color-contrast disabled
 * (jsdom has no canvas — true contrast is the standing browser-only residue).
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "@/test-support/axe";
import React from "react";

import { AppShell } from "@/components/ui/app-shell";
import { ThemeProvider } from "@/components/ui";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { DashboardShell } from "@/components/dashboard-shell";
import { usePathname } from "next/navigation";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

vi.mock("next/navigation", () => ({ usePathname: vi.fn(() => "/") }));
vi.mock("@/lib/hooks/use-current-user", () => ({ useCurrentUser: vi.fn() }));

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

const ALL_LINKS = [
  "Usage",
  "Spend",
  "API Keys",
  "Models",
  "Teams",
  "Members",
  "Routing",
  "Alerts",
  "Audit",
  "Health",
  "SLO",
  "Settings",
];
const ADMIN_ONLY = ["Models", "Teams", "Members", "Routing", "Alerts", "Audit", "Health", "SLO"];

// ── controllable matchMedia + ResizeObserver (theme + Radix Dialog need them) ──
let systemPrefersDark = false;
const mqListeners = new Set<(e: { matches: boolean }) => void>();
beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: query.includes("dark") ? systemPrefersDark : !systemPrefersDark,
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => mqListeners.add(cb),
      removeEventListener: (_: string, cb: (e: { matches: boolean }) => void) => mqListeners.delete(cb),
      addListener: (cb: (e: { matches: boolean }) => void) => mqListeners.add(cb),
      removeListener: (cb: (e: { matches: boolean }) => void) => mqListeners.delete(cb),
      dispatchEvent: () => true,
    }),
  });
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});
beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  systemPrefersDark = false;
  vi.mocked(usePathname).mockReturnValue("/");
  vi.mocked(useCurrentUser).mockReturnValue({ data: null, isLoading: false, isError: false });
});

const Body = () => (
  <>
    <h1>Page</h1>
    <p>Body</p>
  </>
);

// ── BRANDED NAV ──────────────────────────────────────────────────────────────
describe("AppShell — branded sidebar over the nav items", () => {
  it("test_shell_renders_branded_nav", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    expect(screen.getAllByText("Hydroa").length).toBeGreaterThanOrEqual(1);
    for (const label of ALL_LINKS) {
      expect(screen.getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
  });
});

// ── FROZEN v13 SHELL CONTRACT (preserved) ─────────────────────────────────────
describe("AppShell — the frozen v13 shell contract still holds", () => {
  it("test_frozen_v13_shell_contract_holds", async () => {
    const { container } = render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const skip = container.querySelector('a[href="#main"]');
    expect(skip).not.toBeNull();
    expect(skip).toHaveTextContent(/skip to (main )?content/i);
    // first focusable === the skip link
    expect(container.querySelectorAll("a,button,[tabindex]")[0]).toBe(skip);

    const main = container.querySelector("main#main");
    expect(main).not.toBeNull();
    expect(main?.tagName).toBe("MAIN");

    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_single_primary_landmark", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    expect(screen.getAllByRole("navigation", { name: /primary/i })).toHaveLength(1);
  });

  it("test_responsive_root_class", () => {
    const { container } = render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    expect(container.querySelector('[class*="lg:flex-row"]')).not.toBeNull();
  });
});

// ── ACTIVE ROUTE ──────────────────────────────────────────────────────────────
describe("AppShell — active route marking", () => {
  it("test_active_route_marked", () => {
    render(
      <AppShell role="owner" activePath="/app/spend">
        <Body />
      </AppShell>,
    );
    const current = screen.getAllByRole("link").filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAccessibleName(/spend/i);
  });

  it("test_no_active_without_path", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const current = screen.queryAllByRole("link").filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(0);
  });
});

// ── RBAC NAV FILTER (fail-open) ───────────────────────────────────────────────
describe("AppShell — role-filtered nav, fail open", () => {
  it("test_member_hides_admin_links", () => {
    render(
      <AppShell role="member">
        <Body />
      </AppShell>,
    );
    for (const label of ADMIN_ONLY) {
      expect(screen.queryByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeNull();
    }
    for (const label of ["Usage", "Spend", "API Keys", "Settings"]) {
      expect(screen.getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
  });

  it("test_null_role_fails_open", () => {
    render(
      <AppShell role={null}>
        <Body />
      </AppShell>,
    );
    for (const label of ALL_LINKS) {
      expect(screen.getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
  });

  it("test_undefined_role_fails_open", () => {
    // the contract names undefined (loading/failed identity) as a fail-open case too —
    // strict `=== "member"` must show ALL links, never lock the nav down
    render(
      <AppShell>
        <Body />
      </AppShell>,
    );
    for (const label of ALL_LINKS) {
      expect(screen.getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
  });
});

// ── COLLAPSE ──────────────────────────────────────────────────────────────────
describe("AppShell — collapsible desktop rail keeps accessible names", () => {
  it("test_desktop_rail_collapses_keeps_names", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const rail = screen.getByRole("navigation", { name: /primary/i });
    const trigger = screen.getByRole("button", { name: /toggle sidebar/i });
    expect(rail).toHaveAttribute("data-state", "expanded");
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    await user.click(trigger);
    expect(rail).toHaveAttribute("data-state", "collapsed");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    // every nav item IN THE RAIL still exposes its accessible name when collapsed
    // (label is sr-only, not removed) — scoped to the rail, not the whole document
    for (const label of ALL_LINKS) {
      expect(within(rail).getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── MOBILE SHEET ──────────────────────────────────────────────────────────────
describe("AppShell — responsive mobile sheet", () => {
  it("test_mobile_sheet_opens_and_escape", async () => {
    const user = userEvent.setup();
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    // closed by default → no dialog, single Primary nav
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: /open navigation/i }));
    const dialog = screen.getByRole("dialog", { name: /navigation/i });
    expect(dialog).toBeInTheDocument();
    // the sheet holds the SAME role-filtered nav links — all 7 for an owner
    for (const label of ALL_LINKS) {
      expect(within(dialog).getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// ── THEME TOGGLE ──────────────────────────────────────────────────────────────
describe("AppShell — theme toggle mounted + operable", () => {
  it("test_theme_toggle_present_keyboard", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider defaultTheme="light">
        <AppShell role="owner">
          <Body />
        </AppShell>
      </ThemeProvider>,
    );
    const toggles = screen.getAllByRole("button", { name: /toggle theme/i });
    expect(toggles.length).toBeGreaterThanOrEqual(1);
    toggles[0].focus();
    await user.keyboard("{Enter}");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});

// ── USER FOOTER ───────────────────────────────────────────────────────────────
describe("AppShell — user identity footer", () => {
  it("test_user_identity_footer", () => {
    const { rerender } = render(
      <AppShell role="owner" userEmail="ada@hydroa.io">
        <Body />
      </AppShell>,
    );
    expect(screen.getByText("ada@hydroa.io")).toBeInTheDocument();

    // absent identity → no crash, no email
    rerender(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    expect(screen.queryByText("ada@hydroa.io")).toBeNull();
  });
});

// ── SIDEBAR TRIGGER NAME (v24, single source of truth) ────────────────────────
describe("SidebarTrigger — accessible name from the DS default", () => {
  // GREEN-BY-DESIGN: the v24 nit fix is a pure dedup — the DS default aria-label is the single
  // source of truth, and the redundant identical aria-label on the app-shell consumer is removed.
  // No behavioral delta; these assert the name is PRESERVED (the icon-only button is never unnamed).
  it("test_sidebartrigger_name_from_ds_default", () => {
    render(<SidebarTrigger />);
    expect(screen.getByRole("button", { name: /toggle sidebar/i })).toBeInTheDocument();
  });

  it("test_sidebartrigger_consumer_can_still_override_name", () => {
    // {...props} still wins over the default — the documented intentional override
    render(<SidebarTrigger aria-label="Collapse menu" />);
    expect(screen.getByRole("button", { name: /collapse menu/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /toggle sidebar/i })).toBeNull();
  });
});

// ── LIVE SHELL WIRING ─────────────────────────────────────────────────────────
describe("DashboardShell — marks route from URL + reuses identity query", () => {
  it("test_dashboard_shell_marks_route_and_reuses_query", () => {
    vi.mocked(usePathname).mockReturnValue("/app/keys");
    vi.mocked(useCurrentUser).mockReturnValue({
      data: { user_id: "u1", tenant_id: "t1", email: "ada@hydroa.io", role: "owner", exp: null },
      isLoading: false,
      isError: false,
    });
    render(
      <DashboardShell>
        <Body />
      </DashboardShell>,
    );
    const current = screen.getAllByRole("link").filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAccessibleName(/api keys/i);
    expect(screen.getByText("ada@hydroa.io")).toBeInTheDocument();
    // reuses the existing ["current-user"] query exactly once — no second/new fetch path
    expect(vi.mocked(useCurrentUser)).toHaveBeenCalledTimes(1);
  });
});
