/**
 * tests/routing-edit.test.tsx — RED → GREEN suite for v32 routing-config-editor.
 *
 * RoutingPage gains an owner/admin-only editor section that reads the existing
 * GET /admin/routing response (routing_strategy + deployments fields) and writes
 * PUT /admin/routing with { routing_strategy, model_groups: object-form rows }.
 *
 * Mirrors the catalog-sync.test.tsx harness exactly:
 *   - same-origin msw via tests/mocks/server
 *   - me(role) helper for /api/auth/me
 *   - QueryClientProvider wrapper with retry:false
 *   - userEvent for interactions
 *   - captured PUT body for assertion
 *
 * RED before Build: RoutingEditor.tsx does not exist yet → import of RoutingPage
 * (which will re-export it) throws / the editor section renders nothing.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { RoutingPage } from "@/components/routing/RoutingPage";

const APP = "http://localhost:3000";

// ── helpers ──────────────────────────────────────────────────────────────────

function me(role: string) {
  return http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "u-1",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      email: `${role}@acme.io`,
      role,
      exp: Math.floor(Date.now() / 1000) + 86400,
    }),
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

function renderRouting() {
  return render(<RoutingPage />, { wrapper: Wrapper });
}

// ── base GET response that satisfies BOTH the health cards AND the editor ────
// The frozen contract adds `routing_strategy` and `deployments` to what the
// read-only health page already consumed (retry_policy, cooldown, model_groups,
// candidates). Tests provide them all so the health cards don't crash.
const BASE_ROUTING = {
  routing_strategy: "ordered" as const,
  retry_policy: { max_retries: 3, backoff_base_s: 0.5 },
  cooldown: { enabled: true, threshold: 3, ttl_s: 60, window_s: 120 },
  model_groups: { gpt: ["a"] },
  candidates: [],
  deployments: {
    gpt: [{ model_id: "a", weight: 1, rpm_limit: null, tpm_limit: null }],
  },
};

// ── tests ─────────────────────────────────────────────────────────────────────

describe("RoutingPage — routing-config-editor (v32)", () => {
  const user = userEvent.setup();

  // 1. Owner sees the editor pre-filled with the GET response values.
  it("test_owner_editor_prefilled", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
    );

    renderRouting();

    // Strategy select is pre-filled with "ordered"
    const strategySelect = await screen.findByLabelText(/routing strategy/i);
    expect((strategySelect as HTMLSelectElement).value).toBe("ordered");

    // The gpt group row shows model_id "a"
    expect(screen.getByDisplayValue("a")).toBeInTheDocument();
  });

  // 2. Owner edits strategy + weight, saves → PUT body is correct, GET refetched.
  it("test_owner_edit_and_save", async () => {
    let capturedBody: unknown = null;
    let getCalls = 0;

    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => {
        getCalls++;
        return HttpResponse.json(BASE_ROUTING);
      }),
      http.put(`${APP}/api/gw/admin/routing`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ...BASE_ROUTING, routing_strategy: "simple-shuffle" });
      }),
    );

    renderRouting();

    // Wait for editor to be rendered (owner role loaded)
    const strategySelect = await screen.findByLabelText(/routing strategy/i);
    const getCallsBefore = getCalls;

    // Change strategy to simple-shuffle
    await user.selectOptions(strategySelect, "simple-shuffle");

    // Change weight for the "a" model in the "gpt" group
    const weightInput = screen.getByLabelText(/weight.*gpt.*1|gpt.*weight.*1/i);
    await user.clear(weightInput);
    await user.type(weightInput, "5");

    // Click Save
    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    // PUT body must have the new values
    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    const body = capturedBody as Record<string, unknown>;
    expect(body.routing_strategy).toBe("simple-shuffle");

    const groups = body.model_groups as Record<string, Array<{ model_id: string; weight: number }>>;
    expect(groups.gpt[0].weight).toBe(5);

    // GET is refetched after save (invalidateQueries)
    await waitFor(() => {
      expect(getCalls).toBeGreaterThan(getCallsBefore);
    });
  });

  // 3. Owner adds a new alias group "fast" with model "b", saves → object-form body.
  it("test_owner_add_alias_and_save", async () => {
    let capturedBody: unknown = null;

    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(BASE_ROUTING);
      }),
    );

    renderRouting();

    // Wait for editor
    await screen.findByLabelText(/routing strategy/i);

    // Add a new group alias
    const aliasInput = screen.getByLabelText(/new alias/i);
    await user.clear(aliasInput);
    await user.type(aliasInput, "fast");

    const addGroupBtn = screen.getByRole("button", { name: /add group/i });
    await user.click(addGroupBtn);

    // The new "fast" group appears — type model_id for its first (auto-added) deployment row
    const modelIdInput = screen.getByLabelText(/model.*fast.*1|fast.*model.*1/i);
    await user.type(modelIdInput, "b");

    // Save
    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    const body = capturedBody as Record<string, unknown>;
    const groups = body.model_groups as Record<
      string,
      Array<{ model_id: string; weight: number; rpm_limit: number | null; tpm_limit: number | null }>
    >;

    // Object-form row (not bare string) — weights/limits not silently dropped
    expect(groups.fast).toBeDefined();
    expect(typeof groups.fast[0]).toBe("object");
    // Full object-form row with the default weight (1) — proves no bare-string regression
    // and that the default weight is a valid positive int, not 0/NaN.
    expect(groups.fast[0]).toMatchObject({
      model_id: "b",
      weight: 1,
    });
    expect(groups.fast[0]).toHaveProperty("rpm_limit");
    expect(groups.fast[0]).toHaveProperty("tpm_limit");
  });

  // 4. Restart notice is visible in the editor.
  it("test_restart_notice_visible", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
    );

    renderRouting();

    await screen.findByLabelText(/routing strategy/i);

    // A notice matching /restart/i must be present
    expect(screen.getByText(/restart/i)).toBeInTheDocument();
  });

  // 5. PUT 422 shows inline error; form is preserved (user's edited value stays).
  it("test_save_422_inline_error_form_preserved", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Invalid routing configuration",
            status: 422,
            code: "ERR_ROUTING_CONFIG_INVALID",
            detail: "UNKNOWN_ROUTING_STRATEGY",
          },
          { status: 422 },
        ),
      ),
    );

    renderRouting();

    // Wait for editor and change strategy so there's a user-visible edit
    const strategySelect = await screen.findByLabelText(/routing strategy/i);
    await user.selectOptions(strategySelect, "simple-shuffle");

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    // Inline error must surface the underlying validator CODE from `detail`
    // (actionable — the operator sees WHICH rule failed, not just a generic title).
    await waitFor(() => {
      expect(screen.getByText(/UNKNOWN_ROUTING_STRATEGY/)).toBeInTheDocument();
    });

    // Form is NOT cleared — user's edited value is still present
    expect((screen.getByLabelText(/routing strategy/i) as HTMLSelectElement).value).toBe(
      "simple-shuffle",
    );
  });

  // 6. Member sees no editor (no Save button, no strategy select).
  it("test_member_no_editor", async () => {
    server.use(
      me("member"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
    );

    renderRouting();

    // Wait for the page to settle (health cards render for members too)
    await waitFor(() => {
      // The health-read page title should render
      expect(screen.getByText(/routing health/i)).toBeInTheDocument();
    });

    // No editor controls visible
    expect(screen.queryByLabelText(/routing strategy/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
  });
});

// ── v37 routing-editor-feedback: client guard + post-save restart affordance ──
describe("RoutingEditor — feedback + validation (v37)", () => {
  const user = userEvent.setup();

  it("test_routing_zero_weight_blocked", async () => {
    let putHit = false;
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () => {
        putHit = true;
        return HttpResponse.json(BASE_ROUTING);
      }),
    );

    renderRouting();

    const weightInput = await screen.findByLabelText(/weight.*gpt.*1|gpt.*weight.*1/i);
    await user.clear(weightInput); // empty -> coerced to 0
    await user.click(screen.getByRole("button", { name: /save/i }));

    // inline client error, no PUT round-trip
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/weight greater than 0/i);
    expect(putHit).toBe(false);
  });

  it("test_routing_blank_model_blocked", async () => {
    let putHit = false;
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () => {
        putHit = true;
        return HttpResponse.json(BASE_ROUTING);
      }),
    );

    renderRouting();

    const modelInput = await screen.findByDisplayValue("a");
    await user.clear(modelInput); // blank model_id
    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/model and a weight/i);
    expect(putHit).toBe(false);
  });

  it("test_routing_guard_error_clears_then_saves", async () => {
    let putHit = false;
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () => {
        putHit = true;
        return HttpResponse.json(BASE_ROUTING);
      }),
    );

    renderRouting();

    // 1. zero weight → blocked, no PUT, inline error
    const weightInput = await screen.findByLabelText(/weight.*gpt.*1|gpt.*weight.*1/i);
    await user.clear(weightInput);
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(putHit).toBe(false);

    // 2. fix the weight → the client error clears as soon as editing resumes
    await user.type(weightInput, "3");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());

    // 3. save again → PUT fires and the restart affordance shows
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/restart/i);
    expect(putHit).toBe(true);
  });

  it("test_routing_valid_save_shows_restart", async () => {
    let putHit = false;
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () => {
        putHit = true;
        return HttpResponse.json(BASE_ROUTING);
      }),
    );

    renderRouting();

    // BASE_ROUTING is already valid (weight 1, model "a") — just save.
    await screen.findByLabelText(/routing strategy/i);
    await user.click(screen.getByRole("button", { name: /save/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/restart/i);
    expect(putHit).toBe(true);
  });

  it("test_routing_saved_clears_on_edit", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
    );

    renderRouting();

    const strategySelect = await screen.findByLabelText(/routing strategy/i);
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("status")).toBeInTheDocument();

    // editing clears the transient saved affordance; the static notice stays
    await user.selectOptions(strategySelect, "simple-shuffle");
    await waitFor(() => {
      expect(screen.queryByRole("status")).toBeNull();
    });
    expect(screen.getByText(/changes take effect on next restart/i)).toBeInTheDocument();
  });

  it("test_routing_server_422_no_saved", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/routing`, () => HttpResponse.json(BASE_ROUTING)),
      http.put(`${APP}/api/gw/admin/routing`, () =>
        HttpResponse.json(
          { title: "Invalid routing config", status: 422, detail: "INVALID_DEPLOYMENT_WEIGHT" },
          { status: 422 },
        ),
      ),
    );

    renderRouting();

    // client-valid body, but the server rejects → mutError, no saved affordance
    await screen.findByLabelText(/routing strategy/i);
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/INVALID_DEPLOYMENT_WEIGHT/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
