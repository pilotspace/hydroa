/**
 * tests-bff/use-current-user.test.tsx
 *
 * Tests for lib/hooks/use-current-user.ts — the hook that fetches role/claims
 * from GET /api/auth/me instead of decoding the JWT client-side.
 *
 * RED failure mode: import from @/lib/hooks/use-current-user fails with
 * MODULE_NOT_FOUND.
 *
 * Test 18 (useCurrentUser scenario from §4 test plan):
 *   test_bff_use_current_user_returns_role
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── RED: this import fails until Build creates lib/hooks/use-current-user.ts ──
import { useCurrentUser } from "@/lib/hooks/use-current-user";

// ─────────────────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

/** Test component that renders the role returned by useCurrentUser */
function UserRoleDisplay() {
  const { data, isLoading, isError } = useCurrentUser();
  if (isLoading) return <div data-testid="loading">loading</div>;
  if (isError || !data) return <div data-testid="error">error</div>;
  return (
    <div>
      <span data-testid="role">{data.role}</span>
      <span data-testid="email">{data.email}</span>
    </div>
  );
}

describe("useCurrentUser", () => {
  /**
   * TEST 18 — test_bff_use_current_user_returns_role
   * Scenario: useCurrentUser — returns role from /api/auth/me for UI affordance
   */
  it("test_bff_use_current_user_returns_role", async () => {
    // Arrange: /api/auth/me returns owner role
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "00000000-0000-0000-0000-000000000001",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "ada@acme.io",
          role: "owner",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      )
    );

    // Act
    render(<UserRoleDisplay />, { wrapper: Wrapper });

    // Assert: role is "owner" — from the API response, not from localStorage
    await waitFor(() => {
      expect(screen.getByTestId("role")).toHaveTextContent("owner");
    });
    expect(screen.getByTestId("email")).toHaveTextContent("ada@acme.io");

    // No localStorage read for the JWT token
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  it("test_bff_use_current_user_member_role", async () => {
    // Arrange: /api/auth/me returns member role
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({
          user_id: "00000000-0000-0000-0000-000000000002",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "bob@acme.io",
          role: "member",
          exp: Math.floor(Date.now() / 1000) + 86400,
        })
      )
    );

    render(<UserRoleDisplay />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("role")).toHaveTextContent("member");
    });
    // Confirm no raw JWT in the DOM
    expect(screen.queryByText(/fakesignature/)).not.toBeInTheDocument();
  });

  it("test_bff_use_current_user_401_returns_null", async () => {
    // Arrange: /api/auth/me returns 401 (no session)
    server.use(
      http.get("http://localhost:3000/api/auth/me", () =>
        HttpResponse.json({ code: "ERR_AUTH_NO_SESSION" }, { status: 401 })
      )
    );

    render(<UserRoleDisplay />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeInTheDocument();
    });
  });
});
