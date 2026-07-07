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
import { render, screen, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe } from "@/test-support/axe";
import React from "react";

import { AppShell } from "@/components/ui/app-shell";
import { ThemeProvider } from "@/components/ui";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { DashboardShell } from "@/components/dashboard-shell";
import { usePathname } from "next/navigation";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

// command-palette TASK.md: PlatformCommandPalette (mounted by DashboardShell for a
// superadmin session) calls next/navigation's useRouter() — this file's own
// pre-existing LOCAL mock only ever provided usePathname, shadowing the global
// tests/setup.ts mock (which does provide useRouter). A minimal stub is added
// here rather than switching this whole file to the shared getRouterMock()
// helper — none of this file's own tests assert on router.push/replace calls.
vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/"),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() })),
}));
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

  it("test_desktop_rail_full_height_fixed_viewport", () => {
    // v54 (re-architect): the desktop rail fills the full viewport height via a
    // FIXED-VIEWPORT shell — the lg:flex-row container is exactly the viewport height and
    // clips its overflow, so the rail (lg:h-full) always spans it WITHOUT depending on
    // position:sticky (which can break under an ancestor transform/zoom → the "~75%"
    // report). lg-scoped: the stacked mobile layout is unaffected. <main> owns the ONLY
    // desktop scroll region, so there is no double scrollbar and the rail never scrolls away.
    const { container } = render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const rail = screen.getByRole("navigation", { name: /primary/i });
    expect(rail.className).toContain("lg:h-full");
    expect(rail.className).not.toContain("lg:sticky");

    const layout = container.querySelector('[class*="lg:flex-row"]') as HTMLElement;
    expect(layout).not.toBeNull();
    expect(layout.className).toContain("lg:h-screen");
    expect(layout.className).toContain("lg:overflow-hidden");

    const main = container.querySelector("main#main") as HTMLElement;
    expect(main.className).toContain("lg:overflow-y-auto");
  });

  it("test_main_scales_gutters_on_wide_screens", () => {
    // "No cap — scale gutters": content stays FLUID (no max-width cap) but the horizontal
    // gutters grow on very wide screens (2xl) so content is not edge-glued on large monitors.
    const { container } = render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const main = container.querySelector("main#main") as HTMLElement;
    expect(main.className).toContain("2xl:px-16");
    expect(main.className).not.toMatch(/\bmax-w-/); // fluid, never capped
  });
});

// ── FIXED-VIEWPORT SCROLL BEHAVIOR (v54) ──────────────────────────────────────
describe("AppShell — main is a focusable scroll region that resets on route change", () => {
  it("test_main_is_focusable_scroll_region", () => {
    // In the fixed-viewport shell <main> owns the desktop scroll. tabindex=-1 makes it the
    // valid target for the skip-link AND lets a keyboard user focus + scroll the region.
    const { container } = render(
      <AppShell role="owner" activePath="/app/usage">
        <Body />
      </AppShell>,
    );
    const main = container.querySelector("main#main") as HTMLElement;
    expect(main).toHaveAttribute("tabindex", "-1");
  });

  it("test_main_scroll_resets_on_route_change", () => {
    // Regression guard: Next resets window scroll on navigation, but <main> is the scroll
    // container now, so the shell must reset main's scroll to the top when the route changes —
    // otherwise a new page inherits the previous page's scroll offset.
    const scrollTo = vi.fn();
    const orig = (HTMLElement.prototype as unknown as { scrollTo?: unknown }).scrollTo;
    (HTMLElement.prototype as unknown as { scrollTo: unknown }).scrollTo = scrollTo;
    try {
      const { rerender } = render(
        <AppShell role="owner" activePath="/app/usage">
          <Body />
        </AppShell>,
      );
      scrollTo.mockClear(); // ignore the on-mount reset
      rerender(
        <AppShell role="owner" activePath="/app/chat">
          <Body />
        </AppShell>,
      );
      expect(scrollTo).toHaveBeenCalled();
    } finally {
      (HTMLElement.prototype as unknown as { scrollTo?: unknown }).scrollTo = orig;
    }
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

// ── GROUPED WORKFLOW NAV (v7 premium IA, Tin 2026-07-06) ──────────────────────
describe("AppShell — primary nav grouped into labeled workflow sections", () => {
  const GROUP_LABELS = ["Playground", "Insights", "Configure", "Govern"];

  it("test_primary_nav_grouped_into_labeled_sections", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const rail = screen.getByRole("navigation", { name: /primary/i });
    // every workflow section header is present for a full-access owner …
    for (const label of GROUP_LABELS) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    // … and grouping never drops a destination — all links still resolve by name
    for (const label of ALL_LINKS) {
      expect(within(rail).getByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument();
    }
  });

  it("test_all_gated_group_is_hidden_for_member_no_orphan_header", () => {
    render(
      <AppShell role="member">
        <Body />
      </AppShell>,
    );
    const rail = screen.getByRole("navigation", { name: /primary/i });
    // "Govern" is entirely admin-gated → the whole section (label included) disappears
    expect(within(rail).queryByText("Govern")).toBeNull();
    // ungated sections survive, and Settings is always pinned
    for (const label of ["Playground", "Insights"]) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    expect(within(rail).getByRole("link", { name: /^Settings$/i })).toBeInTheDocument();
    // no admin destination leaks through the grouping
    for (const label of ADMIN_ONLY) {
      expect(within(rail).queryByRole("link", { name: new RegExp(`^${label}$`, "i") })).toBeNull();
    }
  });

  it("test_settings_pinned_exactly_once_below_the_groups", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    const rail = screen.getByRole("navigation", { name: /primary/i });
    expect(within(rail).getAllByRole("link", { name: /^Settings$/i })).toHaveLength(1);
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

  it("test_mobile_sheet_exposes_logout", async () => {
    // Below the lg breakpoint the desktop rail (which holds the footer logout) is
    // hidden — so the mobile sheet must carry its own logout, or mobile users have
    // no way to sign out at all.
    const user = userEvent.setup();
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    await user.click(screen.getByRole("button", { name: /open navigation/i }));
    const dialog = screen.getByRole("dialog", { name: /navigation/i });
    expect(within(dialog).getByRole("button", { name: /log ?out/i })).toBeInTheDocument();
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

// ── COMMAND PALETTE MOUNT GATE (command-palette TASK.md, M1/M2/R2) ────────────
// PlatformCommandPalette's OWN behavior (shortcut/focus/search/listbox/render
// states/etc.) is covered by tests/platform-command-palette.test.tsx; THIS
// block covers ONLY the access-control mount gate — DashboardShell must mount
// PlatformCommandPalette if and only if useCurrentUser().data?.role ===
// "superadmin" EXACTLY, threaded through AppShell's new additive
// `commandPalette` prop (mirrors the existing `banner` prop's own recipe,
// tested above by test_dashboard_shell_marks_route_and_reuses_query).
function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

// matches the palette's own trigger aria-label ("Search tenants (Command K)")
const PALETTE_TRIGGER_NAME = /search tenants.*command k/i;

const NON_SUPERADMIN_CASES: Array<{ role: string | null; isLoading: boolean }> = [
  { role: "member", isLoading: false },
  { role: "admin", isLoading: false },
  { role: "owner", isLoading: false },
  { role: null, isLoading: false }, // resolved, no role
  { role: null, isLoading: true }, // still loading
];

function mockCurrentUserRole(role: string | null, isLoading: boolean) {
  vi.mocked(useCurrentUser).mockReturnValue({
    data: role ? { user_id: "u1", tenant_id: "t1", email: "x@hydroa.io", role, exp: null } : null,
    isLoading,
    isError: false,
  });
}

describe("AppShell — commandPalette prop is additive and absent by default (M2)", () => {
  it("test_appshell_commandpalette_prop_is_additive_and_absent_by_default", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    // omitted (every pre-existing call site) -> no palette trigger, no palette
    // dialog anywhere — byte-identical default output to before this task
    expect(screen.queryByRole("button", { name: PALETTE_TRIGGER_NAME })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("DashboardShell — command palette mounts iff role is exactly superadmin (M1)", () => {
  it("test_palette_mounts_only_for_exact_superadmin_role", () => {
    const client = makeQueryClient();

    // positive: an exact "superadmin" role mounts the always-visible trigger
    mockCurrentUserRole("superadmin", false);
    const superadminRender = render(
      <QueryClientProvider client={client}>
        <DashboardShell>
          <Body />
        </DashboardShell>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("button", { name: PALETTE_TRIGGER_NAME })).toBeInTheDocument();
    superadminRender.unmount();

    // negative: every non-exact-superadmin role/state never mounts it (static
    // presence/absence check — see the reject test below for the adversarial
    // dynamic check that actively tries the keyboard shortcut too)
    for (const { role, isLoading } of NON_SUPERADMIN_CASES) {
      mockCurrentUserRole(role, isLoading);
      const { unmount } = render(
        <QueryClientProvider client={client}>
          <DashboardShell>
            <Body />
          </DashboardShell>
        </QueryClientProvider>,
      );
      expect(screen.queryByRole("button", { name: PALETTE_TRIGGER_NAME })).toBeNull();
      unmount();
    }
  });
});

describe("DashboardShell — reject: palette never mounts for a non-exact-superadmin role (R2)", () => {
  it("test_reject_palette_never_mounts_for_non_exact_superadmin_role", () => {
    // The adversarial angle explicitly required at build time: for every
    // non-superadmin-exact session, ACTIVELY press the global shortcut and
    // confirm no dialog ever appears — proving no keydown listener was ever
    // attached (fail-CLOSED). A fail-OPEN bug (e.g. the listener attached
    // unconditionally while only the trigger BUTTON was conditionally hidden)
    // would be invisible to M1's own static markup-absence check above but
    // WOULD be caught here.
    const client = makeQueryClient();
    for (const { role, isLoading } of NON_SUPERADMIN_CASES) {
      mockCurrentUserRole(role, isLoading);
      const { unmount } = render(
        <QueryClientProvider client={client}>
          <DashboardShell>
            <Body />
          </DashboardShell>
        </QueryClientProvider>,
      );
      fireEvent.keyDown(document, { key: "k", metaKey: true });
      expect(screen.queryByRole("dialog")).toBeNull();
      unmount();
    }
  });
});
