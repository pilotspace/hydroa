/**
 * PresetsPage scenarios (preset-admin-surface TASK.md §4 TESTS, frontend bullet)
 *
 * All tests fail at module resolution — @/components/presets/PresetsPage does
 * not exist yet.  msw handlers per-test override the defaults (onUnhandledRequest:
 * "error" in tests/setup.ts, so every render that fires a GET must be given one).
 *
 * Wrapper provides the TanStack Query client that PresetsPage will need.
 * Mirrors tests/keys.test.tsx's structure exactly, adapted for the presets
 * domain shape (preset_name/alias_key/target_model/updated_at, path-templated
 * PUT/DELETE — TASK.md §3 CONTRACT v2, not the rejected v1 JSON-body draft).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { PresetsPage } from "@/components/presets/PresetsPage";

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Builds a fresh QueryClient for each test (no cross-test cache pollution).
 * retry: false so failed queries surface immediately without retries in tests.
 */
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

function renderPresets() {
  return render(<PresetsPage />, { wrapper: Wrapper });
}

// ─────────────────────────────────────────────────────────────────────────────

describe("PresetsPage", () => {
  const user = userEvent.setup();

  /**
   * TEST — test_presets_empty_state
   * Scenario: presets list empty state (GET /admin/presets -> { presets: [] })
   */
  it("test_presets_empty_state", async () => {
    // Arrange
    server.use(
      http.get("http://localhost:3000/api/gw/admin/presets", () =>
        HttpResponse.json({ presets: [] })
      )
    );

    // Act
    renderPresets();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/no presets yet/i)).toBeInTheDocument();
    });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST — test_create_preset_form_submission_adds_row_without_reload
   * Scenario: dashboard page renders and manages presets end to end (create half)
   * Submitting the create form calls bffPut with the path-templated URL
   * (/admin/presets/{preset_name}/{alias_key}, body {target_model}) and the new
   * row appears in the table via query invalidation — no page reload/navigation.
   */
  it("test_create_preset_form_submission_adds_row_without_reload", async () => {
    // Arrange
    let getCallCount = 0;
    let putCallCount = 0;
    let putUrl: string | null = null;
    let putBody: unknown = null;
    server.use(
      http.get("http://localhost:3000/api/gw/admin/presets", () => {
        getCallCount++;
        if (getCallCount === 1) {
          return HttpResponse.json({ presets: [] });
        }
        return HttpResponse.json({
          presets: [
            {
              preset_name: "cheap",
              alias_key: "opus",
              target_model: "gpt5-5-mini",
              updated_at: "2026-07-01T00:00:00Z",
            },
          ],
        });
      }),
      http.put(
        "http://localhost:3000/api/gw/admin/presets/cheap/opus",
        async ({ request }) => {
          putCallCount++;
          putUrl = request.url;
          putBody = await request.json();
          return HttpResponse.json({
            preset_name: "cheap",
            alias_key: "opus",
            target_model: "gpt5-5-mini",
            updated_at: "2026-07-01T00:00:00Z",
          });
        }
      )
    );

    // Act
    renderPresets();

    await waitFor(() => {
      expect(screen.getByText(/no presets yet/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/preset name/i), "cheap");
    await user.type(screen.getByLabelText(/alias key/i), "opus");
    await user.type(screen.getByLabelText(/target model/i), "gpt5-5-mini");
    await user.click(screen.getByRole("button", { name: /add preset/i }));

    // Assert: PUT was called exactly once, with the path-templated URL and
    // a {target_model} body — NOT a JSON body carrying preset_name/alias_key
    // (that was the rejected v1 draft).
    await waitFor(() => {
      expect(putCallCount).toBe(1);
    });
    expect(putUrl).toBe("http://localhost:3000/api/gw/admin/presets/cheap/opus");
    expect(putBody).toEqual({ target_model: "gpt5-5-mini" });

    // Assert: the new row appears without a full page reload (query invalidation).
    await waitFor(() => {
      expect(screen.getByText("cheap")).toBeInTheDocument();
      expect(screen.getByText("opus")).toBeInTheDocument();
      expect(screen.getByText("gpt5-5-mini")).toBeInTheDocument();
    });
  });

  /**
   * TEST — test_delete_preset_removes_row
   * Scenario: dashboard page renders and manages presets end to end (delete half)
   * Clicking delete on a row calls bffDelete with the path-templated URL (no
   * body) and removes the row via query invalidation.
   */
  it("test_delete_preset_removes_row", async () => {
    // Arrange
    let getCallCount = 0;
    let deleteCallCount = 0;
    let deleteUrl: string | null = null;
    server.use(
      http.get("http://localhost:3000/api/gw/admin/presets", () => {
        getCallCount++;
        if (getCallCount === 1) {
          return HttpResponse.json({
            presets: [
              {
                preset_name: "cheap",
                alias_key: "opus",
                target_model: "gpt5-5-mini",
                updated_at: "2026-07-01T00:00:00Z",
              },
            ],
          });
        }
        return HttpResponse.json({ presets: [] });
      }),
      http.delete(
        "http://localhost:3000/api/gw/admin/presets/cheap/opus",
        ({ request }) => {
          deleteCallCount++;
          deleteUrl = request.url;
          return new HttpResponse(null, { status: 204 });
        }
      )
    );

    // Act
    renderPresets();

    await waitFor(() => {
      expect(screen.getByText("cheap")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /delete/i }));

    // Assert: DELETE called exactly once with the path-templated URL, no body
    await waitFor(() => {
      expect(deleteCallCount).toBe(1);
    });
    expect(deleteUrl).toBe(
      "http://localhost:3000/api/gw/admin/presets/cheap/opus"
    );

    // Assert: row removed via query invalidation, empty state shown
    await waitFor(() => {
      expect(screen.queryByText("cheap")).not.toBeInTheDocument();
      expect(screen.getByText(/no presets yet/i)).toBeInTheDocument();
    });
  });

  /**
   * TEST — test_reject_slash_client_side_before_network_call
   * Scenario: client-side "/" rejection mirrors the server-side router-local
   * guard (TASK.md §1 ⚠ / §3 CONTRACT _reject_slash) — a cheap UX win. A "/"
   * in preset_name or alias_key must be rejected before bffPut is ever called.
   */
  it("test_reject_slash_client_side_before_network_call", async () => {
    // Arrange
    let getCallCount = 0;
    let putCallCount = 0;
    server.use(
      http.get("http://localhost:3000/api/gw/admin/presets", () => {
        getCallCount++;
        return HttpResponse.json({ presets: [] });
      }),
      http.put("http://localhost:3000/api/gw/admin/presets/*", () => {
        putCallCount++;
        return HttpResponse.json(
          {
            preset_name: "team/cheap",
            alias_key: "opus",
            target_model: "gpt5-5",
            updated_at: "2026-07-01T00:00:00Z",
          },
          { status: 200 }
        );
      })
    );

    // Act
    renderPresets();

    await waitFor(() => {
      expect(screen.getByText(/no presets yet/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/preset name/i), "team/cheap");
    await user.type(screen.getByLabelText(/alias key/i), "opus");
    await user.type(screen.getByLabelText(/target model/i), "gpt5-5");
    await user.click(screen.getByRole("button", { name: /add preset/i }));

    // Assert: a validation error is shown and PUT is NEVER called
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/\//);
    });
    expect(putCallCount).toBe(0);
    // The empty state is still present — no row was optimistically added
    expect(screen.getByText(/no presets yet/i)).toBeInTheDocument();
  });
});
