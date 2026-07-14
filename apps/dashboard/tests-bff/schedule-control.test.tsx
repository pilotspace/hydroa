/**
 * tests-bff/schedule-control.test.tsx — RED/GREEN suite for
 * components/compliance/ScheduleControl.tsx (compliance-report-center TASK.md §3
 * CONTRACT — FROZEN @ v1, M19/M22/M23/R9/R10).
 *
 * GET /admin/compliance/report-schedule succeeds for any AUDIT_READ role
 * (read-only for a non-owner); PUT is OWNER-only. The default `/api/auth/me`
 * mock handler (tests-bff/mocks/handlers.ts) resolves role "owner" — non-owner
 * scenarios override it per-test to "member" (mirrors
 * tests-bff/nav-role-filter.test.tsx's own role-override precedent).
 *
 * RED failure mode: import from @/components/compliance/ScheduleControl fails
 * with MODULE_NOT_FOUND until Build writes the component.
 */

import { describe, it, expect, beforeAll, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { ScheduleControl } from "@/components/compliance/ScheduleControl";
import { expectNoSeriousViolations } from "@/test-support/axe";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

// jsdom lacks the pointer/scroll/observer APIs Radix Select uses when opening —
// mirrors tests/design-system/primitives.test.tsx's own established precedent.
beforeAll(() => {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = vi.fn(() => false);
  }
  Element.prototype.scrollIntoView = vi.fn();
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const SCHEDULE_DISABLED = {
  enabled: false,
  cadence: "monthly",
  day_of_month: 1,
  window_policy: "previous_calendar_month",
  created_by: null as string | null,
  created_at: null as string | null,
  updated_at: null as string | null,
  last_run_at: null as string | null,
  last_run_status: null as string | null,
  next_run_at: null as string | null,
};

const SCHEDULE_ENABLED = {
  ...SCHEDULE_DISABLED,
  enabled: true,
  day_of_month: 15,
  created_by: "00000000-0000-0000-0000-000000000001",
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  last_run_at: "2026-06-15T00:00:00Z",
  last_run_status: "success",
  next_run_at: "2026-07-15T00:00:00Z",
};

const scheduleGet = (body: unknown = SCHEDULE_DISABLED) =>
  http.get(`${APP}/api/gw/admin/compliance/report-schedule`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]),
  );

function asMember() {
  server.use(
    http.get(`${APP}/api/auth/me`, () =>
      HttpResponse.json({
        user_id: "u-2",
        tenant_id: "t-1",
        email: "bob@acme.io",
        role: "member",
        exp: Math.floor(Date.now() / 1000) + 86400,
      }),
    ),
  );
}

describe("ScheduleControl — read state", () => {
  it("test_owner_sees_enabled_controls_and_current_state", async () => {
    server.use(scheduleGet(SCHEDULE_ENABLED));
    render(<ScheduleControl />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("switch", { name: /enable scheduled generation/i });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).not.toBeDisabled();

    const daySelect = screen.getByRole("combobox", { name: /day of month/i });
    expect(within(daySelect).getByText("15")).toBeInTheDocument();

    expect(screen.getByText(/generated successfully/i)).toBeInTheDocument();
    expect(screen.queryByText(/only the tenant owner can change this/i)).not.toBeInTheDocument();
  });

  it("test_non_owner_read_only_disabled_controls_with_copy", async () => {
    asMember();
    server.use(scheduleGet(SCHEDULE_ENABLED));
    render(<ScheduleControl />, { wrapper: Wrapper });

    // M6: a non-owner can always see what's configured — never a blank/hidden control.
    const toggle = await screen.findByRole("switch", { name: /enable scheduled generation/i });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toBeDisabled();
    expect(screen.getByRole("combobox", { name: /day of month/i })).toBeDisabled();
    expect(screen.getByText(/only the tenant owner can change this/i)).toBeInTheDocument();
  });

  it("test_schedule_get_error_shows_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/report-schedule`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    render(<ScheduleControl />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("test_loading_state_renders_status_then_resolves", async () => {
    server.use(scheduleGet(SCHEDULE_DISABLED));
    render(<ScheduleControl />, { wrapper: Wrapper });
    expect(screen.getByRole("status")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: /enable scheduled generation/i }),
      ).toBeInTheDocument(),
    );
  });
});

describe("ScheduleControl — owner writes", () => {
  it("test_owner_toggle_enable_fires_put_and_updates_ui", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      scheduleGet(SCHEDULE_DISABLED),
      http.put(`${APP}/api/gw/admin/compliance/report-schedule`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...SCHEDULE_DISABLED, enabled: true });
      }),
    );
    render(<ScheduleControl />, { wrapper: Wrapper });
    const toggle = await screen.findByRole("switch", { name: /enable scheduled generation/i });

    await user.click(toggle);

    await waitFor(() => expect(putBody).toEqual({ enabled: true, day_of_month: 1 }));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
  });

  it("test_owner_change_day_of_month_when_enabled_fires_put", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      scheduleGet(SCHEDULE_ENABLED),
      http.put(`${APP}/api/gw/admin/compliance/report-schedule`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...SCHEDULE_ENABLED, day_of_month: 20 });
      }),
    );
    render(<ScheduleControl />, { wrapper: Wrapper });
    const daySelect = await screen.findByRole("combobox", { name: /day of month/i });

    await user.click(daySelect);
    const listbox = await screen.findByRole("listbox");
    await user.click(within(listbox).getByText("20"));

    await waitFor(() => expect(putBody).toEqual({ enabled: true, day_of_month: 20 }));
  });

  it("test_change_day_of_month_while_disabled_does_not_fire_put", async () => {
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      scheduleGet(SCHEDULE_DISABLED),
      http.put(`${APP}/api/gw/admin/compliance/report-schedule`, () => {
        putCalled = true;
        return HttpResponse.json(SCHEDULE_DISABLED);
      }),
    );
    render(<ScheduleControl />, { wrapper: Wrapper });
    const daySelect = await screen.findByRole("combobox", { name: /day of month/i });

    await user.click(daySelect);
    const listbox = await screen.findByRole("listbox");
    await user.click(within(listbox).getByText("5"));

    // dayOfMonth is only persisted server-side while the schedule is enabled.
    expect(putCalled).toBe(false);
  });

  it("test_put_error_reverts_to_last_known_good_state", async () => {
    const user = userEvent.setup();
    server.use(
      scheduleGet(SCHEDULE_DISABLED),
      http.put(`${APP}/api/gw/admin/compliance/report-schedule`, () =>
        problem("Owner role required", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<ScheduleControl />, { wrapper: Wrapper });
    const toggle = await screen.findByRole("switch", { name: /enable scheduled generation/i });

    await user.click(toggle);

    // R10: reverts to the last-known-good server state — never leaves an
    // unconfirmed value shown.
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("ScheduleControl — accessibility", () => {
  it("test_axe_owner_enabled_state", async () => {
    server.use(scheduleGet(SCHEDULE_ENABLED));
    const { container } = render(<ScheduleControl />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /enable scheduled generation/i });
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_non_owner_disabled_state", async () => {
    asMember();
    server.use(scheduleGet(SCHEDULE_ENABLED));
    const { container } = render(<ScheduleControl />, { wrapper: Wrapper });
    await screen.findByText(/only the tenant owner can change this/i);
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/report-schedule`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    const { container } = render(<ScheduleControl />, { wrapper: Wrapper });
    await screen.findByRole("alert");
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });
});
