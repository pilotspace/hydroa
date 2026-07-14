/**
 * tests/agents-console.test.tsx — RED suite for the Agents console dashboard page
 * (agents-console TASK.md §3 — FROZEN @ v1, consuming agent-identity-governance
 * §3 FROZEN @ v2 + mcp-connector-passthrough §3 FROZEN @ v2 + logs-explorer-api
 * reused verbatim).
 *
 * Mirrors tests/logs.test.tsx's own convention (fresh QueryClientProvider per
 * render, msw per-test overrides via server.use, within(section())-scoped
 * assertions, MODULE_NOT_FOUND as the established true-red signal).
 *
 * Two freeze-decision deviations from the literal §2 scenario prose, both driven
 * by the R1 dispatch brief and the §3 CONTRACT's own "Freeze decisions" block
 * (CR-A/CR-B RESOLVED, v2) — NOT a test weakening, the §2 prose is simply stale
 * relative to the later-resolved v2 contract it sits above in the same document:
 *   1. "Spend renders as an honest degrade" (§2) is superseded by CR-A: §3's
 *      AgentPrincipal.spend_usd_this_month is now a REAL, never-null field
 *      ("0.00" floor) — this suite asserts the real value renders, never a
 *      fabricated "$0.00" when the field is ACTUALLY absent (which v2 no longer
 *      allows) and never "not available yet" copy.
 *   2. "Admin attaches a token by pasting its id" (§2) is superseded by CR-B:
 *      GET /admin/agents/{id}/tokens gives ManageTokensDialog a real Detach
 *      picker over currently-attached tokens; Attach still has no
 *      unattached-token enumeration anywhere in the frozen surface, so it keeps
 *      an explicit-id field for NEW attachments only (disclosed in-dialog copy).
 *
 * M8's "LogDetailDrawer UNCHANGED" instruction is honored literally: the 404
 * empty-state text asserted here is the drawer's own real, frozen copy ("Log not
 * found"), not the §2 prose's "Session not found" gloss — forking the drawer's
 * copy would violate the reuse-verbatim Must this task's own M6/M8 state.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { AgentsConsolePage } from "@/components/agents/AgentsConsolePage";
import { NAV_GROUPS, AppShell } from "@/components/ui/app-shell";

const APP = "http://localhost:3000";
const AGENTS_URL = `${APP}/api/gw/admin/agents`;
const USERS_URL = `${APP}/api/gw/admin/users`;
const KEYS_URL = `${APP}/api/gw/admin/keys`;
const LOGS_URL = `${APP}/api/gw/admin/logs`;
const MCP_TENANT_URL = `${APP}/api/gw/admin/mcp-servers`;
const AUTH_ME_URL = `${APP}/api/auth/me`;
const tokensUrl = (id: string) => `${APP}/api/gw/admin/agents/${id}/tokens`;
const killUrl = (id: string) => `${APP}/api/gw/admin/agents/${id}/kill`;
const attachUrl = (id: string, tokenId: string) => `${APP}/api/gw/admin/agents/${id}/tokens/${tokenId}/attach`;
const detachUrl = (id: string, tokenId: string) => `${APP}/api/gw/admin/agents/${id}/tokens/${tokenId}`;
const logDetailUrl = (id: string) => `${APP}/api/gw/admin/logs/${id}`;
const keyMcpUrl = (keyId: string) => `${APP}/api/gw/admin/keys/${keyId}/mcp-servers`;

const AGENT_LIVE = {
  id: "a1111111-0000-0000-0000-000000000001",
  tenant_id: "t-1",
  name: "billing-bot",
  owner_user_id: "u-1111-1111-1111-111111111111",
  monthly_budget_usd: "50.00",
  rpm_limit: null,
  tpm_limit: null,
  created_at: "2026-06-01T00:00:00Z",
  last_seen_at: "2026-07-14T10:00:00Z",
  killed_at: null,
  attached_token_count: 1,
  spend_usd_this_month: "12.50",
};

const AGENT_KILLED = {
  id: "a2222222-0000-0000-0000-000000000002",
  tenant_id: "t-1",
  name: "crawler-01",
  owner_user_id: null,
  monthly_budget_usd: null,
  rpm_limit: null,
  tpm_limit: null,
  created_at: "2026-05-01T00:00:00Z",
  last_seen_at: null,
  killed_at: "2026-07-10T00:00:00Z",
  attached_token_count: 0,
  spend_usd_this_month: "0.00",
};

const AGENT_UNRESOLVED_OWNER = {
  ...AGENT_LIVE,
  id: "a3333333-0000-0000-0000-000000000003",
  name: "ops-agent",
  owner_user_id: "u-9999-9999-9999-999999999999",
};

const USERS_RESPONSE = {
  users: [{ id: "u-1111-1111-1111-111111111111", email: "j.alvarez@acme.io", role: "admin" }],
};

const KEYS_RESPONSE = [{ key_id: "k-1", name: "prod-agent-key" }];

function agentsResponse(agents: unknown[]) {
  return { agents };
}

function meResponse(role: string) {
  return {
    user_id: "u-caller",
    tenant_id: "t-1",
    email: "caller@acme.io",
    role,
    exp: Math.floor(Date.now() / 1000) + 86400,
  };
}

function installBaseHandlers(role: string) {
  server.use(
    http.get(AUTH_ME_URL, () => HttpResponse.json(meResponse(role))),
    http.get(USERS_URL, () => HttpResponse.json(USERS_RESPONSE)),
    http.get(KEYS_URL, () => HttpResponse.json(KEYS_RESPONSE)),
    http.get(MCP_TENANT_URL, () =>
      HttpResponse.json({ servers: [], updated_at: null }),
    ),
  );
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderPage() {
  return render(<AgentsConsolePage />, { wrapper: Wrapper });
}

function section() {
  return screen.getByRole("region", { name: /^agents$/i });
}

beforeEach(() => {
  installBaseHandlers("member");
});

describe("AgentsConsolePage — nav entry (M1)", () => {
  beforeAll(() => {
    if (!("ResizeObserver" in globalThis)) {
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => true,
      }),
    });
  });

  it("test_agents_nav_item_lives_in_govern_group_admin_gated", () => {
    const govern = NAV_GROUPS.find((g) => g.label === "Govern");
    expect(govern).toBeDefined();
    const agents = govern?.items.find((i) => i.href === "/app/agents");
    expect(agents).toBeDefined();
    expect(agents?.label).toBe("Agents");
    expect(agents?.minRole).toBe("admin");
  });

  it("test_agents_nav_visible_for_admin_hidden_for_member", () => {
    const { unmount } = render(
      <AppShell role="admin">
        <div>content</div>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: /^agents$/i })).toBeInTheDocument();
    unmount();

    render(
      <AppShell role="member">
        <div>content</div>
      </AppShell>,
    );
    expect(screen.queryByRole("link", { name: /^agents$/i })).not.toBeInTheDocument();
  });
});

describe("AgentsConsolePage — page states + Directory default (M1, R1)", () => {
  it("test_directory_tab_is_the_default_active_tab", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))));

    renderPage();

    await waitFor(() => expect(within(section()).getByText("billing-bot")).toBeInTheDocument());
    expect(within(section()).getByRole("tab", { name: /directory/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(section()).getByRole("tab", { name: /^sessions$/i })).toBeInTheDocument();
    expect(within(section()).getByRole("tab", { name: /^policy$/i })).toBeInTheDocument();
  });

  it("test_direct_url_visit_below_role_gate_shows_clear_nonleaking_error", async () => {
    installBaseHandlers("member");
    server.use(
      http.get(AGENTS_URL, () =>
        HttpResponse.json({ title: "Insufficient role", status: 403, code: "ERR_AUTH_FORBIDDEN" }, { status: 403 }),
      ),
    );

    renderPage();

    await waitFor(() => {
      expect(within(section()).getByText(/you don't have access to agents/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("test_one_h1", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))));
    const { container } = renderPage();

    await waitFor(() => expect(within(section()).getByText("billing-bot")).toBeInTheDocument());
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });
});

describe("AgentsConsolePage — Directory identity cards (M2)", () => {
  it("test_directory_renders_one_card_per_agent_owner_resolved", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE, AGENT_KILLED]))),
    );

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    expect(within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`)).toBeInTheDocument();
    const liveCard = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await waitFor(() => expect(within(liveCard).getByText(/j\.alvarez@acme\.io/i)).toBeInTheDocument());
  });

  it("test_unresolved_or_absent_owner_never_renders_blank", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () =>
        HttpResponse.json(agentsResponse([AGENT_UNRESOLVED_OWNER, AGENT_KILLED])),
      ),
    );

    renderPage();

    await waitFor(() =>
      expect(within(section()).getByTestId(`agent-card-${AGENT_UNRESOLVED_OWNER.id}`)).toBeInTheDocument(),
    );
    const unresolvedCard = within(section()).getByTestId(`agent-card-${AGENT_UNRESOLVED_OWNER.id}`);
    // raw owner_user_id shown verbatim — never blank
    await waitFor(() =>
      expect(
        within(unresolvedCard).getByText(new RegExp(AGENT_UNRESOLVED_OWNER.owner_user_id)),
      ).toBeInTheDocument(),
    );

    const killedCard = within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`);
    expect(within(killedCard).getByText(/no owner set/i)).toBeInTheDocument();
  });

  it("test_spend_renders_the_real_field_never_fabricated_zero", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE, AGENT_KILLED]))),
    );

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const liveCard = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    // real, non-zero spend from the frozen v2 field — never "not available yet"
    expect(within(liveCard).getByText(/\$12\.50/)).toBeInTheDocument();
    expect(within(liveCard).queryByText(/not available yet/i)).not.toBeInTheDocument();

    const killedCard = within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`);
    // "0.00" floor rendered as a real value, not fabricated — the counter genuinely
    // reads zero on the frozen response, not a client-side guess
    expect(within(killedCard).getByText(/\$0\.00/)).toBeInTheDocument();
  });

  it("test_live_vs_killed_state_is_never_color_only", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE, AGENT_KILLED]))),
    );

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const liveCard = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    const killedCard = within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`);

    expect(within(liveCard).getByText(/^live$/i)).toBeInTheDocument();
    expect(within(killedCard).getByText(/^killed$/i)).toBeInTheDocument();
    // each state carries its own distinct icon (aria-hidden svg), not tint alone
    expect(within(liveCard).getByText(/^live$/i).parentElement?.querySelector("svg")).toBeTruthy();
    expect(within(killedCard).getByText(/^killed$/i).parentElement?.querySelector("svg")).toBeTruthy();
  });

  it("test_killed_card_never_shows_a_kill_button", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_KILLED]))));

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`)).toBeInTheDocument());
    const killedCard = within(section()).getByTestId(`agent-card-${AGENT_KILLED.id}`);
    expect(within(killedCard).queryByRole("button", { name: /^kill$/i })).not.toBeInTheDocument();
    expect(within(killedCard).getAllByText(/killed/i).length).toBeGreaterThan(0);
  });

  it("test_directory_empty_state", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([]))));

    renderPage();

    await waitFor(() => expect(within(section()).getByText(/no agents yet/i)).toBeInTheDocument());
  });
});

describe("AgentsConsolePage — create agent (M3, R2, R3)", () => {
  it("test_admin_creates_a_named_agent", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([]))),
      http.post(AGENTS_URL, async ({ request }) => {
        const body = (await request.json()) as { name: string };
        expect(body.name).toBe("billing-bot");
        return HttpResponse.json({ ...AGENT_LIVE, name: body.name }, { status: 200 });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByText(/no agents yet/i)).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /new agent/i }));

    const dialog = screen.getByRole("dialog", { name: /new agent/i });
    await user.type(within(dialog).getByLabelText(/^name$/i), "billing-bot");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /new agent/i })).not.toBeInTheDocument();
    });
    expect(within(section()).getByText("billing-bot")).toBeInTheDocument();
  });

  it("test_duplicate_name_conflict_surfaces_inline_dialog_stays_open", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.post(AGENTS_URL, () =>
        HttpResponse.json(
          { title: "Agent principal name already in use", status: 409, code: "ERR_AGENT_PRINCIPAL_NAME_CONFLICT" },
          { status: 409 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByText("billing-bot")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /new agent/i }));

    const dialog = screen.getByRole("dialog", { name: /new agent/i });
    await user.type(within(dialog).getByLabelText(/^name$/i), "billing-bot");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(within(dialog).getByRole("alert")).toHaveTextContent(/already in use/i);
    });
    expect(screen.getByRole("dialog", { name: /new agent/i })).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^name$/i)).toHaveValue("billing-bot");
  });

  it("test_invalid_budget_surfaces_inline_no_card_added", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([]))),
      http.post(AGENTS_URL, () =>
        HttpResponse.json({ title: "Invalid request", status: 422, code: "ERR_PAYLOAD_INVALID" }, { status: 422 }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByText(/no agents yet/i)).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /new agent/i }));

    const dialog = screen.getByRole("dialog", { name: /new agent/i });
    await user.type(within(dialog).getByLabelText(/^name$/i), "neg-budget");
    await user.type(within(dialog).getByLabelText(/monthly budget/i), "-5.00");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(within(dialog).getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("dialog", { name: /new agent/i })).toBeInTheDocument();
    expect(within(section()).queryByText("neg-budget")).not.toBeInTheDocument();
  });
});

describe("AgentsConsolePage — kill switch (M4, R5)", () => {
  it("test_owner_kills_a_live_agent_through_confirm_dialog", async () => {
    installBaseHandlers("owner");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.post(killUrl(AGENT_LIVE.id), () =>
        HttpResponse.json({ id: AGENT_LIVE.id, killed_at: "2026-07-14T12:00:00Z" }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const card = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await user.click(within(card).getByRole("button", { name: /^kill$/i }));

    const confirmDialog = screen.getByRole("dialog");
    expect(within(confirmDialog).getByText(/irreversib|cannot be undone|permanent/i)).toBeInTheDocument();
    await user.click(within(confirmDialog).getByRole("button", { name: /^kill/i }));

    await waitFor(() => {
      expect(within(card).getAllByText(/killed/i).length).toBeGreaterThan(0);
    });
    expect(within(card).queryByRole("button", { name: /^kill$/i })).not.toBeInTheDocument();
  });

  it("test_killed_races_another_admin_converges_on_next_refetch", async () => {
    installBaseHandlers("owner");
    let killCalls = 0;
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.post(killUrl(AGENT_LIVE.id), () => {
        killCalls += 1;
        // idempotent per the frozen contract — always 200 with the original killed_at
        return HttpResponse.json({ id: AGENT_LIVE.id, killed_at: "2026-07-14T09:00:00Z" });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const card = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await user.click(within(card).getByRole("button", { name: /^kill$/i }));
    const confirmDialog = screen.getByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /^kill/i }));

    await waitFor(() => expect(killCalls).toBe(1));
    await waitFor(() => expect(within(card).getAllByText(/killed/i).length).toBeGreaterThan(0));
    // never a stale "Live" render after the resolve
    expect(within(card).queryByText(/^live$/i)).not.toBeInTheDocument();
  });
});

describe("AgentsConsolePage — manage tokens (M5, R4)", () => {
  it("test_admin_attaches_a_token_by_id_and_count_increments", async () => {
    installBaseHandlers("admin");
    let attachedCount = AGENT_LIVE.attached_token_count;
    server.use(
      http.get(AGENTS_URL, () =>
        HttpResponse.json(agentsResponse([{ ...AGENT_LIVE, attached_token_count: attachedCount }])),
      ),
      http.get(tokensUrl(AGENT_LIVE.id), () => HttpResponse.json([])),
      http.post(attachUrl(AGENT_LIVE.id, "tok-999"), () => {
        attachedCount += 1;
        return HttpResponse.json({ principal_id: AGENT_LIVE.id, token_id: "tok-999", attached_at: "2026-07-14T12:00:00Z" });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const card = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await user.click(within(card).getByRole("button", { name: /manage tokens/i }));

    const dialog = screen.getByRole("dialog", { name: /manage tokens/i });
    await waitFor(() => expect(within(dialog).getByText(/no tokens attached/i)).toBeInTheDocument());

    await user.type(within(dialog).getByLabelText(/token id/i), "tok-999");
    await user.click(within(dialog).getByRole("button", { name: /^attach$/i }));

    await waitFor(() => {
      expect(within(card).getByText(/2 tokens attached/i)).toBeInTheDocument();
    });
  });

  it("test_attach_unknown_token_surfaces_inline_dialog_stays_open", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(tokensUrl(AGENT_LIVE.id), () => HttpResponse.json([])),
      http.post(attachUrl(AGENT_LIVE.id, "tok-missing"), () =>
        HttpResponse.json(
          { title: "Agent token not found", status: 404, code: "ERR_AGENT_TOKEN_NOT_FOUND" },
          { status: 404 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const card = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await user.click(within(card).getByRole("button", { name: /manage tokens/i }));

    const dialog = screen.getByRole("dialog", { name: /manage tokens/i });
    await waitFor(() => expect(within(dialog).getByLabelText(/token id/i)).toBeInTheDocument());
    await user.type(within(dialog).getByLabelText(/token id/i), "tok-missing");
    await user.click(within(dialog).getByRole("button", { name: /^attach$/i }));

    await waitFor(() => {
      expect(within(dialog).getByRole("alert")).toHaveTextContent(/not found/i);
    });
    expect(screen.getByRole("dialog", { name: /manage tokens/i })).toBeInTheDocument();
    expect(within(card).getByText(/1 token attached/i)).toBeInTheDocument();
  });

  it("test_manage_tokens_lists_attached_tokens_with_detach", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(tokensUrl(AGENT_LIVE.id), () =>
        HttpResponse.json([
          { id: "tok-1", name: "agent:read", created_at: "2026-06-01T00:00:00Z", revoked_at: null, access_expires_at: "2026-08-01T00:00:00Z" },
        ]),
      ),
      http.delete(detachUrl(AGENT_LIVE.id, "tok-1"), () =>
        HttpResponse.json({ principal_id: AGENT_LIVE.id, token_id: "tok-1", detached_at: "2026-07-14T12:00:00Z" }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const card = within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`);
    await user.click(within(card).getByRole("button", { name: /manage tokens/i }));

    const dialog = screen.getByRole("dialog", { name: /manage tokens/i });
    await waitFor(() => expect(within(dialog).getByText(/agent:read/i)).toBeInTheDocument());
    await user.click(within(dialog).getByRole("button", { name: /detach/i }));

    await waitFor(() => expect(within(dialog).queryByText(/agent:read/i)).not.toBeInTheDocument());
  });
});

describe("AgentsConsolePage — Sessions tab (M6, M7, M8, R8, R9)", () => {
  it("test_sessions_reuses_the_logs_drawer_verbatim", async () => {
    installBaseHandlers("admin");
    const LOG_ITEM = {
      id: "log-1",
      created_at: "2026-07-14T10:41:02Z",
      model_id: "mcp::mcp.acme.example::search",
      key_id: "k-1",
      status_code: 200,
      cost_usd: "0.0100",
      stream: false,
      cached: false,
      latency_ms: 118,
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
    };
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, () => HttpResponse.json({ items: [LOG_ITEM], next_cursor: null, has_more: false })),
      http.get(logDetailUrl("log-1"), () =>
        HttpResponse.json({
          ...LOG_ITEM,
          request_id: "req-1",
          request_body: { messages: [{ role: "user", content: "hi" }] },
          response_body: { content: "ok" },
          scrub_status: "scrubbed",
          truncated: false,
          guardrail_verdict: { blocked: false, blocked_by: null, pii_masked: false },
        }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));

    await waitFor(() => expect(within(section()).getByText(/mcp::mcp\.acme\.example::search/i)).toBeInTheDocument());
    await user.click(within(section()).getByRole("row", { name: /mcp::mcp\.acme\.example::search/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /overview/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: /guardrail verdict/i })).toBeInTheDocument();
    expect(screen.getByText(/trace metadata only/i)).toBeInTheDocument();
  });

  it("test_exact_server_tool_filter_narrows_to_one_call_shape", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        const modelId = url.searchParams.get("model_id");
        // the initial (pre-filter) fetch carries no model_id — only assert the exact
        // value once the operator has actually set the server::tool field
        if (modelId !== null) {
          expect(modelId).toBe("mcp::mcp.acme.example::search");
        }
        return HttpResponse.json({ items: [], next_cursor: null, has_more: false });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));
    await waitFor(() => expect(within(section()).getByLabelText(/server::tool/i)).toBeInTheDocument());

    fireEvent.change(within(section()).getByLabelText(/server::tool/i), {
      target: { value: "mcp::mcp.acme.example::search" },
    });

    await waitFor(() => {
      expect(within(section()).queryByText(/showing mcp sessions found on the current page/i)).not.toBeInTheDocument();
    });
  });

  it("test_empty_filter_degrades_honestly_with_page_scoped_banner", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get("model_id")).toBeNull();
        return HttpResponse.json({
          items: [
            {
              id: "log-chat",
              created_at: "2026-07-14T10:00:00Z",
              model_id: "openai/gpt-4o",
              key_id: "k-1",
              status_code: 200,
              cost_usd: "0.01",
              stream: false,
              cached: false,
              latency_ms: 100,
              prompt_tokens: 1,
              completion_tokens: 1,
              total_tokens: 2,
            },
            {
              id: "log-mcp",
              created_at: "2026-07-14T10:01:00Z",
              model_id: "mcp::mcp.acme.example::fetch",
              key_id: "k-1",
              status_code: 200,
              cost_usd: "0.02",
              stream: false,
              cached: false,
              latency_ms: 90,
              prompt_tokens: 1,
              completion_tokens: 1,
              total_tokens: 2,
            },
          ],
          next_cursor: null,
          has_more: false,
        });
      }),
    );

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));

    await waitFor(() => expect(within(section()).getByText(/mcp::mcp\.acme\.example::fetch/i)).toBeInTheDocument());
    expect(within(section()).queryByText(/openai\/gpt-4o/i)).not.toBeInTheDocument();
    expect(within(section()).getByText(/showing mcp sessions found on the current page/i)).toBeInTheDocument();
  });

  it("test_blocked_tool_call_visible_in_drawer_with_zero_new_rendering_branch", async () => {
    installBaseHandlers("admin");
    const LOG_ITEM = {
      id: "log-blocked",
      created_at: "2026-07-14T10:41:02Z",
      model_id: "mcp::mcp.acme.example::search",
      key_id: "k-1",
      status_code: 403,
      cost_usd: null,
      stream: false,
      cached: false,
      latency_ms: null,
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
    };
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, () => HttpResponse.json({ items: [LOG_ITEM], next_cursor: null, has_more: false })),
      http.get(logDetailUrl("log-blocked"), () =>
        HttpResponse.json({
          ...LOG_ITEM,
          request_id: "req-2",
          request_body: null,
          response_body: null,
          scrub_status: "scrubbed",
          truncated: false,
          guardrail_verdict: { blocked: true, blocked_by: "mcp_server_not_allowed", pii_masked: false },
        }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));
    await waitFor(() => expect(within(section()).getByText(/mcp::mcp\.acme\.example::search/i)).toBeInTheDocument());
    await user.click(within(section()).getByRole("row", { name: /mcp::mcp\.acme\.example::search/i }));

    await waitFor(() => expect(screen.getByText(/^blocked$/i)).toBeInTheDocument());
    expect(screen.getByText("mcp_server_not_allowed")).toBeInTheDocument();
  });

  it("test_sessions_drawer_404_scoped_to_drawer_table_untouched", async () => {
    installBaseHandlers("admin");
    const LOG_ITEM = {
      id: "log-purged",
      created_at: "2026-07-14T10:41:02Z",
      model_id: "mcp::mcp.acme.example::search",
      key_id: "k-1",
      status_code: 200,
      cost_usd: "0.01",
      stream: false,
      cached: false,
      latency_ms: 50,
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    };
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, () => HttpResponse.json({ items: [LOG_ITEM], next_cursor: null, has_more: false })),
      http.get(logDetailUrl("log-purged"), () =>
        HttpResponse.json({ title: "Request log not found", status: 404, code: "ERR_LOG_NOT_FOUND" }, { status: 404 }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));
    await waitFor(() => expect(within(section()).getByText(/mcp::mcp\.acme\.example::search/i)).toBeInTheDocument());
    await user.click(within(section()).getByRole("row", { name: /mcp::mcp\.acme\.example::search/i }));

    // the drawer's own real, frozen copy — LogDetailDrawer is reused UNCHANGED (M8)
    await waitFor(() => expect(screen.getByText(/log not found/i)).toBeInTheDocument());
    // the table underneath is unaffected — queried by text, not role, since Radix's own
    // inert-background makes the rest of the page temporarily inaccessible by design
    // while the modal drawer is open (same idiom as logs-explorer-ui's own R2 test)
    expect(screen.getByText(/mcp::mcp\.acme\.example::search/i)).toBeInTheDocument();
  });

  it("test_invalid_sessions_filter_is_a_field_error_not_a_page_error", async () => {
    installBaseHandlers("admin");
    let callCount = 0;
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, () => {
        callCount += 1;
        return HttpResponse.json({
          items: [
            {
              id: "log-mcp",
              created_at: "2026-07-14T10:00:00Z",
              model_id: "mcp::mcp.acme.example::fetch",
              key_id: "k-1",
              status_code: 200,
              cost_usd: "0.02",
              stream: false,
              cached: false,
              latency_ms: 90,
              prompt_tokens: 1,
              completion_tokens: 1,
              total_tokens: 2,
            },
          ],
          next_cursor: null,
          has_more: false,
        });
      }),
    );

    renderPage();
    const user = userEvent.setup();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));
    await waitFor(() => expect(within(section()).getByText(/mcp::mcp\.acme\.example::fetch/i)).toBeInTheDocument());
    const callsAfterInitial = callCount;

    fireEvent.change(within(section()).getByLabelText(/^from$/i), { target: { value: "2026-07-14T00:00" } });
    await waitFor(() => expect(callCount).toBeGreaterThan(callsAfterInitial));
    const callsAfterValidChange = callCount;

    fireEvent.change(within(section()).getByLabelText(/^to$/i), { target: { value: "2026-07-01T00:00" } });

    await waitFor(() => {
      expect(within(section()).getByRole("alert")).toHaveTextContent(/after/i);
    });
    expect(within(section()).getByText(/mcp::mcp\.acme\.example::fetch/i)).toBeInTheDocument();
    expect(callCount).toBe(callsAfterValidChange);
  });
});

describe("AgentsConsolePage — Policy tab (M9, M10, M11, R6, R7)", () => {
  it("test_owner_sets_the_tenant_mcp_allow_list", async () => {
    installBaseHandlers("owner");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () => HttpResponse.json({ servers: [], updated_at: null })),
      http.put(MCP_TENANT_URL, async ({ request }) => {
        const body = (await request.json()) as { servers: { url: string; label: string }[] };
        expect(body.servers).toEqual([{ url: "https://mcp.acme.example/v1", label: "Acme" }]);
        return HttpResponse.json({ servers: body.servers, updated_at: "2026-07-14T12:00:00Z" });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));

    expect(within(section()).getByText(/unlisted servers are refused, not warned/i)).toBeInTheDocument();

    await user.click(within(section()).getByRole("button", { name: /add server/i }));
    const urlInputs = within(section()).getAllByLabelText(/server url/i);
    const labelInputs = within(section()).getAllByLabelText(/^label$/i);
    fireEvent.change(urlInputs[urlInputs.length - 1], { target: { value: "https://mcp.acme.example/v1" } });
    fireEvent.change(labelInputs[labelInputs.length - 1], { target: { value: "Acme" } });

    await user.click(within(section()).getAllByRole("button", { name: /^save$/i })[0]);

    await waitFor(() => {
      expect(within(section()).getByDisplayValue("https://mcp.acme.example/v1")).toBeInTheDocument();
    });
  });

  it("test_non_owner_cannot_edit_tenant_allow_list", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () =>
        HttpResponse.json({ servers: [{ url: "https://mcp.acme.example/v1", label: "Acme" }], updated_at: "2026-07-01T00:00:00Z" }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));

    await waitFor(() => expect(within(section()).getByText(/mcp\.acme\.example/i)).toBeInTheDocument());
    // GET is any role — the list still renders, read-only
    expect(within(section()).queryByRole("button", { name: /add server/i })).not.toBeInTheDocument();
  });

  it("test_owner_admin_sets_a_per_key_override", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () => HttpResponse.json({ servers: [], updated_at: null })),
      http.get(keyMcpUrl("k-1"), () => HttpResponse.json({ servers: null, source: "tenant" })),
      http.put(keyMcpUrl("k-1"), async ({ request }) => {
        const body = (await request.json()) as { servers: { url: string; label: string }[] };
        expect(body.servers).toEqual([{ url: "https://mcp.narrow.example", label: "narrow" }]);
        return HttpResponse.json({ servers: body.servers, source: "key" });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));

    await waitFor(() => expect(within(section()).getByLabelText(/^key$/i)).toBeInTheDocument());
    await user.selectOptions(within(section()).getByLabelText(/^key$/i), "k-1");

    await user.click(within(section()).getByRole("radio", { name: /custom list for this key/i }));
    await user.click(within(section()).getAllByRole("button", { name: /add server/i })[0]);

    const urlInputs = within(section()).getAllByLabelText(/server url/i);
    const labelInputs = within(section()).getAllByLabelText(/^label$/i);
    fireEvent.change(urlInputs[urlInputs.length - 1], { target: { value: "https://mcp.narrow.example" } });
    fireEvent.change(labelInputs[labelInputs.length - 1], { target: { value: "narrow" } });

    const saveButtons = within(section()).getAllByRole("button", { name: /^save$/i });
    await user.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() => {
      expect(within(section()).getByDisplayValue("https://mcp.narrow.example")).toBeInTheDocument();
    });
  });

  it("test_explicitly_empty_per_key_override_is_visually_distinguished_from_inherit", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () => HttpResponse.json({ servers: [], updated_at: null })),
      http.get(keyMcpUrl("k-1"), () => HttpResponse.json({ servers: [], source: "key" })),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));
    await waitFor(() => expect(within(section()).getByLabelText(/^key$/i)).toBeInTheDocument());
    await user.selectOptions(within(section()).getByLabelText(/^key$/i), "k-1");

    await waitFor(() => {
      expect(within(section()).getByText(/an empty (custom )?list blocks this key from every mcp server/i)).toBeInTheDocument();
    });
    expect(within(section()).getByRole("radio", { name: /custom list for this key/i })).toBeChecked();
  });

  it("test_invalid_server_url_surfaces_inline_prior_list_untouched", async () => {
    installBaseHandlers("owner");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () =>
        HttpResponse.json({ servers: [{ url: "https://mcp.acme.example/v1", label: "Acme" }], updated_at: "2026-07-01T00:00:00Z" }),
      ),
      http.put(MCP_TENANT_URL, () =>
        HttpResponse.json(
          { title: "MCP server URL failed the egress-safety check", status: 422, code: "ERR_MCP_SERVER_URL_INVALID" },
          { status: 422 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));
    await waitFor(() => expect(within(section()).getByDisplayValue("https://mcp.acme.example/v1")).toBeInTheDocument());

    await user.click(within(section()).getByRole("button", { name: /add server/i }));
    const urlInputs = within(section()).getAllByLabelText(/server url/i);
    fireEvent.change(urlInputs[urlInputs.length - 1], { target: { value: "http://mcp.acme.example/v1" } });

    await user.click(within(section()).getAllByRole("button", { name: /^save$/i })[0]);

    await waitFor(() => {
      expect(within(section()).getByRole("alert")).toBeInTheDocument();
    });
    // previously-saved list stays displayed, not optimistically cleared
    expect(within(section()).getByDisplayValue("https://mcp.acme.example/v1")).toBeInTheDocument();
  });

  it("test_key_deleted_concurrently_surfaces_inline_editor_replaced", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () => HttpResponse.json({ servers: [], updated_at: null })),
      http.get(keyMcpUrl("k-1"), () => HttpResponse.json({ servers: [], source: "key" })),
      http.put(keyMcpUrl("k-1"), () =>
        HttpResponse.json({ title: "Key not found", status: 404, code: "ERR_KEY_NOT_FOUND" }, { status: 404 }),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));
    await waitFor(() => expect(within(section()).getByLabelText(/^key$/i)).toBeInTheDocument());
    await user.selectOptions(within(section()).getByLabelText(/^key$/i), "k-1");

    await waitFor(() => expect(within(section()).getAllByRole("button", { name: /^save$/i }).length).toBeGreaterThan(0));
    const saveButtons = within(section()).getAllByRole("button", { name: /^save$/i });
    await user.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() => {
      expect(within(section()).getByText(/this key no longer exists/i)).toBeInTheDocument();
    });
  });
});

describe("AgentsConsolePage — trust boundary + accessibility (M12, M13)", () => {
  it("test_no_raw_credential_ever_reaches_the_browser", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))));

    renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    // every fetch this render triggers targets same-origin /api/gw/... (bffGet/bffPost/etc
    // hard-code that prefix) — asserted structurally via the mocked URL matches above; here
    // we additionally assert no sk-/agent-token value ever appears in rendered DOM text.
    expect(document.body.textContent).not.toMatch(/sk-[a-zA-Z0-9]/);
  });

  it("test_axe_zero_violations_directory_tab", async () => {
    installBaseHandlers("admin");
    server.use(http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE, AGENT_KILLED]))));
    const { container } = renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });

  it("test_axe_zero_violations_sessions_tab", async () => {
    installBaseHandlers("admin");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(LOGS_URL, () => HttpResponse.json({ items: [], next_cursor: null, has_more: false })),
    );
    const user = userEvent.setup();
    const { container } = renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^sessions$/i }));
    await waitFor(() => expect(within(section()).getByLabelText(/server::tool/i)).toBeInTheDocument());

    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });

  it("test_axe_zero_violations_policy_tab", async () => {
    installBaseHandlers("owner");
    server.use(
      http.get(AGENTS_URL, () => HttpResponse.json(agentsResponse([AGENT_LIVE]))),
      http.get(MCP_TENANT_URL, () =>
        HttpResponse.json({ servers: [{ url: "https://mcp.acme.example/v1", label: "Acme" }], updated_at: "2026-07-01T00:00:00Z" }),
      ),
    );
    const user = userEvent.setup();
    const { container } = renderPage();

    await waitFor(() => expect(within(section()).getByTestId(`agent-card-${AGENT_LIVE.id}`)).toBeInTheDocument());
    await user.click(within(section()).getByRole("tab", { name: /^policy$/i }));
    await waitFor(() => expect(within(section()).getByDisplayValue("https://mcp.acme.example/v1")).toBeInTheDocument());

    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});
