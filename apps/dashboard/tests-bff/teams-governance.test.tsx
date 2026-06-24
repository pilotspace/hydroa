/**
 * tests-bff/teams-governance.test.tsx — RED suite for v15 task teams-governance-ui.
 *
 * Surface: a NEW owner/admin `/teams` page (master-detail on one page) that manages
 * teams + members through the EXISTING (frozen) teams gateway contract, consumed via
 * the BFF seam (bffGet/bffPost/bffPatch/bffDelete). The BFF passes gateway JSON
 * through VERBATIM — GET /admin/teams is a BARE array (no {object,data} envelope).
 *
 *   GET    /admin/teams                         -> TeamResponse[]
 *   POST   /admin/teams            {name}       -> 201 TeamResponse | 409
 *   PATCH  /admin/teams/{id} {team_budget_usd}  -> 200 TeamResponse  | 404 | 422
 *   DELETE /admin/teams/{id}                    -> 204
 *   GET    /admin/teams/{id}                    -> 200 TeamDetailResponse
 *   POST   /admin/teams/{id}/members {email,role} -> 201 | 404 | 409 | 422
 *   DELETE /admin/teams/{id}/members/{user_id}  -> 204
 *
 * Member-add is BY EMAIL (the Tin-approved CR landed in teams-add-by-email).
 * team_budget_usd is a decimal-as-STRING, nullable ("" budget cell shows "—").
 *
 * RED before build: `@/components/teams/TeamsPage` does not exist, so the whole file
 * fails at collect with MODULE_NOT_FOUND — the established true-red convention. Once
 * TeamsPage exists, remaining failures are BEHAVIORAL.
 *
 * Runs in the "bff" vitest project (msw same-origin handlers, server.use overrides).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse, delay } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/teams/TeamsPage.tsx ──────────
import { TeamsPage } from "@/components/teams/TeamsPage";
// AppShell already exists (v13); the nav assertion is RED until /teams is added.
import { AppShell } from "@/components/ui";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
}

const TEAM_A = {
  id: "team-a",
  name: "platform",
  tenant_id: "t1",
  created_at: "2026-01-01T00:00:00Z",
  member_count: 2,
  key_count: 1,
  team_budget_usd: "100.00",
};
const TEAM_B = {
  id: "team-b",
  name: "research",
  tenant_id: "t1",
  created_at: "2026-01-02T00:00:00Z",
  member_count: 0,
  key_count: 0,
  team_budget_usd: null,
};
const TEAM_A_DETAIL = {
  ...TEAM_A,
  members: [
    { user_id: "user-1", role: "lead", added_at: "2026-01-03T00:00:00Z" },
    { user_id: "user-2", role: "member", added_at: "2026-01-04T00:00:00Z" },
  ],
};

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

// ── list + four state patterns ──────────────────────────────────────────────────
describe("TeamsPage — list + four states", () => {
  it("test_list_renders_rows", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A, TEAM_B])),
    );
    render(<TeamsPage />, { wrapper: Wrapper });

    expect(await screen.findByText("platform")).toBeInTheDocument();
    expect(screen.getByText("research")).toBeInTheDocument();
    // budgeted team shows the decimal-string; null-budget team shows the em-dash
    expect(screen.getByText("100.00")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("test_empty_shows_empty_state", async () => {
    server.use(http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([])));
    render(<TeamsPage />, { wrapper: Wrapper });

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText(/no teams yet/i)).toBeInTheDocument();
  });

  it("test_list_error_shows_alert", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("platform")).not.toBeInTheDocument();
  });

  it("test_member_forbidden_shows_alert", async () => {
    // owner/admin-only: the gateway 403s a member on the list GET; surfaced as the
    // standard ErrorState (no redundant client useCurrentUser gate — model-mgmt rule).
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () =>
        problem("Forbidden", 403, "ERR_FORBIDDEN"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/forbidden/i);
  });
});

// ── create team ──────────────────────────────────────────────────────────────────
describe("TeamsPage — create team", () => {
  it("test_create_team_success", async () => {
    const user = userEvent.setup();
    let getCount = 0;
    let postBody: Record<string, unknown> | null = null;

    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => {
        getCount += 1;
        return HttpResponse.json([TEAM_A]);
      }),
      http.post(`${APP}/api/gw/admin/teams`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TEAM_B, name: "newteam" }, { status: 201 });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    await user.click(screen.getByRole("button", { name: /create team/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/team name/i), "newteam");
    await user.click(within(dialog).getByRole("button", { name: /create/i }));

    await waitFor(() => expect(postBody).toEqual({ name: "newteam" }));
    // dialog closes + list invalidates (refetch)
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2));
  });

  it("test_create_blank_name_blocked", async () => {
    const user = userEvent.setup();
    let postCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.post(`${APP}/api/gw/admin/teams`, () => {
        postCalled = true;
        return HttpResponse.json(TEAM_A, { status: 201 });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    await user.click(screen.getByRole("button", { name: /create team/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /create/i }));

    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(postCalled).toBe(false);
  });

  it("test_create_duplicate_409", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.post(`${APP}/api/gw/admin/teams`, () =>
        problem("Team already exists", 409, "ERR_TEAM_EXISTS"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    await user.click(screen.getByRole("button", { name: /create team/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/team name/i), "platform");
    await user.click(within(dialog).getByRole("button", { name: /create/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/already exists/i);
    // list unchanged: still exactly one data row (+ header), dialog still open
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
});

// ── budget ──────────────────────────────────────────────────────────────────────
describe("TeamsPage — set/clear budget", () => {
  it("test_set_budget_success", async () => {
    const user = userEvent.setup();
    let patchBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_B])),
      http.patch(`${APP}/api/gw/admin/teams/:id`, async ({ request }) => {
        await delay(40);
        patchBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TEAM_B, team_budget_usd: "250.00" });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("research");

    const input = screen.getByRole("textbox", { name: /budget for research/i });
    await user.type(input, "250.00");
    const save = screen.getByRole("button", { name: /save budget for research/i });
    await user.click(save);

    // disabled in-flight, then settles
    await waitFor(() => expect(save).toBeDisabled());
    await waitFor(() => expect(patchBody).toEqual({ team_budget_usd: "250.00" }));
    expect(await screen.findByText("250.00")).toBeInTheDocument();
  });

  it("test_clear_budget_success", async () => {
    const user = userEvent.setup();
    let patchBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.patch(`${APP}/api/gw/admin/teams/:id`, async ({ request }) => {
        patchBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TEAM_A, team_budget_usd: null });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    const input = screen.getByRole("textbox", { name: /budget for platform/i });
    await user.clear(input);
    await user.click(screen.getByRole("button", { name: /save budget for platform/i }));

    await waitFor(() => expect(patchBody).toEqual({ team_budget_usd: null }));
    expect(await screen.findByText("—")).toBeInTheDocument();
  });

  it("test_invalid_budget_blocked", async () => {
    const user = userEvent.setup();
    let patchCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_B])),
      http.patch(`${APP}/api/gw/admin/teams/:id`, () => {
        patchCalled = true;
        return HttpResponse.json(TEAM_B);
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("research");

    const input = screen.getByRole("textbox", { name: /budget for research/i });
    await user.type(input, "abc");
    await user.click(screen.getByRole("button", { name: /save budget for research/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(patchCalled).toBe(false);
  });

  it("test_budget_patch_server_error", async () => {
    // §5 safety rule: a failed PATCH must NEVER fail silently — the server 422/404
    // surfaces as a role=alert carrying the title. (Adversarial DEFECT: the budget
    // mutation had no onError, swallowing the failure.)
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.patch(`${APP}/api/gw/admin/teams/:id`, () =>
        problem("Invalid budget", 422, "ERR_PAYLOAD_INVALID"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    const input = screen.getByRole("textbox", { name: /budget for platform/i });
    await user.clear(input);
    await user.type(input, "250.00");
    await user.click(screen.getByRole("button", { name: /save budget for platform/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid budget/i);
  });
});

// ── delete team ──────────────────────────────────────────────────────────────────
describe("TeamsPage — delete team", () => {
  it("test_delete_team_success_clears_selection", async () => {
    const user = userEvent.setup();
    let getCount = 0;
    let deleted = false;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => {
        getCount += 1;
        return HttpResponse.json(deleted ? [] : [TEAM_A]);
      }),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
      http.delete(`${APP}/api/gw/admin/teams/:id`, () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    // select the team first → members panel shows
    await user.click(screen.getByRole("button", { name: "platform" }));
    expect(await screen.findByText("user-1")).toBeInTheDocument();

    // delete it
    await user.click(screen.getByRole("button", { name: /delete team platform/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2));
    // selection cleared: members panel gone
    await waitFor(() => expect(screen.queryByText("user-1")).not.toBeInTheDocument());
  });
});

// ── members panel ────────────────────────────────────────────────────────────────
describe("TeamsPage — members panel", () => {
  it("test_view_members", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    await user.click(screen.getByRole("button", { name: "platform" }));

    expect(await screen.findByText("user-1")).toBeInTheDocument();
    expect(screen.getByText("user-2")).toBeInTheDocument();
    expect(screen.getByText("lead")).toBeInTheDocument();
    expect(screen.getByText("member")).toBeInTheDocument();
  });

  it("test_members_empty", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () =>
        HttpResponse.json({ ...TEAM_A_DETAIL, members: [] }),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    await user.click(screen.getByRole("button", { name: "platform" }));
    expect(await screen.findByText(/no members yet/i)).toBeInTheDocument();
  });

  it("test_add_member_by_email_success", async () => {
    const user = userEvent.setup();
    let detailCount = 0;
    let postBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => {
        detailCount += 1;
        return HttpResponse.json(TEAM_A_DETAIL);
      }),
      http.post(`${APP}/api/gw/admin/teams/:id/members`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { team_id: "team-a", user_id: "user-9", role: "member", added_at: "2026-02-01T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /add member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/email/i), "dev@acme.io");
    await user.selectOptions(within(dialog).getByLabelText(/role/i), "member");
    await user.click(within(dialog).getByRole("button", { name: /add/i }));

    await waitFor(() => expect(postBody).toEqual({ email: "dev@acme.io", role: "member" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(detailCount).toBeGreaterThanOrEqual(2));
  });

  it("test_add_member_unknown_email_404", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
      http.post(`${APP}/api/gw/admin/teams/:id/members`, () =>
        problem("User not found", 404, "ERR_USER_NOT_FOUND"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /add member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/email/i), "ghost@nowhere.io");
    await user.click(within(dialog).getByRole("button", { name: /add/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /no user with that email in your tenant/i,
    );
  });

  it("test_add_member_exists_409", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
      http.post(`${APP}/api/gw/admin/teams/:id/members`, () =>
        problem("Already a member", 409, "ERR_MEMBER_EXISTS"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /add member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/email/i), "user1@acme.io");
    await user.click(within(dialog).getByRole("button", { name: /add/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/already a member/i);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("test_add_member_invalid_422", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
      http.post(`${APP}/api/gw/admin/teams/:id/members`, () =>
        problem("Invalid payload", 422, "ERR_PAYLOAD_INVALID"),
      ),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /add member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/email/i), "dev@acme.io");
    await user.click(within(dialog).getByRole("button", { name: /add/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/invalid payload/i);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("test_add_member_blank_email_blocked", async () => {
    const user = userEvent.setup();
    let postCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => HttpResponse.json(TEAM_A_DETAIL)),
      http.post(`${APP}/api/gw/admin/teams/:id/members`, () => {
        postCalled = true;
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /add member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /add/i }));

    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(postCalled).toBe(false);
  });

  it("test_remove_member_success", async () => {
    const user = userEvent.setup();
    let detailCount = 0;
    let removedUrl: string | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])),
      http.get(`${APP}/api/gw/admin/teams/:id`, () => {
        detailCount += 1;
        return HttpResponse.json(TEAM_A_DETAIL);
      }),
      http.delete(`${APP}/api/gw/admin/teams/:id/members/:uid`, ({ request }) => {
        removedUrl = request.url;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");
    await user.click(screen.getByRole("button", { name: "platform" }));
    await screen.findByText("user-1");

    await user.click(screen.getByRole("button", { name: /remove user-1/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(removedUrl).toMatch(/\/members\/user-1$/));
    await waitFor(() => expect(detailCount).toBeGreaterThanOrEqual(2));
  });
});

// ── a11y + nav ────────────────────────────────────────────────────────────────────
describe("TeamsPage — axe + dialog a11y + nav", () => {
  it("test_axe_clean_and_dialog_a11y", async () => {
    const user = userEvent.setup();
    server.use(http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([TEAM_A])));
    const { container } = render(<TeamsPage />, { wrapper: Wrapper });
    await screen.findByText("platform");

    // populated list is axe-clean
    expect(await axeSeriousCritical(container)).toEqual([]);

    // open the create dialog → role=dialog aria-modal, axe-clean, Escape closes
    await user.click(screen.getByRole("button", { name: /create team/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(await axeSeriousCritical(container)).toEqual([]);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // a destructive confirm dialog is ALSO axe-clean + aria-modal + Escape closes
    await user.click(screen.getByRole("button", { name: /delete team platform/i }));
    const confirm = await screen.findByRole("dialog");
    expect(confirm).toHaveAttribute("aria-modal", "true");
    expect(await axeSeriousCritical(container)).toEqual([]);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("test_nav_exposes_teams", () => {
    render(
      <AppShell activePath="/app/teams">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    const link = within(nav).getByRole("link", { name: /teams/i });
    expect(link).toHaveAttribute("href", "/app/teams");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
