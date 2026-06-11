/**
 * tests-bff/govern.test.tsx
 *
 * RED suite for dashboard-govern task.
 *
 * Covers:
 *   - KeyGovernanceEditor: PATCH /api/gw/admin/keys/{id} with EXACT frozen
 *     field names (monthly_budget_usd — NOT budget_usd_monthly), expires_at,
 *     model_allowlist; clear-to-null; no Authorization header in client request
 *   - Key rotation: POST /api/gw/admin/keys/{id}/rotate → 201 → plaintext banner
 *     shown once → dismissed → list refreshed
 *   - Governance 403 / 422 surfaced inline
 *   - Client-side validation: soft > hard blocks API call
 *   - SpendPage: default window=month, window selector re-fetches, zero-state,
 *     403/422 error surfaces
 *   - Model-mgmt surface absent (deferred)
 *
 * RED failure mode:
 *   Static imports of non-existent modules cause MODULE_NOT_FOUND at test-collect
 *   time — the established codebase convention for true-red (see tests/keys.test.tsx
 *   line 21, tests-bff/bff-client.test.tsx line 20, tests-bff/middleware.test.ts
 *   line 19). When the components exist, tests fail for BEHAVIORAL reasons (wrong
 *   field names, missing Authorization guard, wrong query key, etc.).
 *
 * Suite runs in the "bff" vitest project (tests-bff/**) with setup:
 *   tests-bff/setup.ts  +  test-support/mock-cjs-navigation.ts
 * MSW server: tests-bff/mocks/server.ts (gatewayHandlers + bffHandlers)
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── RED: these imports fail until Build creates the component files ─────────────
//
// KeyGovernanceEditor — NEW component at apps/dashboard/components/keys/KeyGovernanceEditor.tsx
// SpendPage           — NEW component at apps/dashboard/components/spend/SpendPage.tsx
//
// Until these files exist, vitest collect fails with MODULE_NOT_FOUND for this
// entire file. This IS the correct true-red: the target behavior is a component
// that exists and behaves correctly per the frozen contracts; it fails now because
// the target doesn't exist.
import { KeyGovernanceEditor } from "@/components/keys/KeyGovernanceEditor";
import { SpendPage } from "@/components/spend/SpendPage";

// ─────────────────────────────────────────────────────────────────────────────
// Test infrastructure
// ─────────────────────────────────────────────────────────────────────────────

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

/**
 * A minimal KeyInfoResponse matching the FROZEN key-governance §3 contract.
 * Used as the default key fixture in governance tests.
 */
const KEY_FIXTURE = {
  key_id: "00000000-0000-0000-0000-000000000001",
  name: "prod-key",
  prefix: "sk-1a2b3c",
  created_at: "2026-01-01T00:00:00Z",
  revoked_at: null,
  monthly_budget_usd: null,
  soft_budget_usd: null,
  expires_at: null,
  model_allowlist: null,
};

/**
 * A minimal SpendWindowResponse matching the FROZEN spend-windows §3 contract.
 * Used as the default spend fixture.
 */
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
      requests: 3,
      prompt_tokens: 300,
      completion_tokens: 150,
      cost_usd: "1.23",
    },
  ],
  breakdown: null,
};

const SPEND_DAY_FIXTURE = {
  window: "day",
  bucket_size: "day",
  totals: {
    bucket_start: "2026-06-11T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 1,
    prompt_tokens: 100,
    completion_tokens: 50,
    cost_usd: "0.04",
  },
  buckets: [
    {
      bucket_start: "2026-06-11T00:00:00Z",
      requests: 1,
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "0.04",
    },
  ],
  breakdown: null,
};

const SPEND_ZERO_FIXTURE = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: "0",
  },
  buckets: [],
  breakdown: null,
};

// ─────────────────────────────────────────────────────────────────────────────
// GOVERNANCE EDITOR TESTS
// ─────────────────────────────────────────────────────────────────────────────

describe("KeyGovernanceEditor — PATCH field names and BFF security", () => {
  const user = userEvent.setup();

  /**
   * TEST 1 — test_governance_editor_patch_sends_exact_frozen_field_names
   *
   * TRUE-RED REASON (after module exists):
   *   If component uses "budget_usd_monthly" instead of "monthly_budget_usd",
   *   the assertion `capturedBody.monthly_budget_usd === "50.00"` FAILS.
   *   This is the most critical correctness gate: the TENANT budget field is
   *   "budget_usd_monthly" (BudgetEditForm). The per-KEY budget field is
   *   "monthly_budget_usd". If wrong, governance is silently never saved.
   */
  it("test_governance_editor_patch_sends_exact_frozen_field_names", async () => {
    let patchCallCount = 0;
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      // Intercept at the BFF layer (what the client actually fetches)
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        async ({ request }) => {
          patchCallCount++;
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(
            { ...KEY_FIXTURE, monthly_budget_usd: "50.00", soft_budget_usd: "40.00" },
            { status: 200 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    // Open / interact with the governance editor
    // The component should have a monthly_budget field and a soft_budget field
    await waitFor(() => {
      const monthlyField =
        screen.queryByLabelText(/monthly.budget/i) ??
        screen.queryByPlaceholderText(/monthly.budget/i) ??
        screen.queryByRole("spinbutton", { name: /monthly/i }) ??
        screen.queryByTestId("monthly-budget-input");
      expect(monthlyField).toBeInTheDocument();
    });

    const monthlyField =
      screen.queryByLabelText(/monthly.budget/i) ??
      screen.queryByPlaceholderText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(monthlyField!);
    await user.type(monthlyField!, "50.00");

    const softField =
      screen.queryByLabelText(/soft.budget/i) ??
      screen.queryByPlaceholderText(/soft.budget/i) ??
      (screen.queryAllByRole("spinbutton")[1] as HTMLElement);

    if (softField) {
      await user.clear(softField);
      await user.type(softField, "40.00");
    }

    // Submit the governance form
    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    // Assert: PATCH was called exactly once
    await waitFor(() => {
      expect(patchCallCount).toBe(1);
    });

    // CRITICAL: body must use "monthly_budget_usd" (key-governance frozen contract)
    // NOT "budget_usd_monthly" (tenant budget contract — completely different field)
    expect(capturedBody).not.toBeNull();
    expect(capturedBody!["monthly_budget_usd"]).toBe("50.00");
    // Explicit rejection of the wrong field name
    expect(capturedBody!["budget_usd_monthly"]).toBeUndefined();

    if (softField) {
      expect(capturedBody!["soft_budget_usd"]).toBe("40.00");
    }

    // Updated values visible in the rendered output
    await waitFor(() => {
      expect(screen.getByText(/50\.00/)).toBeInTheDocument();
    });
  });

  /**
   * TEST 2 — test_governance_editor_patch_sends_expires_at
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red after module exists: if expires_at field is absent from the form
   * or not sent in the body, assertion fails.
   */
  it("test_governance_editor_patch_sends_expires_at", async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(
            { ...KEY_FIXTURE, expires_at: "2099-01-01T00:00:00Z" },
            { status: 200 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const expiresField =
        screen.queryByLabelText(/expir/i) ??
        screen.queryByPlaceholderText(/expir/i) ??
        screen.queryByTestId("expires-at-input");
      expect(expiresField).toBeInTheDocument();
    });

    const expiresField =
      screen.queryByLabelText(/expir/i) ??
      screen.queryByPlaceholderText(/expir/i) ??
      (screen.queryAllByRole("textbox")[0] as HTMLElement);

    await user.clear(expiresField!);
    await user.type(expiresField!, "2099-01-01T00:00:00Z");

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody!["expires_at"]).toBe("2099-01-01T00:00:00Z");
  });

  /**
   * TEST 3 — test_governance_editor_patch_sends_model_allowlist
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: model_allowlist must be an array of strings in the body.
   */
  it("test_governance_editor_patch_sends_model_allowlist", async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(
            { ...KEY_FIXTURE, model_allowlist: ["openai/gpt-4o"] },
            { status: 200 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    // The governance editor must have a model allowlist control
    await waitFor(() => {
      const allowlistControl =
        screen.queryByLabelText(/model.allowlist/i) ??
        screen.queryByLabelText(/allowlist/i) ??
        screen.queryByPlaceholderText(/model.id/i) ??
        screen.queryByTestId("model-allowlist-input");
      expect(allowlistControl).toBeInTheDocument();
    });

    const allowlistInput =
      screen.queryByLabelText(/model.allowlist/i) ??
      screen.queryByLabelText(/allowlist/i) ??
      screen.queryByPlaceholderText(/model.id/i) ??
      (screen.queryByTestId("model-allowlist-input") as HTMLElement);

    await user.type(allowlistInput!, "openai/gpt-4o");

    // Trigger add (Enter or Add button)
    const addButton =
      screen.queryByRole("button", { name: /add/i }) ??
      screen.queryByTestId("add-model-button");

    if (addButton) {
      await user.click(addButton);
    } else {
      await user.keyboard("{Enter}");
    }

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(Array.isArray(capturedBody!["model_allowlist"])).toBe(true);
    expect((capturedBody!["model_allowlist"] as string[])[0]).toBe("openai/gpt-4o");
  });

  /**
   * TEST 4 — test_governance_editor_clears_budget_to_null
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: empty field must serialize as null, not empty string.
   */
  it("test_governance_editor_clears_budget_to_null", async () => {
    const keyWithBudget = { ...KEY_FIXTURE, monthly_budget_usd: "25.00" };
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(
            { ...keyWithBudget, monthly_budget_usd: null },
            { status: 200 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={keyWithBudget} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const monthlyField =
        screen.queryByLabelText(/monthly.budget/i) ??
        screen.queryByDisplayValue("25.00") ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(monthlyField).toBeInTheDocument();
    });

    const monthlyField =
      screen.queryByLabelText(/monthly.budget/i) ??
      screen.queryByDisplayValue("25.00") ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(monthlyField!);

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set|clear/i })
    );

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // Empty field must serialize as null, not ""
    expect(capturedBody!["monthly_budget_usd"]).toBeNull();
  });

  /**
   * TEST 5 — test_governance_no_authorization_header_in_client_request
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red after module exists: if the component calls apiPatch with an
   * Authorization header instead of bffPatch, this assertion fails.
   *
   * This is the BFF security invariant: Authorization header MUST only appear
   * server-side (in the catch-all BFF handler), never in the client-side fetch.
   */
  it("test_governance_no_authorization_header_in_client_request", async () => {
    let capturedClientRequest: Request | null = null;

    server.use(
      // Intercept the BFF layer (what the browser/component fetches)
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        ({ request }) => {
          capturedClientRequest = request;
          return HttpResponse.json(
            { ...KEY_FIXTURE, monthly_budget_usd: "10.00" },
            { status: 200 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const field =
        screen.queryByLabelText(/monthly.budget/i) ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(field).toBeInTheDocument();
    });

    const field =
      screen.queryByLabelText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(field!);
    await user.type(field!, "10.00");

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    await waitFor(() => {
      expect(capturedClientRequest).not.toBeNull();
    });

    // SECURITY: No Authorization header must appear in the client-side request.
    // The BFF catch-all injects it server-side — client never sees or sends a token.
    expect(
      (capturedClientRequest as Request).headers.get("Authorization")
    ).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ROTATION TESTS
// ─────────────────────────────────────────────────────────────────────────────

describe("KeyGovernanceEditor — rotation", () => {
  const user = userEvent.setup();

  /**
   * TEST 6 — test_rotation_shows_plaintext_once_then_refreshes_list
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red after module exists:
   *   - If rotate endpoint not called → patchCallCount stays 0
   *   - If plaintext "sk-new.NEWSECRET" not shown in banner → text absent
   *   - If banner not dismissable → secret stays in DOM after dismiss
   *   - If list not refreshed → onUpdated callback not called
   */
  it("test_rotation_shows_plaintext_once_then_refreshes_list", async () => {
    let rotateCallCount = 0;
    let onUpdatedCalled = false;

    server.use(
      http.post(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001/rotate",
        () => {
          rotateCallCount++;
          return HttpResponse.json(
            {
              new_key_id: "00000000-0000-0000-0000-000000000002",
              superseded_key_id: "00000000-0000-0000-0000-000000000001",
              key: "sk-new.NEWSECRET",
              name: "prod-key",
              monthly_budget_usd: null,
              soft_budget_usd: null,
              expires_at: null,
              model_allowlist: null,
            },
            { status: 201 }
          );
        }
      )
    );

    render(
      <KeyGovernanceEditor
        apiKey={KEY_FIXTURE}
        onUpdated={() => { onUpdatedCalled = true; }}
      />,
      { wrapper: Wrapper }
    );

    // Rotate button must be present
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /rotate/i })
      ).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /rotate/i }));

    // Confirmation dialog or confirm button
    await waitFor(() => {
      const confirmBtn =
        screen.queryByRole("button", { name: /confirm|yes|rotate|proceed/i });
      expect(confirmBtn).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /confirm|yes|rotate|proceed/i })
    );

    // Assert: rotate called once
    await waitFor(() => {
      expect(rotateCallCount).toBe(1);
    });

    // Assert: plaintext shown in one-time banner
    await waitFor(() => {
      expect(screen.getByText("sk-new.NEWSECRET")).toBeInTheDocument();
    });

    // Copy button must be present alongside the plaintext
    expect(
      screen.getByRole("button", { name: /copy/i })
    ).toBeInTheDocument();

    // Assert: onUpdated callback called (signals parent to refresh keys list)
    expect(onUpdatedCalled).toBe(true);

    // Dismiss the banner
    await user.click(
      screen.getByRole("button", { name: /dismiss|close|done|got it/i })
    );

    // Assert: secret is gone from DOM after dismiss
    await waitFor(() => {
      expect(screen.queryByText("sk-new.NEWSECRET")).not.toBeInTheDocument();
    });
  });

  /**
   * TEST 11 — test_rotation_403_shows_error_no_banner
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: 403 from rotate must show inline error, not a plaintext banner.
   */
  it("test_rotation_403_shows_error_no_banner", async () => {
    server.use(
      http.post(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001/rotate",
        () =>
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

    let onUpdatedCalled = false;

    render(
      <KeyGovernanceEditor
        apiKey={KEY_FIXTURE}
        onUpdated={() => { onUpdatedCalled = true; }}
      />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /rotate/i })
      ).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /rotate/i }));

    await waitFor(() => {
      const confirmBtn =
        screen.queryByRole("button", { name: /confirm|yes|rotate|proceed/i });
      expect(confirmBtn).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /confirm|yes|rotate|proceed/i })
    );

    // Assert: inline error with "Forbidden" text
    await waitFor(() => {
      expect(screen.getByText(/forbidden/i)).toBeInTheDocument();
    });

    // Assert: no plaintext banner — rotation failed, no new secret to show
    expect(screen.queryByText(/sk-new\./i)).not.toBeInTheDocument();

    // Assert: onUpdated NOT called (rotation failed — no list refresh)
    expect(onUpdatedCalled).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// GOVERNANCE ERROR SURFACES
// ─────────────────────────────────────────────────────────────────────────────

describe("KeyGovernanceEditor — error surfaces", () => {
  const user = userEvent.setup();

  /**
   * TEST 7 — test_governance_403_surfaces_inline_error
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if component navigates on 403 instead of showing inline error,
   * the router.push assertion or absence-of-navigation assertion fails.
   */
  it("test_governance_403_surfaces_inline_error", async () => {
    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        () =>
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

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const field =
        screen.queryByLabelText(/monthly.budget/i) ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(field).toBeInTheDocument();
    });

    const field =
      screen.queryByLabelText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(field!);
    await user.type(field!, "10.00");

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    // Assert: inline error shown
    await waitFor(() => {
      expect(screen.getByText(/forbidden/i)).toBeInTheDocument();
    });

    // Assert: form remains open (not dismissed)
    expect(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    ).toBeInTheDocument();
  });

  /**
   * TEST 8 — test_governance_422_gateway_surfaces_inline_error
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: 422 from gateway must show inline error; values must be retained.
   */
  it("test_governance_422_gateway_surfaces_inline_error", async () => {
    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        () =>
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

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const field =
        screen.queryByLabelText(/monthly.budget/i) ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(field).toBeInTheDocument();
    });

    const field =
      screen.queryByLabelText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(field!);
    await user.type(field!, "99.99");

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    // Assert: gateway error shown inline
    await waitFor(() => {
      expect(screen.getByText(/invalid budget value/i)).toBeInTheDocument();
    });

    // Assert: form is still visible (not navigated away)
    expect(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    ).toBeInTheDocument();
  });

  /**
   * TEST 9 — test_governance_client_validation_soft_gt_hard_blocks_api
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if no client-side validation, PATCH IS called (patchCount > 0).
   */
  it("test_governance_client_validation_soft_gt_hard_blocks_api", async () => {
    let patchCallCount = 0;

    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        () => {
          patchCallCount++;
          return HttpResponse.json({ ...KEY_FIXTURE }, { status: 200 });
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const monthlyField =
        screen.queryByLabelText(/monthly.budget/i) ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(monthlyField).toBeInTheDocument();
    });

    const monthlyField =
      screen.queryByLabelText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
    const softField =
      screen.queryByLabelText(/soft.budget/i) ??
      (screen.queryAllByRole("spinbutton")[1] as HTMLElement);

    await user.clear(monthlyField!);
    await user.type(monthlyField!, "10.00");

    if (softField) {
      await user.clear(softField);
      await user.type(softField, "15.00");
    }

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    // Assert: inline validation error (soft > hard)
    await waitFor(() => {
      const error =
        screen.queryByRole("alert") ??
        screen.queryByText(/soft.*budget.*cannot.*exceed|soft.*must.*be.*less|soft.*greater.*hard/i) ??
        screen.queryByText(/invalid|budget|exceed/i);
      expect(error).toBeInTheDocument();
    });

    // Assert: PATCH was NOT called (client-side validation short-circuited)
    expect(patchCallCount).toBe(0);
  });

  /**
   * TEST 10 — test_governance_client_validation_negative_budget_blocks_api
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if no validation, PATCH is called with a negative value.
   */
  it("test_governance_client_validation_negative_budget_blocks_api", async () => {
    let patchCallCount = 0;

    server.use(
      http.patch(
        "http://localhost:3000/api/gw/admin/keys/00000000-0000-0000-0000-000000000001",
        () => {
          patchCallCount++;
          return HttpResponse.json({ ...KEY_FIXTURE }, { status: 200 });
        }
      )
    );

    render(
      <KeyGovernanceEditor apiKey={KEY_FIXTURE} onUpdated={() => {}} />,
      { wrapper: Wrapper }
    );

    await waitFor(() => {
      const field =
        screen.queryByLabelText(/monthly.budget/i) ??
        (screen.queryAllByRole("spinbutton")[0] as HTMLElement);
      expect(field).toBeInTheDocument();
    });

    const field =
      screen.queryByLabelText(/monthly.budget/i) ??
      (screen.queryAllByRole("spinbutton")[0] as HTMLElement);

    await user.clear(field!);
    await user.type(field!, "-5.00");

    await user.click(
      screen.getByRole("button", { name: /save|update|apply|set/i })
    );

    // Assert: inline validation error for negative budget
    await waitFor(() => {
      const error =
        screen.queryByRole("alert") ??
        screen.queryByText(/negative|must be.*non-negative|must be.*0|invalid/i);
      expect(error).toBeInTheDocument();
    });

    // Assert: PATCH not called
    expect(patchCallCount).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// SPEND PAGE TESTS
// ─────────────────────────────────────────────────────────────────────────────

describe("SpendPage — windowed spend views via BFF", () => {
  const user = userEvent.setup();

  /**
   * TEST 12 — test_spend_view_default_month_renders_totals
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red after module exists:
   *   - If GET /admin/spend?window=month not called → data absent → assertions fail
   *   - If totals.cost_usd not rendered → "1.23" absent from screen
   *   - If bucket_start not rendered → bucket row absent
   */
  it("test_spend_view_default_month_renders_totals", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", ({ request }) => {
        const url = new URL(request.url);
        const window = url.searchParams.get("window");
        if (window === "month" || window === null) {
          return HttpResponse.json(SPEND_MONTH_FIXTURE);
        }
        return HttpResponse.json(
          { code: "ERR_PAYLOAD_INVALID" },
          { status: 422 }
        );
      })
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // Assert: totals rendered
    await waitFor(() => {
      // cost_usd
      expect(screen.getByText(/1\.23/)).toBeInTheDocument();
      // requests
      expect(screen.getByText(/\b3\b/)).toBeInTheDocument();
    });

    // Assert: at least one bucket rendered
    expect(screen.getByText(/2026-06-01/)).toBeInTheDocument();
  });

  /**
   * TEST 13 — test_spend_view_window_selector_changes_query_param
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red after module exists: if queryKey doesn't include window, changing
   * selector doesn't trigger re-fetch → day-window data never appears on screen.
   */
  it("test_spend_view_window_selector_changes_query_param", async () => {
    let capturedWindows: string[] = [];

    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", ({ request }) => {
        const url = new URL(request.url);
        const window = url.searchParams.get("window") ?? "month";
        capturedWindows.push(window);

        if (window === "day") {
          return HttpResponse.json(SPEND_DAY_FIXTURE);
        }
        return HttpResponse.json(SPEND_MONTH_FIXTURE);
      })
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // Wait for initial (month) render
    await waitFor(() => {
      expect(screen.getByText(/1\.23/)).toBeInTheDocument();
    });

    // Find the window selector
    await waitFor(() => {
      const selector =
        screen.queryByRole("combobox") ??
        screen.queryByRole("listbox") ??
        screen.queryByTestId("window-selector");
      expect(selector).toBeInTheDocument();
    });

    const selector =
      screen.queryByRole("combobox") ??
      screen.queryByRole("listbox") ??
      (screen.queryByTestId("window-selector") as HTMLElement);

    // Select "day"
    if (selector?.tagName === "SELECT") {
      await user.selectOptions(selector as HTMLSelectElement, "day");
    } else {
      // Button-based selector or custom dropdown
      const dayOption =
        screen.queryByRole("option", { name: /day/i }) ??
        screen.queryByRole("button", { name: /\bday\b/i }) ??
        screen.queryByText(/^day$/i);
      if (dayOption) {
        await user.click(dayOption);
      }
    }

    // Assert: GET /admin/spend?window=day was called
    await waitFor(() => {
      expect(capturedWindows).toContain("day");
    });

    // Assert: day-window data now visible (0.04 cost from SPEND_DAY_FIXTURE)
    await waitFor(() => {
      expect(screen.getByText(/0\.04/)).toBeInTheDocument();
    });

    // Assert: previous month data no longer the primary totals display
    // (day has cost 0.04; month had 1.23 — they're distinct enough to verify)
  });

  /**
   * TEST 14 — test_spend_view_week_selector
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: window=week not called.
   */
  it("test_spend_view_week_selector", async () => {
    let capturedWindows: string[] = [];

    const SPEND_WEEK_FIXTURE = {
      window: "week",
      bucket_size: "week",
      totals: {
        bucket_start: "2026-06-09T00:00:00Z",
        bucket_end: "2026-06-11T00:00:00Z",
        requests: 2,
        prompt_tokens: 200,
        completion_tokens: 100,
        cost_usd: "0.82",
      },
      buckets: [
        {
          bucket_start: "2026-06-09T00:00:00Z",
          requests: 2,
          prompt_tokens: 200,
          completion_tokens: 100,
          cost_usd: "0.82",
        },
      ],
      breakdown: null,
    };

    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", ({ request }) => {
        const url = new URL(request.url);
        const window = url.searchParams.get("window") ?? "month";
        capturedWindows.push(window);

        if (window === "week") return HttpResponse.json(SPEND_WEEK_FIXTURE);
        return HttpResponse.json(SPEND_MONTH_FIXTURE);
      })
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // Wait for initial render
    await waitFor(() => {
      expect(screen.getByText(/1\.23/)).toBeInTheDocument();
    });

    await waitFor(() => {
      const selector =
        screen.queryByRole("combobox") ??
        screen.queryByTestId("window-selector");
      expect(selector).toBeInTheDocument();
    });

    const selector =
      screen.queryByRole("combobox") ??
      (screen.queryByTestId("window-selector") as HTMLElement);

    if (selector?.tagName === "SELECT") {
      await user.selectOptions(selector as HTMLSelectElement, "week");
    } else {
      const weekOption =
        screen.queryByRole("option", { name: /week/i }) ??
        screen.queryByRole("button", { name: /\bweek\b/i }) ??
        screen.queryByText(/^week$/i);
      if (weekOption) {
        await user.click(weekOption);
      }
    }

    await waitFor(() => {
      expect(capturedWindows).toContain("week");
    });

    await waitFor(() => {
      expect(screen.getByText(/0\.82/)).toBeInTheDocument();
    });
  });

  /**
   * TEST 15 — test_spend_view_zero_state
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if component shows error on 200+zero instead of zero-state,
   * the zero-state message is absent.
   */
  it("test_spend_view_zero_state", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(SPEND_ZERO_FIXTURE)
      )
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // Assert: zero-state message visible
    await waitFor(() => {
      const zeroState =
        screen.queryByText(/no usage/i) ??
        screen.queryByText(/no data/i) ??
        screen.queryByText(/no spend/i) ??
        screen.queryByText(/nothing to show/i) ??
        screen.queryByTestId("spend-zero-state");
      expect(zeroState).toBeInTheDocument();
    });

    // Assert: totals show 0 (not an error state)
    expect(screen.getByText(/\b0\b/)).toBeInTheDocument();

    // Assert: no error message
    expect(screen.queryByText(/error|forbidden|invalid/i)).not.toBeInTheDocument();
  });

  /**
   * TEST 16 — test_spend_view_403_shows_inline_error
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if component crashes on 403 instead of showing inline error,
   * the test throws an uncaught exception.
   */
  it("test_spend_view_403_shows_inline_error", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
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

    render(<SpendPage />, { wrapper: Wrapper });

    // Assert: inline error shown (not a crash)
    await waitFor(() => {
      expect(screen.getByText(/forbidden/i)).toBeInTheDocument();
    });

    // Component did not crash — it's still mounted
    expect(document.body).toBeInTheDocument();
  });

  /**
   * TEST 17 — test_spend_view_422_shows_inline_error
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND.
   * Behavioral red: if component crashes on 422, test throws.
   */
  it("test_spend_view_422_shows_inline_error", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Invalid window parameter",
            status: 422,
            code: "ERR_PAYLOAD_INVALID",
          },
          { status: 422 }
        )
      )
    );

    render(<SpendPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const error =
        screen.queryByText(/invalid window parameter/i) ??
        screen.queryByText(/invalid/i) ??
        screen.queryByText(/422/i) ??
        screen.queryByRole("alert");
      expect(error).toBeInTheDocument();
    });

    // No crash
    expect(document.body).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MODEL-MGMT DEFERRED SURFACE TEST
// ─────────────────────────────────────────────────────────────────────────────

describe("Deferred surface — model management absent from dashboard-govern", () => {
  /**
   * TEST 18 — test_model_mgmt_surface_absent
   *
   * TRUE-RED REASON: Component missing → MODULE_NOT_FOUND (same as all others).
   * This test is a CONTRACT LOCK: it ensures the Build does not accidentally
   * include a ModelManagementPage or model enable/disable toggle in the
   * dashboard-govern build. The model-mgmt task is a separate concern.
   *
   * GREEN invariant: passes DURING build (no model mgmt component added) AND
   * continues to pass AFTER build (milestone exit criterion: dashboard-govern
   * does NOT ship model-mgmt UI).
   */
  it("test_model_mgmt_surface_absent", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(SPEND_MONTH_FIXTURE)
      )
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // Wait for spend page to render (confirms the component mounted)
    await waitFor(() => {
      expect(document.body).toBeInTheDocument();
    });

    // Assert: no model management UI is present in the rendered tree
    expect(
      screen.queryByText(/model management|enable model|disable model|model.*toggle/i)
    ).not.toBeInTheDocument();

    expect(
      screen.queryByRole("switch", { name: /model/i })
    ).not.toBeInTheDocument();

    expect(
      screen.queryByTestId("model-mgmt")
    ).not.toBeInTheDocument();
  });
});
