/**
 * ui-ux-verify.test.tsx (LEGACY project) — v13 milestone-exit verification pass.
 *
 * The frozen §3 VERIFICATION ACCEPTANCE BAR, the jsdom-provable half:
 *   - axe(container) on every redesigned surface -> ZERO serious|critical violations
 *     (axe default WCAG 2.0/2.1/2.2 A+AA ruleset; color-contrast EXCLUDED — jsdom has no
 *      canvas for pixel sampling, declared as browser-only residue, never silently passed)
 *   - keyboard: the Create dialog moves focus inside, traps Tab/Shift-Tab, Escape closes
 *   - AppShell: skip-link -> #main + nav[aria-label="Primary"] + main#main landmarks
 *   - the four state patterns render through the SHARED state components
 *     (Loading role=status · ErrorState role=alert · Empty · data success)
 *   - responsive: redesigned surfaces carry breakpoint utilities (sm:/lg:), not fixed px
 *
 * This file covers the surfaces proven to render in the LEGACY harness (tests/):
 *   UsagePage, UsageStatsCards, UsageTable, KeysPage, CreateKeyDialog,
 *   PlaintextKeyBanner, AppShell. SpendPage + KeyGovernanceEditor are covered in the
 *   sibling tests-bff/ui-ux-verify.test.tsx (bff harness).
 *
 * NEW VALUE: no existing test axe-scans these surfaces — this is the milestone a11y gate.
 * RED-FIRST: the file is absent before this commit; if axe surfaces a REAL serious/critical
 * violation, that is a found-and-fixed a11y bug repaired in the OWNING component (verify
 * doing its job), never a relaxed bar.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

import { UsagePage } from "@/components/usage/UsagePage";
import { UsageStatsCards, type UsageData } from "@/components/usage/UsageStatsCards";
import { UsageTable } from "@/components/usage/UsageTable";
import { KeysPage } from "@/components/keys/KeysPage";
import { CreateKeyDialog } from "@/components/keys/CreateKeyDialog";
import { PlaintextKeyBanner } from "@/components/keys/PlaintextKeyBanner";
import { AppShell } from "@/components/ui/app-shell";

// ── axe helper: serious|critical only, color-contrast disabled (jsdom/no-canvas) ──
async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
}

// ── shared query wrapper (retry:false so failures surface immediately) ────────
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

// ── fixtures (match the FROZEN gateway contracts, identical to usage/keys suites) ──
const USAGE_DATA: UsageData = {
  total_cost_usd: "1.23",
  total_requests: 3,
  total_prompt_tokens: 300,
  total_completion_tokens: 150,
  records: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      model_id: "openai/gpt-4o",
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "0.41",
      status: 200,
      created_at: "2026-06-10T00:00:00Z",
    },
  ],
};
const USAGE_EMPTY: UsageData = {
  total_cost_usd: "0",
  total_requests: 0,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  records: [],
};
const MODELS_RESPONSE = {
  object: "list",
  data: [
    {
      id: "openai/gpt-4o",
      name: "GPT-4o",
      context_length: 128000,
      prompt_per_token: 0.000003,
      completion_per_token: 0.000012,
      object: "model",
    },
  ],
};
const BUDGET_RESPONSE = { budget_usd_monthly: "25.00", spent_usd_month: "10.50" };
const ME_OWNER = {
  user_id: "user-1",
  tenant_id: "00000000-0000-0000-0000-000000000099",
  email: "owner@acme.io",
  role: "owner",
  exp: Math.floor(Date.now() / 1000) + 86400,
};
const KEY_FIXTURE = [
  {
    key_id: "key_abcdef0123456789",
    name: "prod-key",
    prefix: "sk-prod",
    created_at: "2026-06-01T00:00:00Z",
    revoked_at: null,
  },
];

function useUsageHappyHandlers() {
  server.use(
    http.get("http://localhost:3000/api/auth/me", () => HttpResponse.json(ME_OWNER)),
    http.get("http://localhost:3000/api/gw/admin/usage", () =>
      HttpResponse.json(USAGE_DATA),
    ),
    http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
      HttpResponse.json(MODELS_RESPONSE),
    ),
    http.get("http://localhost:3000/api/gw/admin/budget", () =>
      HttpResponse.json(BUDGET_RESPONSE),
    ),
  );
}

// ─────────────────────────────────────────────────────────────────────────────
describe("v13 verify · axe — usage/cost journey (no serious|critical)", () => {
  it("test_usage_journey_passes_axe", async () => {
    useUsageHappyHandlers();
    const { container } = render(<UsagePage />, { wrapper: Wrapper });

    // data must be loaded before the scan (an empty skeleton would falsely pass).
    // total cost lives in the always-visible hero region (outside the tabs).
    await waitFor(() => expect(screen.getByText(/1\.23/)).toBeInTheDocument());

    // The monitoring redesign (§3 v1) moves usage records behind the "Records"
    // tab; navigate there to confirm the loaded record's model_id. Only a tab
    // click is added — the seam and assertion target are unchanged.
    await userEvent.click(screen.getByRole("tab", { name: /records/i }));
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();

    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });
});

describe("v13 verify · axe — key/budget journey (no serious|critical)", () => {
  it("test_keys_list_passes_axe", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json(KEY_FIXTURE),
      ),
    );
    const { container } = render(<KeysPage />, { wrapper: Wrapper });

    await screen.findByText(/prod-key/);
    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });

  it("test_create_dialog_passes_axe", async () => {
    const { container } = render(
      <CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />,
    );
    expect(screen.getByRole("dialog", { name: /create api key/i })).toBeInTheDocument();
    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });

  it("test_plaintext_banner_passes_axe", async () => {
    const { container } = render(
      <PlaintextKeyBanner plaintextKey="sk-live-PLACEHOLDER-not-a-real-secret" onDismiss={vi.fn()} />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });
});

describe("v13 verify · AppShell landmarks + skip-link", () => {
  it("test_shell_landmarks_and_skiplink", async () => {
    const { container } = render(
      <AppShell>
        <h1>Usage</h1>
        <p>Content</p>
      </AppShell>,
    );

    // skip-link is the first focusable, targets #main
    const skip = container.querySelector('a[href="#main"]');
    expect(skip).not.toBeNull();
    expect(skip).toHaveTextContent(/skip to main/i);

    // landmarks: Primary nav + main#main
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
    const main = container.querySelector("main#main");
    expect(main).not.toBeNull();

    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });
});

describe("v13 verify · four state patterns via shared state components", () => {
  // Each state render is ALSO axe-scanned in isolation so a serious|critical
  // violation in the Loading/ErrorState/Empty shared components is caught here,
  // not only in the success-state full-page scans above.
  it("test_usage_four_state_patterns", async () => {
    // LOADING -> Loading (role=status)
    {
      const { container, unmount } = render(
        <UsageStatsCards isLoading isError={false} error={null} data={undefined} />,
      );
      expect(screen.getByRole("status")).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // ERROR -> ErrorState (role=alert)
    {
      const { container, unmount } = render(
        <UsageStatsCards
          isLoading={false}
          isError
          error={new Error("boom")}
          data={undefined}
        />,
      );
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // EMPTY -> Empty component (no usage records)
    {
      const { container, unmount } = render(
        <UsageTable isLoading={false} isError={false} data={USAGE_EMPTY} />,
      );
      expect(screen.getByText(/no usage records yet/i)).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // SUCCESS -> data renders
    {
      const { unmount } = render(
        <UsageStatsCards
          isLoading={false}
          isError={false}
          error={null}
          data={USAGE_DATA}
        />,
      );
      expect(screen.getByText("3")).toBeInTheDocument();
      expect(screen.getByText(/1\.23/)).toBeInTheDocument();
      unmount();
    }
  });
});

describe("v13 verify · responsive utilities present (static proxy)", () => {
  it("test_responsive_utilities_present_usage_shell", () => {
    // usage stat cards: 2-col mobile -> 4-col at sm: breakpoint, not a fixed px grid
    {
      const { container, unmount } = render(
        <UsageStatsCards
          isLoading={false}
          isError={false}
          error={null}
          data={USAGE_DATA}
        />,
      );
      expect(container.querySelector('[class*="sm:grid-cols-4"]')).not.toBeNull();
      unmount();
    }
    // shell: stacked on mobile -> sidebar row at lg: breakpoint
    {
      const { container, unmount } = render(
        <AppShell>
          <h1>Usage</h1>
        </AppShell>,
      );
      expect(container.querySelector('[class*="lg:flex-row"]')).not.toBeNull();
      unmount();
    }
  });
});

describe("v13 verify · keyboard operability (Create dialog gate)", () => {
  const user = userEvent.setup();

  it("test_create_dialog_keyboard_operable", async () => {
    const onClose = vi.fn();
    // outside focusables make the trap assertion discriminating
    render(
      <div>
        <button data-testid="outside-before">before</button>
        <CreateKeyDialog isOpen onClose={onClose} onSubmit={vi.fn(async () => {})} />
        <button data-testid="outside-after">after</button>
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: /create api key/i });

    // initial focus lands inside
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    // Tab from the LAST in-dialog control wraps back into the dialog
    const focusables = (within(dialog).getAllByRole("textbox") as HTMLElement[]).concat(
      within(dialog).getAllByRole("button"),
    );
    focusables[focusables.length - 1].focus();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByTestId("outside-after"));

    // Shift+Tab from the FIRST in-dialog control wraps to the last, never to #outside-before
    focusables[0].focus();
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByTestId("outside-before"));

    // Escape closes (delegates to onClose)
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });
});
