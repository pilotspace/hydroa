/**
 * Tests 20–35: UsagePage scenarios (dashboard-usage task)
 *
 * All tests fail at module resolution — the following components do not exist yet:
 *   @/components/usage/UsagePage
 *
 * This is the correct RED failure mode. The harness (msw, localStorage,
 * next/navigation mock) is identical to the dashboard-shell tests.
 *
 * Covers:
 *   - Usage section: cards + records table (success, empty, error, loading)
 *   - Model catalog section (success, error)
 *   - Budget widget (ceiling + spend, null/unlimited)
 *   - Budget edit: happy path, clear-to-null, client-side validation rejections,
 *     403 and 422 gateway error surfaces
 *   - Member role hides Edit Budget button
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { UsagePage } from "@/components/usage/UsagePage";

// ─────────────────────────────────────────────────────────────────────────────

// msw fixture shapes (matching gateway FROZEN contracts exactly)
const USAGE_RESPONSE = {
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

const USAGE_EMPTY_RESPONSE = {
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

const BUDGET_RESPONSE = {
  budget_usd_monthly: "25.00",
  spent_usd_month: "10.50",
};

const BUDGET_NULL_RESPONSE = {
  budget_usd_monthly: null,
  spent_usd_month: "0.00",
};

// ─────────────────────────────────────────────────────────────────────────────

/** Fresh QueryClient per test — no cross-test cache pollution. */
function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

function renderUsage() {
  return render(<UsagePage />, { wrapper: Wrapper });
}

/** Default happy-path handlers for all three sections.
 *  Individual tests override via server.use(...) before rendering. */
function useDefaultUsageHandlers() {
  server.use(
    http.get("http://localhost:3000/api/gw/admin/usage", () =>
      HttpResponse.json(USAGE_RESPONSE)
    ),
    http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
      HttpResponse.json(MODELS_RESPONSE)
    ),
    http.get("http://localhost:3000/api/gw/admin/budget", () =>
      HttpResponse.json(BUDGET_RESPONSE)
    )
  );
}

// ─────────────────────────────────────────────────────────────────────────────

describe("UsagePage", () => {
  const user = userEvent.setup();

  /**
   * TEST 20 — test_usage_renders_cards_and_table
   * Scenario: usage page renders aggregate cards and records table for owner
   */
  it("test_usage_renders_cards_and_table", async () => {
    // Arrange: role comes from /api/auth/me
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      )
    );
    useDefaultUsageHandlers();

    // Act
    renderUsage();

    // Assert: aggregate cards
    await waitFor(() => {
      // Total requests card
      expect(screen.getByText("3")).toBeInTheDocument();
      // Prompt tokens
      expect(screen.getByText("300")).toBeInTheDocument();
      // Completion tokens
      expect(screen.getByText("150")).toBeInTheDocument();
      // Total cost
      expect(screen.getByText(/1\.23/)).toBeInTheDocument();
    });

    // Records table row
    await user.click(screen.getByRole("tab", { name: /records/i }));
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();

    // Owner sees Edit Budget button
    expect(
      screen.getByRole("button", { name: /edit budget/i })
    ).toBeInTheDocument();
  });

  /**
   * TEST 21 — test_usage_empty_state
   * Scenario: usage page empty state — no records
   */
  it("test_usage_empty_state", async () => {
    // Arrange
    server.use(
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_EMPTY_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert: all aggregate cards show 0
    await waitFor(() => {
      // All four cards should have value "0" (may appear multiple times)
      const zeros = screen.getAllByText("0");
      expect(zeros.length).toBeGreaterThanOrEqual(4);
    });

    // Empty state message for records
    await user.click(screen.getByRole("tab", { name: /records/i }));
    expect(
      screen.getByText(/no usage records yet/i)
    ).toBeInTheDocument();
  });

  /**
   * TEST 22 — test_usage_error_state
   * Scenario: usage section error state
   */
  it("test_usage_error_state", async () => {
    // Arrange
    server.use(
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(
          { title: "Internal server error", status: 500 },
          { status: 500 }
        )
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert
    await waitFor(() => {
      expect(
        screen.getByText(/internal server error/i)
      ).toBeInTheDocument();
    });
    // No record rows
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST 23 — test_usage_loading_state
   * Scenario: usage page loading state
   */
  it("test_usage_loading_state", async () => {
    // Arrange: GET /api/gw/admin/usage never resolves in this test window
    server.use(
      http.get(
        "http://localhost:3000/api/gw/admin/usage",
        () => new Promise<never>(() => { /* intentionally never resolves */ })
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert: loading indicator visible before response
    await waitFor(() => {
      const spinner =
        screen.queryByRole("status") ??
        screen.queryByTestId("loading") ??
        screen.queryByText(/loading/i) ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
    // No record rows shown during loading
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST 24 — test_catalog_renders_rows
   * Scenario: model catalog section renders table rows
   */
  it("test_catalog_renders_rows", async () => {
    // Arrange
    useDefaultUsageHandlers();

    // Act
    renderUsage();

    // Assert: catalog table row
    await user.click(screen.getByRole("tab", { name: /catalog/i }));
    await waitFor(() => {
      expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
    // Prices visible (float values)
    expect(screen.getByText(/0\.000003/)).toBeInTheDocument();
  });

  /**
   * TEST 24b — test_catalog_reads_admin_plane_not_v1_models  [regression]
   *
   * The model catalog must be read over the JWT/control-plane twin
   * GET /admin/catalog/models, NEVER the data-plane GET /v1/models. /v1/* sits
   * behind the edge ext_authz (sk-/agent token only); a browser SESSION JWT 401s
   * through the edge, and the BFF clears the cookie → the user is logged out just
   * by opening this page. This pins the endpoint: the OLD path is stubbed to return
   * the 401 the edge would produce, so a regression renders no catalog (and would
   * trip the logout) instead of the model rows.
   */
  it("test_catalog_reads_admin_plane_not_v1_models", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      ),
      // The control-plane twin serves a sentinel model.
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json({
          object: "list",
          data: [
            {
              id: "sentinel/admin-catalog",
              name: "Sentinel Admin Catalog",
              context_length: 1000,
              prompt_per_token: 0.000003,
              completion_per_token: 0.000012,
              object: "model",
            },
          ],
        })
      ),
      // The data-plane path is what the edge would 401 → must NOT be called.
      http.get("http://localhost:3000/api/gw/v1/models", () =>
        HttpResponse.json({ code: "ERR_AUTH_SESSION_EXPIRED" }, { status: 401 })
      )
    );

    renderUsage();

    // The sentinel from the admin-catalog twin renders → the admin plane was used.
    await user.click(screen.getByRole("tab", { name: /catalog/i }));
    await waitFor(() => {
      expect(screen.getByText("Sentinel Admin Catalog")).toBeInTheDocument();
    });
  });

  /**
   * TEST 25 — test_catalog_error_state
   * Scenario: model catalog error state
   */
  it("test_catalog_error_state", async () => {
    // Arrange
    server.use(
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(
          {
            title: "Catalog not synced",
            status: 409,
            code: "ERR_CATALOG_EMPTY",
          },
          { status: 409 }
        )
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert
    await user.click(screen.getByRole("tab", { name: /catalog/i }));
    await waitFor(() => {
      expect(screen.getByText(/catalog not synced/i)).toBeInTheDocument();
    });
    // No catalog rows — usage records row may exist, so we check catalog section specifically
    expect(screen.queryByText("GPT-4o")).not.toBeInTheDocument();
  });

  /**
   * TEST 26 — test_budget_widget_shows_ceiling_and_spend
   * Scenario: budget widget shows ceiling and spend
   */
  it("test_budget_widget_shows_ceiling_and_spend", async () => {
    // Arrange
    useDefaultUsageHandlers(); // BUDGET_RESPONSE = { budget_usd_monthly: "25.00", spent_usd_month: "10.50" }

    // Act
    renderUsage();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/25\.00/)).toBeInTheDocument();
      expect(screen.getByText(/10\.50/)).toBeInTheDocument();
    });
  });

  /**
   * TEST 27 — test_budget_widget_null_shows_unlimited
   * Scenario: budget widget shows unlimited when ceiling is null
   */
  it("test_budget_widget_null_shows_unlimited", async () => {
    // Arrange
    server.use(
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_NULL_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/unlimited/i)).toBeInTheDocument();
      expect(screen.getByText(/0\.00/)).toBeInTheDocument();
    });
  });

  /**
   * TEST 28 — test_budget_edit_happy_path
   * Scenario: owner edits budget — happy path
   */
  it("test_budget_edit_happy_path", async () => {
    // Arrange: role from /api/auth/me; owner sees Edit Budget button
    let putCallCount = 0;
    let putBody: unknown = null;
    let budgetGetCount = 0;

    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", async () => {
        budgetGetCount++;
        if (budgetGetCount === 1) {
          return HttpResponse.json({
            budget_usd_monthly: "25.00",
            spent_usd_month: "10.00",
          });
        }
        // After PUT, return updated value
        return HttpResponse.json({
          budget_usd_monthly: "50.00",
          spent_usd_month: "10.00",
        });
      }),
      http.put("http://localhost:3000/api/gw/admin/budget", async ({ request }) => {
        putCallCount++;
        putBody = await request.json();
        return HttpResponse.json({ budget_usd_monthly: "50.00" });
      })
    );

    // Act
    renderUsage();

    // Wait for Edit Budget button
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    // Fill in new budget value
    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);
    await user.type(input!, "50.00");
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget/i })
    );

    // Assert: PUT called once with correct body
    await waitFor(() => {
      expect(putCallCount).toBe(1);
    });
    expect((putBody as { budget_usd_monthly: string }).budget_usd_monthly).toBe("50.00");

    // Widget refreshes to show new value
    await waitFor(() => {
      expect(screen.getByText(/50\.00/)).toBeInTheDocument();
    });
  });

  /**
   * TEST 29 — test_budget_edit_clear_to_unlimited
   * Scenario: owner clears budget to unlimited
   */
  it("test_budget_edit_clear_to_unlimited", async () => {
    // Arrange: role from /api/auth/me
    let putCallCount = 0;
    let putBody: unknown = null;

    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json({
          budget_usd_monthly: "25.00",
          spent_usd_month: "5.00",
        })
      ),
      http.put("http://localhost:3000/api/gw/admin/budget", async ({ request }) => {
        putCallCount++;
        putBody = await request.json();
        return HttpResponse.json({ budget_usd_monthly: null });
      })
    );

    // Act
    renderUsage();

    // Open edit form
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    // Clear the input
    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);

    // Submit empty value (clear = null)
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget|clear/i })
    );

    // Assert: PUT called once with null
    await waitFor(() => {
      expect(putCallCount).toBe(1);
    });
    expect((putBody as { budget_usd_monthly: null }).budget_usd_monthly).toBeNull();

    // Widget shows unlimited
    await waitFor(() => {
      expect(screen.getByText(/unlimited/i)).toBeInTheDocument();
    });
  });

  /**
   * TEST 30 — test_budget_edit_negative_no_api_call
   * Scenario: budget edit rejected — negative value, no API call
   */
  it("test_budget_edit_negative_no_api_call", async () => {
    // Arrange: role from /api/auth/me
    let putCallCount = 0;

    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      ),
      http.put("http://localhost:3000/api/gw/admin/budget", () => {
        putCallCount++;
        return HttpResponse.json({ budget_usd_monthly: null });
      })
    );

    // Act
    renderUsage();

    // Open edit form
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);
    await user.type(input!, "-5.00");
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget/i })
    );

    // Assert: inline error, zero PUT calls
    await waitFor(() => {
      const error =
        screen.queryByRole("alert") ??
        screen.queryByText(/negative|must be|non-negative|invalid/i);
      expect(error).toBeInTheDocument();
    });
    expect(putCallCount).toBe(0);
  });

  /**
   * TEST 31 — test_budget_edit_non_numeric_no_api_call
   * Scenario: budget edit rejected — non-numeric string, no API call
   */
  it("test_budget_edit_non_numeric_no_api_call", async () => {
    // Arrange: role from /api/auth/me
    let putCallCount = 0;

    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      ),
      http.put("http://localhost:3000/api/gw/admin/budget", () => {
        putCallCount++;
        return HttpResponse.json({ budget_usd_monthly: null });
      })
    );

    // Act
    renderUsage();

    // Open edit form
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);
    await user.type(input!, "abc");
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget/i })
    );

    // Assert: inline error, zero PUT calls
    await waitFor(() => {
      const error =
        screen.queryByRole("alert") ??
        screen.queryByText(/invalid|must be|decimal|numeric/i);
      expect(error).toBeInTheDocument();
    });
    expect(putCallCount).toBe(0);
  });

  /**
   * TEST 32 — test_budget_edit_403_surfaces_error
   * Scenario: budget edit 403 — member role surfaces error
   */
  it("test_budget_edit_403_surfaces_error", async () => {
    // Arrange: role from /api/auth/me (owner to render button); gateway returns 403
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      ),
      http.put("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            code: "ERR_AUTH_FORBIDDEN",
          },
          { status: 403 }
        )
      )
    );

    // Act
    renderUsage();

    // Open edit form and submit
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);
    await user.type(input!, "50.00");
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget/i })
    );

    // Assert: error title shown, no navigation
    await waitFor(() => {
      expect(screen.getByText(/forbidden/i)).toBeInTheDocument();
    });
    const router = getRouterMock();
    expect(
      router.push.mock.calls.some((c: string[]) => c[0] !== "/login")
    ).toBe(false); // no unexpected navigation
  });

  /**
   * TEST 33 — test_budget_edit_422_surfaces_error
   * Scenario: budget edit 422 — surfaces error inline
   */
  it("test_budget_edit_422_surfaces_error", async () => {
    // Arrange: role from /api/auth/me
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "owner@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      ),
      http.put("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Invalid budget value",
            status: 422,
            code: "ERR_PAYLOAD_INVALID",
          },
          { status: 422 }
        )
      )
    );

    // Act
    renderUsage();

    // Open edit form and submit a value that passes client-side validation
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /edit budget/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /edit budget/i }));

    await waitFor(() => {
      const input =
        screen.queryByLabelText(/budget/i) ??
        screen.queryByRole("spinbutton") ??
        screen.queryByRole("textbox");
      expect(input).toBeInTheDocument();
    });
    const input =
      screen.queryByLabelText(/budget/i) ??
      screen.queryByRole("spinbutton") ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);
    await user.clear(input!);
    await user.type(input!, "99.99");
    await user.click(
      screen.getByRole("button", { name: /save|confirm|update|set budget/i })
    );

    // Assert: 422 title shown inline; budget widget unchanged (still shows 25.00)
    await waitFor(() => {
      expect(screen.getByText(/invalid budget value/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/25\.00/)).toBeInTheDocument();
  });

  /**
   * TEST 35 — test_member_no_edit_budget_button
   * Scenario: member role does not see Edit Budget button
   */
  it("test_member_no_edit_budget_button", async () => {
    // Arrange: role from GET /api/auth/me returning role:"member"
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "user-1",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "member@acme.io",
          role: "member",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      ),
      http.get("http://localhost:3000/api/gw/admin/usage", () =>
        HttpResponse.json(USAGE_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json(MODELS_RESPONSE)
      ),
      http.get("http://localhost:3000/api/gw/admin/budget", () =>
        HttpResponse.json(BUDGET_RESPONSE)
      )
    );

    // Act
    renderUsage();

    // Assert: budget values visible but Edit Budget button absent
    await waitFor(() => {
      expect(screen.getByText(/25\.00/)).toBeInTheDocument();
      expect(screen.getByText(/10\.50/)).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /edit budget/i })
    ).not.toBeInTheDocument();
  });
});
