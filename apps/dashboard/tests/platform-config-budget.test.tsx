/**
 * tests/platform-config-budget.test.tsx — RED suite for the Config (M6) and
 * Budget (M7) tabs of the admin-console-ui tenant-detail screen.
 *
 * Config: GET/PUT .../{tenantId}/cache + .../guardrails (byte-identical DTOs to
 * self-service CacheSettings.tsx/GuardrailSettings.tsx — confirmed by reading
 * platform_tenant_config_router.py's own imports this session).
 * Budget: GET/PUT .../{tenantId}/budget (byte-identical to gateway/budgets/api/
 * schemas.py, confirmed by reading it directly). Validation mirrors
 * TeamBudgetForm.tsx's validateBudget exactly (empty clears; non-numeric/zero/
 * negative blocked client-side).
 *
 * RED before Build: neither `PlatformConfigTab` nor `PlatformBudgetTab` exists yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformConfigTab } from "@/components/platform/PlatformConfigTab";
import { PlatformBudgetTab } from "@/components/platform/PlatformBudgetTab";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderWithClient(ui: React.ReactElement) {
  const client = makeQueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("PlatformConfigTab", () => {
  it("test_config_cache_saves_independently_of_guardrails", async () => {
    let capturedCacheBody: unknown = null;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, () =>
        HttpResponse.json({ enabled: false, semantic_enabled: false }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/guardrails`, () =>
        HttpResponse.json({ prompt_injection: null, pii_mask: null }),
      ),
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, async ({ request }) => {
        capturedCacheBody = await request.json();
        return HttpResponse.json({ enabled: true, semantic_enabled: false });
      }),
    );

    renderWithClient(<PlatformConfigTab tenantId={TENANT_ID} />);

    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText(/enable response cache/i));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedCacheBody).toEqual({ enabled: true, semantic_enabled: false });
    });
    await waitFor(() => {
      expect(screen.getByText(/saved\./i)).toBeInTheDocument();
    });
    // Guardrails card unaffected — its own fields still render, no error anywhere.
    // Anchored to match ONLY the fieldset legend ("Prompt injection"), not the
    // longer label text ("Enable prompt injection protection") that also
    // contains this substring — a query-precision fix, not a weaker assertion:
    // the same "Guardrails card rendered, unaffected" fact is still being checked.
    expect(screen.getByText(/^prompt injection$/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_config_cache_save_failure_shows_own_mutError", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, () =>
        HttpResponse.json({ enabled: false, semantic_enabled: false }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/guardrails`, () =>
        HttpResponse.json({ prompt_injection: null, pii_mask: null }),
      ),
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, () =>
        HttpResponse.json(
          { title: "Insufficient role for this action", status: 403 },
          { status: 403 },
        ),
      ),
    );

    renderWithClient(<PlatformConfigTab tenantId={TENANT_ID} />);

    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/insufficient role/i);
    });
    // Guardrails card is unaffected by the Cache card's own error (R4 scoping).
    // Anchored for the same query-precision reason as the sibling test above.
    expect(screen.getByText(/^prompt injection$/i)).toBeInTheDocument();
  });

  // console-flat-visual-pass, 2026-07-06 (M1) — both Cards go from variant omitted
  // (default) to variant="flat"; the Guardrails fieldsets are OUTSIDE M1's Card-only
  // scope and keep their existing rounded-lg border-border treatment unchanged.
  it("test_config_both_cards_render_flat_and_fieldsets_unchanged", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, () =>
        HttpResponse.json({ enabled: false, semantic_enabled: false }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/guardrails`, () =>
        HttpResponse.json({ prompt_injection: null, pii_mask: null }),
      ),
    );

    const { container } = renderWithClient(<PlatformConfigTab tenantId={TENANT_ID} />);

    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });

    const flatCards = container.querySelectorAll('[data-variant="flat"]');
    expect(flatCards.length).toBe(2);
    flatCards.forEach((card) => {
      expect(card.className).toContain("rounded-[var(--radius-flat-card)]");
      expect(card.className).toContain("shadow-none");
      expect(card.className).not.toContain("border-border");
    });

    const fieldsets = container.querySelectorAll("fieldset");
    expect(fieldsets.length).toBe(2);
    fieldsets.forEach((fs) => {
      expect(fs.className).toContain("rounded-lg");
      expect(fs.className).toContain("border-border");
    });
  });

  // console-flat-visual-pass (M5) — Input×2 (pattern name/regex) and Button×4 (Save,
  // Save guardrails, Remove pattern, Add pattern) get the flat-control radius class;
  // the raw <select> mode pickers are explicitly OUT of M5's scope.
  it("test_config_inputs_and_buttons_get_flat_radius_class", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/cache`, () =>
        HttpResponse.json({ enabled: false, semantic_enabled: false }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/guardrails`, () =>
        HttpResponse.json({ prompt_injection: null, pii_mask: null }),
      ),
    );

    renderWithClient(<PlatformConfigTab tenantId={TENANT_ID} />);
    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /^save$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    expect(screen.getByRole("button", { name: /save guardrails/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    const addPatternButton = screen.getByRole("button", { name: /add pattern/i });
    expect(addPatternButton.className).toContain("rounded-[var(--radius-flat-control)]");

    fireEvent.click(addPatternButton);
    const nameInput = screen.getByLabelText(/pattern name 1/i);
    const regexInput = screen.getByLabelText(/pattern regex 1/i);
    expect(nameInput.className).toContain("rounded-[var(--radius-flat-control)]");
    expect(regexInput.className).toContain("rounded-[var(--radius-flat-control)]");
    expect(screen.getByRole("button", { name: /remove pattern 1/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );

    const piModeSelect = screen.getByLabelText(/prompt injection mode/i);
    expect(piModeSelect.className).toContain("rounded-md");
    expect(piModeSelect.className).not.toContain("rounded-[var(--radius-flat-control)]");
  });
});

describe("PlatformBudgetTab", () => {
  it("test_budget_edit_clears_to_unlimited", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json({ budget_usd_monthly: "500.00", spent_usd_month: "120.00" }),
      ),
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ budget_usd_monthly: null });
      }),
    );

    renderWithClient(<PlatformBudgetTab tenantId={TENANT_ID} />);

    await waitFor(() => {
      expect(screen.getByText("500.00")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /edit budget/i }));
    const input = screen.getByLabelText(/^budget$/i);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ budget_usd_monthly: null });
    });
    await waitFor(() => {
      expect(screen.getByText(/unlimited/i)).toBeInTheDocument();
    });
    // spent-this-month is preserved (the PUT response omits it — merge, not clobber)
    expect(screen.getByText("120.00")).toBeInTheDocument();
  });

  it("test_budget_edit_blocks_invalid_client_side", async () => {
    let putCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json({ budget_usd_monthly: "500.00", spent_usd_month: "120.00" }),
      ),
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () => {
        putCalled = true;
        return HttpResponse.json({ budget_usd_monthly: "500.00" });
      }),
    );

    renderWithClient(<PlatformBudgetTab tenantId={TENANT_ID} />);
    await waitFor(() => {
      expect(screen.getByText("500.00")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /edit budget/i }));

    const input = screen.getByLabelText(/^budget$/i);

    fireEvent.change(input, { target: { value: "-10" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(putCalled).toBe(false);

    fireEvent.change(input, { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(putCalled).toBe(false);
  });

  // console-flat-visual-pass, 2026-07-06 (M2) — both StatCard instances pass
  // variant="flat", wiring the additive passthrough prop end-to-end.
  it("test_budget_statcards_render_flat_variant", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json({ budget_usd_monthly: "500.00", spent_usd_month: "120.00" }),
      ),
    );
    const { container } = renderWithClient(<PlatformBudgetTab tenantId={TENANT_ID} />);
    await waitFor(() => {
      expect(screen.getByText("500.00")).toBeInTheDocument();
    });
    const flatStatCards = container.querySelectorAll(
      '[data-slot="stat-card"][data-variant="flat"]',
    );
    expect(flatStatCards.length).toBe(2);
  });

  // console-flat-visual-pass (M5) — Edit Budget/Save/Cancel Buttons and the budget
  // amount Input get the page-local flat-control radius class.
  it("test_budget_buttons_and_input_get_flat_radius_class", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json({ budget_usd_monthly: "500.00", spent_usd_month: "120.00" }),
      ),
    );
    renderWithClient(<PlatformBudgetTab tenantId={TENANT_ID} />);
    await waitFor(() => {
      expect(screen.getByText("500.00")).toBeInTheDocument();
    });

    const editButton = screen.getByRole("button", { name: /edit budget/i });
    expect(editButton.className).toContain("rounded-[var(--radius-flat-control)]");
    fireEvent.click(editButton);

    const input = screen.getByLabelText(/^budget$/i);
    expect(input.className).toContain("rounded-[var(--radius-flat-control)]");
    expect(screen.getByRole("button", { name: /^save$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    expect(screen.getByRole("button", { name: /^cancel$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
  });
});
