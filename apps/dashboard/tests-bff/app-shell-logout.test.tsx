/**
 * tests-bff/app-shell-logout.test.tsx — RED for global logout in the sidebar chrome.
 *
 * Logout used to live only on KeysPage. It now lives in the shared AppShell footer so
 * every authenticated surface can sign out. Contract: clicking it POSTs to
 * /api/auth/logout (via the bff-client) then navigates to /login.
 *
 * AppShell navigates with window.location.assign (NOT next/navigation) so it stays
 * decoupled from how each test mocks the router — here we stub assign to observe it.
 *
 * RED before build: AppShell has no logout control → getByRole(button, /log out/) throws.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { AppShell } from "@/components/ui/app-shell";

const APP = "http://localhost:3000";

describe("AppShell — global logout in the sidebar chrome", () => {
  let originalLocation: Location;
  let assignMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalLocation = window.location;
    assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: assignMock,
        href: "http://localhost:3000/app",
        origin: "http://localhost:3000",
        pathname: "/app",
      },
    });
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  it("test_logout_posts_to_api_auth_logout_then_navigates_to_login", async () => {
    let logoutCalled = false;
    server.use(
      http.post(`${APP}/api/auth/logout`, () => {
        logoutCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );

    const user = userEvent.setup();
    render(
      <AppShell role="owner" userEmail="ada@hydroa.io">
        <h1>Body</h1>
      </AppShell>,
    );

    await user.click(screen.getByRole("button", { name: /log ?out/i }));

    await waitFor(() => expect(logoutCalled).toBe(true));
    expect(assignMock).toHaveBeenCalledWith("/login");
  });
});
