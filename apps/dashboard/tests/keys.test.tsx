/**
 * Tests 7–14: KeysPage scenarios
 *
 * All 8 tests fail at module resolution — @/components/keys/KeysPage does not
 * exist yet.  msw handlers per-test override the defaults.
 *
 * Wrapper provides the TanStack Query client that KeysPage will need.
 * (QueryClient is imported from @tanstack/react-query which IS installed.)
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import { VALID_JWT } from "./mocks/handlers";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { KeysPage } from "@/components/keys/KeysPage";

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

function renderKeys() {
  return render(<KeysPage />, { wrapper: Wrapper });
}

/** A JWT with exp = past-60s, for expired-token tests */
function makeExpiredJwt(): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  const payload = btoa(
    JSON.stringify({ sub: "u-1", exp: Math.floor(Date.now() / 1000) - 60 })
  ).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${header}.${payload}.fakesig`;
}

// ─────────────────────────────────────────────────────────────────────────────

describe("KeysPage", () => {
  const user = userEvent.setup();

  /**
   * TEST 7 — test_keys_list_renders_rows
   * Scenario: keys list renders rows from GET /admin/keys
   */
  it("test_keys_list_renders_rows", async () => {
    // Arrange
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    server.use(
      http.get("http://gateway.test/admin/keys", () =>
        HttpResponse.json([
          {
            key_id: "kid-abc",
            name: "prod-key",
            prefix: "sk-1a2b3c",
            created_at: "2026-01-01T00:00:00Z",
            revoked_at: null,
          },
        ])
      )
    );

    // Act
    renderKeys();

    // Assert
    await waitFor(() => {
      expect(screen.getByText("prod-key")).toBeInTheDocument();
      expect(screen.getByText("sk-1a2b3c")).toBeInTheDocument();
    });
    // No raw secret in the DOM
    expect(screen.queryByText(/\bSECRET\b/i)).not.toBeInTheDocument();
  });

  /**
   * TEST 8 — test_keys_empty_state
   * Scenario: keys list empty state
   */
  it("test_keys_empty_state", async () => {
    // Arrange
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    server.use(
      http.get("http://gateway.test/admin/keys", () => HttpResponse.json([]))
    );

    // Act
    renderKeys();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });
    // No key rows
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST 9 — test_keys_error_state
   * Scenario: keys list error state (500 from gateway)
   */
  it("test_keys_error_state", async () => {
    // Arrange
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    server.use(
      http.get("http://gateway.test/admin/keys", () =>
        HttpResponse.json(
          { title: "Internal server error", status: 500 },
          { status: 500 }
        )
      )
    );

    // Act
    renderKeys();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
    });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST 10 — test_keys_loading_state
   * Scenario: keys list loading state (GET /admin/keys never resolves during
   * the test window)
   */
  it("test_keys_loading_state", async () => {
    // Arrange: handler returns a promise that never settles in this test
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    server.use(
      http.get(
        "http://gateway.test/admin/keys",
        () => new Promise<never>(() => {/* intentionally never resolves */})
      )
    );

    // Act
    renderKeys();

    // Assert immediately (before any response)
    // Loading indicator visible (skeleton or spinner — any ARIA role or text)
    await waitFor(() => {
      const spinner =
        screen.queryByRole("status") ??
        screen.queryByTestId("loading") ??
        screen.queryByText(/loading/i) ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
  });

  /**
   * TEST 11 — test_create_key_shows_plaintext_once_not_in_list
   * Scenario: create key — shows plaintext once, not in list afterwards
   */
  it("test_create_key_shows_plaintext_once_not_in_list", async () => {
    // Arrange
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    let getCallCount = 0;
    server.use(
      http.get("http://gateway.test/admin/keys", () => {
        getCallCount++;
        if (getCallCount === 1) {
          return HttpResponse.json([]);
        }
        // Second call (after create) returns the list WITHOUT the secret
        return HttpResponse.json([
          {
            key_id: "kid-ci",
            name: "ci-key",
            prefix: "sk-abc123",
            created_at: "2026-01-01T00:00:00Z",
            revoked_at: null,
          },
        ]);
      }),
      http.post("http://gateway.test/admin/keys", () =>
        HttpResponse.json(
          {
            key_id: "kid-ci",
            name: "ci-key",
            key: "sk-abc123.SECRETVALUE",
          },
          { status: 201 }
        )
      )
    );

    // Act
    renderKeys();

    // Open Create key dialog
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /create key/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /create key/i }));

    // Fill dialog
    await waitFor(() => {
      expect(screen.getByLabelText(/key name/i)).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText(/key name/i), "ci-key");
    await user.click(screen.getByRole("button", { name: /create/i }));

    // Assert: plaintext appears in banner
    await waitFor(() => {
      expect(screen.getByText("sk-abc123.SECRETVALUE")).toBeInTheDocument();
    });
    // Copy button present
    expect(
      screen.getByRole("button", { name: /copy/i })
    ).toBeInTheDocument();

    // Dismiss banner
    await user.click(screen.getByRole("button", { name: /dismiss|close|done|got it/i }));

    // Assert: secret is gone from DOM; list shows ci-key but not secret
    await waitFor(() => {
      expect(screen.queryByText(/SECRETVALUE/)).not.toBeInTheDocument();
    });
    expect(screen.getByText("ci-key")).toBeInTheDocument();
  });

  /**
   * TEST 12 — test_revoke_key_removes_row
   * Scenario: revoke key — removes from active list
   */
  it("test_revoke_key_removes_row", async () => {
    // Arrange
    localStorage.setItem("ai_proxy_token", VALID_JWT);
    let deleteCallCount = 0;
    let getCallCount = 0;
    server.use(
      http.get("http://gateway.test/admin/keys", () => {
        getCallCount++;
        if (getCallCount === 1) {
          return HttpResponse.json([
            {
              key_id: "kid-1",
              name: "k1",
              prefix: "sk-k1",
              created_at: "2026-01-01T00:00:00Z",
              revoked_at: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            key_id: "kid-1",
            name: "k1",
            prefix: "sk-k1",
            created_at: "2026-01-01T00:00:00Z",
            revoked_at: "2026-06-10T00:00:00Z",
          },
        ]);
      }),
      http.delete("http://gateway.test/admin/keys/kid-1", () => {
        deleteCallCount++;
        return new HttpResponse(null, { status: 204 });
      })
    );

    // Act
    renderKeys();

    // Wait for row to appear
    await waitFor(() => {
      expect(screen.getByText("k1")).toBeInTheDocument();
    });

    // Click Revoke
    await user.click(screen.getByRole("button", { name: /revoke/i }));

    // Confirm dialog
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /confirm|yes|revoke/i })
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /confirm|yes/i }));

    // Assert DELETE was called exactly once
    await waitFor(() => {
      expect(deleteCallCount).toBe(1);
    });

    // Row now shows revoked state (revoked_at non-null rendered visibly)
    await waitFor(() => {
      // Either the date appears, or a "revoked" status badge
      const revokedText =
        screen.queryByText(/2026-06-10/i) ??
        screen.queryByText(/revoked/i);
      expect(revokedText).toBeInTheDocument();
    });
  });

  /**
   * TEST 13 — test_unauthenticated_keys_redirects_login
   * Scenario: unauthenticated access to /keys redirects to /login
   */
  it("test_unauthenticated_keys_redirects_login", async () => {
    // Arrange: NO token in localStorage (already cleared by afterEach setup)
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();

    // Act
    renderKeys();

    // Assert: redirect fired, content not rendered
    await waitFor(() => {
      const router = getRouterMock();
      const redirectMock =
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        (require("next/navigation") as { redirect: ReturnType<typeof vi.fn> }).redirect;
      // Either router.push OR redirect() should point to /login
      const navigatedToLogin =
        router.push.mock.calls.some((c: string[]) => c[0] === "/login") ||
        redirectMock.mock.calls.some((c: string[]) => c[0] === "/login");
      expect(navigatedToLogin).toBe(true);
    });
    // The authenticated content should not appear
    expect(screen.queryByRole("button", { name: /create key/i })).not.toBeInTheDocument();
  });

  /**
   * TEST 14 — test_expired_token_keys_redirects_login
   * Scenario: expired token on /keys redirects to /login
   */
  it("test_expired_token_keys_redirects_login", async () => {
    // Arrange: place an expired JWT in localStorage
    localStorage.setItem("ai_proxy_token", makeExpiredJwt());

    // Act
    renderKeys();

    // Assert: redirect to /login
    await waitFor(() => {
      const router = getRouterMock();
      const redirectMock =
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        (require("next/navigation") as { redirect: ReturnType<typeof vi.fn> }).redirect;
      const navigatedToLogin =
        router.push.mock.calls.some((c: string[]) => c[0] === "/login") ||
        redirectMock.mock.calls.some((c: string[]) => c[0] === "/login");
      expect(navigatedToLogin).toBe(true);
    });
  });
});
