/**
 * design-system-extension (v15 freeze-first) — RED render+a11y contract for the four
 * additive primitives the coverage surfaces need: Switch · Tabs · Textarea · Checkbox.
 *
 * RED before build: components/ui/{switch,tabs,textarea,checkbox}.tsx do not exist yet, so
 * every import below is MODULE_NOT_FOUND until Build writes them.
 *
 * NOTE — deliberately NO Radix polyfills here (unlike primitives.test.tsx's Select block):
 * the four primitives are hand-rolled on native elements + ARIA, so they MUST render with
 * zero hasPointerCapture/scrollIntoView/ResizeObserver shims (the whole point — they ship into
 * the legacy/bff surface suites that lack those shims).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "@/test-support/axe";
import React from "react";

import {
  Switch,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  Textarea,
  Checkbox,
} from "@/components/ui";

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
}

// ── Switch ───────────────────────────────────────────────────────────────────
describe("Switch — controlled toggle, pointer + keyboard", () => {
  function Controlled({
    onCheckedChange,
    disabled,
  }: {
    onCheckedChange: (n: boolean) => void;
    disabled?: boolean;
  }) {
    const [checked, setChecked] = React.useState(false);
    return (
      <Switch
        checked={checked}
        disabled={disabled}
        aria-label="dark mode"
        onCheckedChange={(n) => {
          setChecked(n);
          onCheckedChange(n);
        }}
      />
    );
  }

  it("test_switch_toggles_pointer_keyboard", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Controlled onCheckedChange={spy} />);
    const sw = screen.getByRole("switch", { name: /dark mode/i });
    expect(sw).toHaveAttribute("aria-checked", "false");

    await user.click(sw);
    expect(spy).toHaveBeenLastCalledWith(true);
    expect(sw).toHaveAttribute("aria-checked", "true");

    sw.focus();
    await user.keyboard(" ");
    expect(spy).toHaveBeenLastCalledWith(false);
    expect(sw).toHaveAttribute("aria-checked", "false");
  });

  it("test_switch_disabled_does_not_fire", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Controlled onCheckedChange={spy} disabled />);
    await user.click(screen.getByRole("switch", { name: /dark mode/i }));
    expect(spy).not.toHaveBeenCalled();
  });

  it("test_switch_labelled_axe_clean", async () => {
    const { container } = render(
      <Switch checked={false} aria-label="enable cache" onCheckedChange={vi.fn()} />,
    );
    expect(screen.getByRole("switch", { name: /enable cache/i })).toBeInTheDocument();
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── Tabs ─────────────────────────────────────────────────────────────────────
describe("Tabs — WAI-ARIA tablist, click + keyboard", () => {
  function ThreeTabs() {
    return (
      <Tabs defaultValue="cache">
        <TabsList aria-label="settings">
          <TabsTrigger value="cache">Cache</TabsTrigger>
          <TabsTrigger value="guardrails">Guardrails</TabsTrigger>
          <TabsTrigger value="sso">SSO</TabsTrigger>
        </TabsList>
        <TabsContent value="cache">cache panel</TabsContent>
        <TabsContent value="guardrails">guardrails panel</TabsContent>
        <TabsContent value="sso">sso panel</TabsContent>
      </Tabs>
    );
  }

  it("test_tabs_select_by_click", async () => {
    const user = userEvent.setup();
    render(<ThreeTabs />);
    expect(screen.getByText("cache panel")).toBeInTheDocument();
    expect(screen.queryByText("guardrails panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    const guardrailsTab = screen.getByRole("tab", { name: "Guardrails" });
    expect(guardrailsTab).toHaveAttribute("aria-selected", "true");
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("guardrails panel");
    expect(panel).toHaveAttribute("aria-labelledby", guardrailsTab.id);
    expect(screen.queryByText("cache panel")).not.toBeInTheDocument();
  });

  it("test_tabs_keyboard_roving", async () => {
    const user = userEvent.setup();
    render(<ThreeTabs />);
    const tablist = screen.getByRole("tablist", { name: "settings" });
    expect(tablist).toBeInTheDocument();
    const tabs = within(tablist).getAllByRole("tab");

    // roving tabindex: only the active (first) tab is tabbable initially
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
    expect(tabs[2]).toHaveAttribute("tabindex", "-1");

    tabs[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    // roving tabindex UPDATES after navigation: the new active tab is the only tabbable one
    expect(tabs[1]).toHaveAttribute("tabindex", "0");
    expect(tabs[0]).toHaveAttribute("tabindex", "-1");
    expect(tabs[2]).toHaveAttribute("tabindex", "-1");

    await user.keyboard("{ArrowLeft}"); // backward navigation
    expect(document.activeElement).toBe(tabs[0]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(document.activeElement).toBe(tabs[2]);
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowRight}"); // wraps to first
    expect(document.activeElement).toBe(tabs[0]);

    await user.keyboard("{ArrowLeft}"); // wraps to last
    expect(document.activeElement).toBe(tabs[2]);

    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(tabs[0]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");

    // each trigger's aria-controls names a deterministic panel id, and the ACTIVE
    // tab's aria-controls resolves to the rendered tabpanel that labels back to it
    tabs.forEach((t) => expect(t.getAttribute("aria-controls")).toBeTruthy());
    const activeTab = tabs[0];
    const activePanel = document.getElementById(activeTab.getAttribute("aria-controls")!);
    expect(activePanel).toBeInTheDocument();
    expect(activePanel).toHaveAttribute("role", "tabpanel");
    expect(activePanel).toHaveAttribute("aria-labelledby", activeTab.id);
  });

  it("test_tabs_arrowup_down_do_not_navigate", async () => {
    // horizontal tablist: Up/Down are NOT tab navigation (WAI-ARIA APG)
    const user = userEvent.setup();
    render(<ThreeTabs />);
    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab");
    tabs[0].focus();
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(tabs[0]);
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("test_tabs_axe_clean", async () => {
    const { container } = render(<ThreeTabs />);
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── Textarea ─────────────────────────────────────────────────────────────────
describe("Textarea — native, typeable, axe-clean", () => {
  it("test_textarea_native_typeable_axe", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Textarea aria-label="pii patterns" placeholder="one regex per line" />,
    );
    const ta = screen.getByLabelText("pii patterns");
    expect(ta.tagName).toBe("TEXTAREA");
    // NOTE: userEvent.type treats { and [ as special-key descriptors, so the
    // typed string is brace-free (the assertion is "accepts typing", not regex).
    await user.type(ta, "account number");
    expect(ta).toHaveValue("account number");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── Checkbox ─────────────────────────────────────────────────────────────────
describe("Checkbox — native, pointer + keyboard, axe-clean", () => {
  function Controlled({ onChange }: { onChange: (c: boolean) => void }) {
    const [checked, setChecked] = React.useState(false);
    return (
      <Checkbox
        aria-label="block on injection"
        checked={checked}
        onChange={(e) => {
          setChecked(e.target.checked);
          onChange(e.target.checked);
        }}
      />
    );
  }

  it("test_checkbox_toggles_pointer_keyboard_axe", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    const { container } = render(<Controlled onChange={spy} />);
    const cb = screen.getByRole("checkbox", { name: /block on injection/i });
    expect(cb.tagName).toBe("INPUT");
    expect(cb).toHaveAttribute("type", "checkbox");

    await user.click(cb);
    expect(spy).toHaveBeenLastCalledWith(true);

    cb.focus();
    await user.keyboard(" ");
    expect(spy).toHaveBeenLastCalledWith(false);

    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── barrel ───────────────────────────────────────────────────────────────────
describe("barrel — every new primitive is exported from @/components/ui", () => {
  it("test_primitives_barrel_exported", () => {
    for (const C of [Switch, Tabs, TabsList, TabsTrigger, TabsContent, Textarea, Checkbox]) {
      expect(C).toBeDefined();
    }
  });
});
