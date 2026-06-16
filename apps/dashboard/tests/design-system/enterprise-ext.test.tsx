/**
 * design-system-enterprise-ext (v23 freeze-first) — RED render+token+a11y contract for the
 * enterprise extension: chart + sidebar tokens, the light/dark/system theme, and the shared
 * component inventory (ThemeProvider/useTheme/ThemeToggle · StatCard · ChartCard · DataTable ·
 * AppSidebar parts).
 *
 * RED before build: the new tokens are absent from tokens.json/globals.css and
 * components/ui/{theme-provider,theme-toggle,stat-card,chart,data-table,sidebar}.tsx do not
 * exist yet — every component import below is MODULE_NOT_FOUND until Build writes them.
 *
 * Mirrors extension.test.tsx: hand-rolled primitives render with no Radix polyfills; axe is
 * scoped to serious|critical with color-contrast disabled (jsdom has no canvas — true contrast
 * is the standing browser-only residue).
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "@/test-support/axe";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";

import {
  ThemeProvider,
  useTheme,
  themeScript,
  ThemeToggle,
  StatCard,
  ChartCard,
  ChartContainer,
  ChartTooltipContent,
  DataTable,
  Sidebar,
  SidebarHeader,
  SidebarBrand,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarItem,
  SidebarFooter,
  SidebarTrigger,
} from "@/components/ui";
import type { ColumnDef } from "@tanstack/react-table";

const REPO_ROOT = resolve(process.cwd(), "../..");
const DESIGN = resolve(REPO_ROOT, ".add/design");
const DASH = process.cwd();

type TokenNode = { $value?: unknown; $type?: string } & Record<string, unknown>;
function readJson(p: string): Record<string, unknown> {
  return JSON.parse(readFileSync(p, "utf8")) as Record<string, unknown>;
}
function resolveAlias(tokens: Record<string, unknown>, value: unknown): unknown {
  let v = value;
  let hops = 0;
  while (typeof v === "string" && v.startsWith("{") && v.endsWith("}") && hops < 10) {
    const path = v.slice(1, -1).split(".");
    let node: unknown = tokens;
    for (const seg of path) node = (node as Record<string, unknown>)?.[seg];
    v = (node as TokenNode)?.$value;
    hops += 1;
  }
  return v;
}
async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

// ── controllable matchMedia + ResizeObserver for the theme + chart suites ──
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
function setSystemDark(v: boolean) {
  systemPrefersDark = v;
  act(() => mqListeners.forEach((cb) => cb({ matches: v })));
}
beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  systemPrefersDark = false;
});

// ── TOKENS ─────────────────────────────────────────────────────────────────
describe("tokens — chart + sidebar extension resolves across the 3 layers", () => {
  it("test_chart_tokens_resolve", () => {
    const t = readJson(resolve(DESIGN, "tokens.json"));
    const semantic = t.semantic as Record<string, Record<string, TokenNode>>;
    for (let i = 1; i <= 5; i++) {
      const resolved = resolveAlias(t, semantic.color[`chart-${i}`]?.$value);
      expect(String(resolved)).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it("test_sidebar_tokens_resolve", () => {
    const t = readJson(resolve(DESIGN, "tokens.json"));
    const semantic = t.semantic as Record<string, Record<string, TokenNode>>;
    for (const name of [
      "sidebar",
      "sidebar-foreground",
      "sidebar-accent",
      "sidebar-accent-foreground",
      "sidebar-border",
      "sidebar-ring",
    ]) {
      const resolved = resolveAlias(t, semantic.color[name]?.$value);
      expect(String(resolved)).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
    const component = t.component as Record<string, Record<string, TokenNode>>;
    // component tokens must resolve through the semantic layer to a real hex (not just exist)
    expect(String(resolveAlias(t, component.sidebar?.bg?.$value))).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(String(resolveAlias(t, component.chart?.["1"]?.$value))).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(String(resolveAlias(t, component["stat-card"]?.value?.$value))).toMatch(/^#[0-9a-fA-F]{6}$/);
  });

  it("test_v13_tokens_unchanged", () => {
    // additive-only: the v13 accent identity still resolves to indigo-600
    const t = readJson(resolve(DESIGN, "tokens.json"));
    const semantic = t.semantic as Record<string, Record<string, TokenNode>>;
    expect(String(resolveAlias(t, semantic.color.accent?.$value)).toUpperCase()).toBe("#4F46E5");
  });
});

describe("globals.css — new tokens mirrored in both themes + @theme bridge", () => {
  const css = () => readFileSync(resolve(DASH, "app/globals.css"), "utf8");
  it("test_root_and_dark_declare_new_vars", () => {
    const src = css();
    // locate the actual SELECTOR blocks (":root {" / ".dark {"), not the prose mentions
    // of :root/.dark in the file's top comment
    const rootBlock = src.slice(src.indexOf(":root {"), src.indexOf(".dark {"));
    const darkBlock = src.slice(src.indexOf(".dark {"), src.indexOf("@theme"));
    for (const v of ["--chart-1", "--chart-5", "--sidebar", "--sidebar-border"]) {
      expect(rootBlock).toContain(v);
      expect(darkBlock).toContain(v);
    }
  });
  it("test_theme_inline_bridges_new_colors", () => {
    const src = css();
    const themeBlock = src.slice(src.indexOf("@theme"));
    for (const v of ["--color-chart-1", "--color-sidebar", "--color-sidebar-accent"]) {
      expect(themeBlock).toContain(v);
    }
  });
});

// ── THEME ──────────────────────────────────────────────────────────────────
function ThemeProbe() {
  const { resolvedTheme, theme } = useTheme();
  return (
    <div>
      <span data-testid="resolved">{resolvedTheme}</span>
      <span data-testid="mode">{theme}</span>
    </div>
  );
}

describe("ThemeProvider + ThemeToggle — toggle, persist, system", () => {
  it("test_toggle_applies_dark_class_and_persists", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
        <ThemeProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await user.click(screen.getByRole("button", { name: /theme/i }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
  });

  it("test_theme_restored_from_storage_on_remount", () => {
    localStorage.setItem("theme", "dark");
    render(
      <ThemeProvider defaultTheme="light">
        <ThemeProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("test_system_theme_follows_os_live", () => {
    render(
      <ThemeProvider defaultTheme="system">
        <ThemeProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("system");
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    setSystemDark(true);
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    // "system" choice is not overwritten by following the OS — provider must not write
    // storage at all until the user explicitly chooses
    expect(localStorage.getItem("theme")).toBeNull();
  });

  it("test_theme_script_safely_encodes_key", () => {
    // the no-flash script must embed the storage key as a safely-encoded literal, never by
    // raw single-quote concatenation (which a crafted key could break out of)
    expect(themeScript()).toContain('localStorage.getItem("theme")');
    const evil = 'a");globalThis.__pwned=1;("';
    expect(themeScript(evil)).toContain("localStorage.getItem(" + JSON.stringify(evil) + ")");
    expect(themeScript(evil)).not.toContain("getItem('" + evil + "')");
    expect(themeScript(evil)).not.toContain("globalThis.__pwned=1;(\")");
  });

  it("test_theme_toggle_keyboard_and_axe", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    const btn = screen.getByRole("button", { name: /theme/i });
    btn.focus();
    await user.keyboard("{Enter}");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── STAT CARD ────────────────────────────────────────────────────────────────
describe("StatCard — label, value, non-color-only trend", () => {
  it("test_statcard_renders_label_value_delta", async () => {
    const { container } = render(
      <StatCard label="New Customers" value="1,234" delta={{ direction: "down", text: "-20%" }} footer="Acquisition needs attention" />,
    );
    expect(screen.getByText("New Customers")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("-20%")).toBeInTheDocument();
    expect(screen.getByText("Acquisition needs attention")).toBeInTheDocument();
    // direction conveyed by text/aria, not color alone (WCAG 1.4.1)
    expect(container.textContent?.toLowerCase()).toMatch(/down|decreas|↓/);
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── CHART CARD ───────────────────────────────────────────────────────────────
describe("ChartCard — token-driven, accessible, axe-clean", () => {
  it("test_chartcard_title_desc_token_vars", async () => {
    const config = { requests: { label: "Requests", color: "var(--color-chart-1)" } };
    const { container } = render(
      <ChartCard title="Usage over time" description="Last 7 days" config={config}>
        <ChartContainer config={config}>
          <div data-testid="chart-body" />
        </ChartContainer>
      </ChartCard>,
    );
    expect(screen.getByText("Usage over time")).toBeInTheDocument();
    expect(screen.getByText("Last 7 days")).toBeInTheDocument();
    expect(screen.getByTestId("chart-body")).toBeInTheDocument();
    // ChartContainer injects the token-named css var (no raw hex in the consumer)
    expect(container.innerHTML).toContain("--color-requests");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_chart_tooltip_content_renders_payload", () => {
    const { rerender, container } = render(
      <ChartTooltipContent
        active
        label="Mon"
        payload={[{ name: "Requests", value: 446, color: "var(--color-chart-1)" }]}
      />,
    );
    expect(screen.getByText("Mon")).toBeInTheDocument();
    expect(screen.getByText("Requests")).toBeInTheDocument();
    expect(screen.getByText("446")).toBeInTheDocument();
    // inactive / empty payload renders nothing
    rerender(<ChartTooltipContent active={false} payload={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// ── DATA TABLE ───────────────────────────────────────────────────────────────
type Row = { model: string; cost: number };
const columns: ColumnDef<Row>[] = [
  { accessorKey: "model", header: "Model" },
  { accessorKey: "cost", header: "Cost" },
];
describe("DataTable — sorts + empty state", () => {
  it("test_datatable_renders_rows_and_sorts", async () => {
    const user = userEvent.setup();
    render(
      <DataTable
        columns={columns}
        data={[
          { model: "b-model", cost: 2 },
          { model: "a-model", cost: 1 },
        ]}
      />,
    );
    let cells = screen.getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toContain("b-model");
    // sortable header is a button (keyboard-operable)
    const header = screen.getByRole("button", { name: /model/i });
    await user.click(header);
    const firstDataRow = screen.getAllByRole("row")[1];
    expect(within(firstDataRow).getByText("a-model")).toBeInTheDocument();
  });

  it("test_datatable_empty_state", () => {
    render(<DataTable columns={columns} data={[]} emptyMessage="No usage yet" />);
    expect(screen.getByText("No usage yet")).toBeInTheDocument();
  });
});

// ── SIDEBAR ──────────────────────────────────────────────────────────────────
describe("AppSidebar parts — landmark, items, collapse trigger, axe", () => {
  function FullSidebar() {
    return (
      <Sidebar aria-label="Primary">
        <SidebarHeader>
          <SidebarBrand title="Acme Inc." />
          <SidebarTrigger />
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Home</SidebarGroupLabel>
            <SidebarItem href="/" active>
              Dashboard
            </SidebarItem>
            <SidebarItem href="/usage">Usage</SidebarItem>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>shadcn</SidebarFooter>
      </Sidebar>
    );
  }
  it("test_sidebar_landmark_items_axe", async () => {
    const { container } = render(<FullSidebar />);
    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(nav).toBeInTheDocument();
    expect(within(nav).getByText("Dashboard")).toBeInTheDocument();
    expect(within(nav).getByText("Acme Inc.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /collapse|toggle|sidebar|menu/i })).toBeInTheDocument();
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_sidebar_active_item_marked", () => {
    render(<FullSidebar />);
    const active = screen.getByRole("link", { name: "Dashboard" });
    expect(active).toHaveAttribute("aria-current", "page");
  });
});

// ── BARREL ───────────────────────────────────────────────────────────────────
describe("barrel — every new symbol is exported from @/components/ui", () => {
  it("test_enterprise_barrel_exported", () => {
    for (const C of [
      ThemeProvider,
      useTheme,
      ThemeToggle,
      StatCard,
      ChartCard,
      ChartContainer,
      DataTable,
      Sidebar,
      SidebarHeader,
      SidebarBrand,
      SidebarContent,
      SidebarGroup,
      SidebarGroupLabel,
      SidebarItem,
      SidebarFooter,
      SidebarTrigger,
    ]) {
      expect(C).toBeDefined();
    }
  });
});
