/**
 * tests/accept-invite-page.test.tsx — RED suite for the public invite-accept form,
 * components/auth/AcceptInviteForm.tsx (rendered by app/(auth)/invite/[token]/page.tsx).
 *
 * Behavior:
 *   1. On mount, GET /api/auth/invite/{token} (the public preview BFF route) and
 *      render {tenant_name, role, email}.
 *   2. 404/409/410 preview responses render a SPECIFIC, non-500 message (not-found /
 *      already-used / expired) — never a generic/raw gateway error.
 *   3. Submitting a password POSTs /api/auth/invite/{token} {password}; success
 *      navigates to /app (the BFF has already set the session cookie — no
 *      localStorage write, no token in this component at all).
 *   4. A non-2xx accept response surfaces its friendly message inline; no navigation.
 *
 * RED before Build: @/components/auth/AcceptInviteForm does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention (mirrors tests/signup.test.tsx).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { AcceptInviteForm } from "@/components/auth/AcceptInviteForm";

const APP = "http://localhost:3000";

describe("AcceptInviteForm", () => {
  const user = userEvent.setup();

  it("test_accept_invite_renders_preview", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-good`, () =>
        HttpResponse.json({
          tenant_name: "Acme Corp",
          email: "newbie@acme.io",
          role: "member",
          expires_at: "2026-07-22T00:00:00Z",
        }),
      ),
    );

    render(<AcceptInviteForm token="tok-good" />);

    await waitFor(() => {
      expect(screen.getByText(/acme corp/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/newbie@acme\.io/i)).toBeInTheDocument();
  });

  it("test_accept_invite_404_shows_not_found_message", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-missing`, () =>
        HttpResponse.json(
          { title: "Not found", status: 404, code: "ERR_INVITE_NOT_FOUND" },
          { status: 404 },
        ),
      ),
    );

    render(<AcceptInviteForm token="tok-missing" />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/invalid/i)).toBeInTheDocument();
  });

  it("test_accept_invite_409_shows_already_used_message", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-used`, () =>
        HttpResponse.json(
          { title: "Conflict", status: 409, code: "ERR_INVITE_NOT_PENDING" },
          { status: 409 },
        ),
      ),
    );

    render(<AcceptInviteForm token="tok-used" />);

    await waitFor(() => {
      expect(screen.getByText(/already/i)).toBeInTheDocument();
    });
  });

  it("test_accept_invite_410_shows_expired_message", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-expired`, () =>
        HttpResponse.json(
          { title: "Gone", status: 410, code: "ERR_INVITE_EXPIRED" },
          { status: 410 },
        ),
      ),
    );

    render(<AcceptInviteForm token="tok-expired" />);

    await waitFor(() => {
      expect(screen.getByText(/expired/i)).toBeInTheDocument();
    });
  });

  it("test_accept_invite_submit_success_navigates_to_app", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-submit`, () =>
        HttpResponse.json({
          tenant_name: "Acme Corp",
          email: "newbie@acme.io",
          role: "member",
          expires_at: "2026-07-22T00:00:00Z",
        }),
      ),
      http.post(`${APP}/api/auth/invite/tok-submit`, () =>
        HttpResponse.json({ ok: true }, { status: 201 }),
      ),
    );

    render(<AcceptInviteForm token="tok-submit" />);

    await waitFor(() => {
      expect(screen.getByText(/acme corp/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /accept invite/i }));

    await waitFor(() => {
      const router = getRouterMock();
      expect(router.push).toHaveBeenCalledWith("/app");
    });
  });

  it("test_accept_invite_submit_error_inline_no_navigation", async () => {
    server.use(
      http.get(`${APP}/api/auth/invite/tok-fail`, () =>
        HttpResponse.json({
          tenant_name: "Acme Corp",
          email: "newbie@acme.io",
          role: "member",
          expires_at: "2026-07-22T00:00:00Z",
        }),
      ),
      http.post(`${APP}/api/auth/invite/tok-fail`, () =>
        HttpResponse.json(
          { title: "Invite expired", status: 410, code: "ERR_INVITE_EXPIRED" },
          { status: 410 },
        ),
      ),
    );

    render(<AcceptInviteForm token="tok-fail" />);

    await waitFor(() => {
      expect(screen.getByText(/acme corp/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /accept invite/i }));

    await waitFor(() => {
      expect(screen.getByText(/expired/i)).toBeInTheDocument();
    });
    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/app");
  });

  it("test_accept_invite_weak_password_no_api_call", async () => {
    let postCalled = false;
    server.use(
      http.get(`${APP}/api/auth/invite/tok-weak`, () =>
        HttpResponse.json({
          tenant_name: "Acme Corp",
          email: "newbie@acme.io",
          role: "member",
          expires_at: "2026-07-22T00:00:00Z",
        }),
      ),
      http.post(`${APP}/api/auth/invite/tok-weak`, () => {
        postCalled = true;
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );

    render(<AcceptInviteForm token="tok-weak" />);

    await waitFor(() => {
      expect(screen.getByText(/acme corp/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/password/i), "short1234"); // 9 chars, min is 10
    await user.click(screen.getByRole("button", { name: /accept invite/i }));

    await waitFor(() => {
      expect(screen.getByText(/at least 10 characters/i)).toBeInTheDocument();
    });
    expect(postCalled).toBe(false);
  });
});
