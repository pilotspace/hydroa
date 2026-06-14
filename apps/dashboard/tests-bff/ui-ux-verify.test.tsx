/**
 * ui-ux-verify.test.tsx (BFF project) — v13 milestone-exit verification pass.
 *
 * Sibling of tests/ui-ux-verify.test.tsx; covers the surfaces proven to render in the
 * BFF harness (tests-bff/): SpendPage (+ accessible spend chart) and KeyGovernanceEditor.
 *
 * Same frozen §3 bar: axe ZERO serious|critical (color-contrast EXCLUDED — jsdom/no-canvas,
 * declared browser residue), the four state patterns via shared components, responsive
 * utilities present, and the rotate-confirm dialog keyboard-operable (completing the
 * 3-dialog keyboard gate — create+revoke are covered in tests/keys-dialog-a11y.test.tsx).
 *
 * RED-FIRST: this file is absent before this commit; a real axe serious|critical finding is
 * a found-and-fixed a11y bug in the owning component, never a relaxed bar.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "vitest-axe";
import React from "react";

import { SpendPage } from "@/components/spend/SpendPage";
import {
  KeyGovernanceEditor,
  type ApiKeyGovernance,
} from "@/components/keys/KeyGovernanceEditor";

// ── axe helper: serious|critical only, color-contrast disabled (jsdom/no-canvas) ──
async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
}

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

// ── fixtures (match the FROZEN admin/spend contract, identical to spend-chart suite) ──
const SPEND_MONTH_FIXTURE = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 3,
    prompt_tokens: 300,
    completion_tokens: 150,
    cost_usd: "1.23",
  },
  buckets: [
    {
      bucket_start: "2026-06-01T00:00:00Z",
      requests: 2,
      prompt_tokens: 200,
      completion_tokens: 100,
      cost_usd: "0.80",
    },
    {
      bucket_start: "2026-06-08T00:00:00Z",
      requests: 1,
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "0.43",
    },
  ],
  breakdown: null,
};
const SPEND_ZERO_FIXTURE = {
  ...SPEND_MONTH_FIXTURE,
  totals: { ...SPEND_MONTH_FIXTURE.totals, requests: 0, cost_usd: "0.00" },
  buckets: [],
};

const KEY_GOV_FIXTURE: ApiKeyGovernance = {
  key_id: "key_abcdef0123456789",
  name: "prod-key",
  prefix: "sk-prod",
  created_at: "2026-06-01T00:00:00Z",
  revoked_at: null,
  monthly_budget_usd: "50.00",
  soft_budget_usd: "40.00",
  expires_at: null,
  model_allowlist: null,
  // v15 governance-completion-ui depth fields (editor now self-fetches teams,
  // so these renders must run inside the QueryClientProvider Wrapper)
  rpm_limit: null,
  tpm_limit: null,
  team_id: null,
  cache_enabled: false,
};

// ─────────────────────────────────────────────────────────────────────────────
describe("v13 verify · axe — spend journey (no serious|critical)", () => {
  it("test_spend_journey_passes_axe", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(SPEND_MONTH_FIXTURE),
      ),
    );
    const { container } = render(<SpendPage />, { wrapper: Wrapper });

    // chart + data loaded (not an empty skeleton)
    await screen.findByTestId("spend-chart");
    expect(screen.getAllByTestId("spend-bucket").length).toBe(2);

    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });
});

describe("v13 verify · axe — key governance editor (no serious|critical)", () => {
  it("test_governance_editor_passes_axe", async () => {
    const { container } = render(
      <KeyGovernanceEditor apiKey={KEY_GOV_FIXTURE} onUpdated={vi.fn()} />,
      { wrapper: Wrapper },
    );
    expect(screen.getByTestId("key-governance-editor")).toBeInTheDocument();
    // controls reachable by accessible name
    expect(screen.getByLabelText(/monthly budget/i)).toBeInTheDocument();

    const violations = await axeSeriousCritical(container);
    expect(violations).toEqual([]);
  });
});

describe("v13 verify · rotate-confirm dialog keyboard-operable", () => {
  const user = userEvent.setup();

  it("test_rotate_confirm_keyboard_operable", async () => {
    render(<KeyGovernanceEditor apiKey={KEY_GOV_FIXTURE} onUpdated={vi.fn()} />, {
      wrapper: Wrapper,
    });

    await user.click(screen.getByRole("button", { name: /^rotate$/i }));
    const dialog = await screen.findByRole("dialog", { name: /confirm key rotation/i });

    // focus moves inside the dialog on open
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    // Tab from the LAST in-dialog control wraps back inside (focus never escapes the dialog)
    const dialogButtons = within(dialog).getAllByRole("button");
    dialogButtons[dialogButtons.length - 1].focus();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);

    // Escape closes (no rotation performed)
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: /confirm key rotation/i }),
      ).not.toBeInTheDocument(),
    );
  });
});

describe("v13 verify · spend state patterns + responsive utilities", () => {
  it("test_spend_three_state_patterns", async () => {
    // LOADING -> Loading (role=status), asserted before the query resolves
    {
      server.use(
        http.get("http://localhost:3000/api/gw/admin/spend", () =>
          HttpResponse.json(SPEND_MONTH_FIXTURE),
        ),
      );
      const { unmount } = render(<SpendPage />, { wrapper: Wrapper });
      expect(screen.getByTestId("spend-loading")).toBeInTheDocument();
      unmount();
    }
    // ERROR -> spend-error / ErrorState (role=alert) + axe-clean in isolation
    {
      server.use(
        http.get("http://localhost:3000/api/gw/admin/spend", () =>
          HttpResponse.json({ title: "boom" }, { status: 500 }),
        ),
      );
      const { container, unmount } = render(<SpendPage />, { wrapper: Wrapper });
      await screen.findByTestId("spend-error");
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // ZERO/EMPTY -> spend-zero-state (Empty) + axe-clean in isolation
    {
      server.use(
        http.get("http://localhost:3000/api/gw/admin/spend", () =>
          HttpResponse.json(SPEND_ZERO_FIXTURE),
        ),
      );
      const { container, unmount } = render(<SpendPage />, { wrapper: Wrapper });
      await screen.findByTestId("spend-zero-state");
      expect(await axeSeriousCritical(container)).toEqual([]);
      unmount();
    }
    // SUCCESS + responsive utility present on the totals dl
    {
      server.use(
        http.get("http://localhost:3000/api/gw/admin/spend", () =>
          HttpResponse.json(SPEND_MONTH_FIXTURE),
        ),
      );
      const { container, unmount } = render(<SpendPage />, { wrapper: Wrapper });
      await screen.findByTestId("spend-chart");
      expect(container.querySelector('[class*="sm:grid-cols-4"]')).not.toBeNull();
      unmount();
    }
  });
});
