/**
 * tests-bff/model-mgmt.test.tsx — RED suite for v15 task 2 (model-management-ui).
 *
 * Surface: a NEW owner/admin `/models` page that lists catalog models and toggles
 * each enabled/disabled per tenant via the EXISTING gateway endpoints
 *   GET /admin/models            -> { object:"list", data: AdminModelItem[] }
 *   PUT /admin/models/{id:path}  body { enabled: boolean } -> AdminModelItem
 * consumed through the BFF seam (bffGet/bffPut). Owner/admin-only: a member's GET
 * 403s, surfaced as an ErrorState (role=alert) carrying the BFF error title.
 *
 * RED before build: `@/components/models/ModelsPage` does not exist, so this whole
 * file fails at collect with MODULE_NOT_FOUND — the established true-red convention
 * (see govern.test.tsx:38-48). When ModelsPage exists, the remaining failures are
 * BEHAVIORAL (wrong field name, unencoded id path, missing refetch, etc.).
 *
 * Runs in the "bff" vitest project (msw same-origin handlers, server.use overrides).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse, delay } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/models/ModelsPage.tsx ───────
import { ModelsPage } from "@/components/models/ModelsPage";
// AppShell already exists (v13); the nav assertion is RED until /models is added.
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

const GPT4O = {
  id: "openai/gpt-4o",
  name: "GPT-4o",
  context_length: 128000,
  enabled: true,
  // region-catalog-dimension TASK.md §3 FROZEN: region is NOT NULL on the real
  // backend (models.region defaults 'global') — these pre-existing fixtures predate
  // that column; added here so RegionBadge (residency-tiers-ui M6) never sees undefined.
  region: "global",
};
const CLAUDE = {
  id: "anthropic/claude-3.5-sonnet",
  name: "Claude 3.5 Sonnet",
  context_length: 200000,
  enabled: false,
  region: "global",
};

function listResponse(models: unknown[]) {
  return { object: "list", data: models };
}

// ── list + state patterns ──────────────────────────────────────────────────────
describe("ModelsPage — list + four states", () => {
  it("test_owner_lists_models", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([GPT4O, CLAUDE])),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    // both model names + ids appear
    expect(await screen.findByText("GPT-4o")).toBeInTheDocument();
    expect(screen.getByText("Claude 3.5 Sonnet")).toBeInTheDocument();
    expect(screen.getByText(/openai\/gpt-4o/)).toBeInTheDocument();
    expect(screen.getByText(/anthropic\/claude-3\.5-sonnet/)).toBeInTheDocument();

    // each row's Switch aria-checked reflects its enabled flag
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(2);
    const gpt = screen.getByRole("switch", { name: /gpt-4o/i });
    const claude = screen.getByRole("switch", { name: /claude 3\.5 sonnet/i });
    expect(gpt).toHaveAttribute("aria-checked", "true");
    expect(claude).toHaveAttribute("aria-checked", "false");
  });

  it("test_loading_then_empty", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([]))),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    // loading shows first (role=status), before the query resolves
    expect(screen.getByRole("status")).toBeInTheDocument();

    // then the empty state — and NO Switch is rendered
    expect(await screen.findByText(/no models/i)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("test_member_forbidden_shows_error", async () => {
    // owner/admin-only surface: BOTH GET and PUT require owner/admin on the gateway
    // (router.py:118,161), so a member's GET 403s. The gate is server-authoritative —
    // the surface deliberately does NOT add a redundant client useCurrentUser check
    // (it would be untestable dead code; §1 itself requires the BFF error TITLE, which
    // only the real 403 carries). A member therefore sees the standard ErrorState.
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(
          { title: "Forbidden", status: 403, code: "ERR_AUTH_FORBIDDEN" },
          { status: 403 },
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/forbidden/i);
    // no list, no Switch fabricated
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

// ── toggle round-trips the EXACT contract body ──────────────────────────────────
describe("ModelsPage — toggle persists exact contract body", () => {
  it("test_toggle_off_puts_exact_body", async () => {
    const user = userEvent.setup();
    let getCount = 0;
    let putBody: Record<string, unknown> | null = null;
    let putUrl: string | null = null;

    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => {
        getCount += 1;
        return HttpResponse.json(listResponse([GPT4O]));
      }),
      http.put(`${APP}/api/gw/admin/models/:model_id`, async ({ request }) => {
        putUrl = request.url;
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...GPT4O, enabled: false });
      }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    await user.click(sw);

    await waitFor(() => expect(putBody).not.toBeNull());
    // EXACT field + value — no extra keys, correct field name
    expect(putBody).toEqual({ enabled: false });
    // slash-id encoded in the PUT path
    expect(putUrl).toMatch(/\/api\/gw\/admin\/models\/openai%2Fgpt-4o$/);
    // refetch after invalidate: GET hit at least twice
    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2));
  });

  it("test_toggle_on_puts_true", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    let putUrl: string | null = null;

    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([CLAUDE]))),
      http.put(`${APP}/api/gw/admin/models/:model_id`, async ({ request }) => {
        putUrl = request.url;
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...CLAUDE, enabled: true });
      }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /claude 3\.5 sonnet/i });
    await user.click(sw);

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({ enabled: true });
    expect(putUrl).toMatch(/\/admin\/models\/anthropic%2Fclaude-3\.5-sonnet$/);
  });

  it("test_toggle_put_404_shows_error", async () => {
    // §3 contract: PUT 404 ERR_MODEL_NOT_FOUND -> surface as ErrorState (mutation error).
    // A failed toggle must NOT fail silently — the user gets a role=alert with the title.
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([GPT4O]))),
      http.put(`${APP}/api/gw/admin/models/:model_id`, () =>
        HttpResponse.json(
          { title: "Model not found", status: 404, code: "ERR_MODEL_NOT_FOUND" },
          { status: 404 },
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    await user.click(sw);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/model not found/i);
    // the list is still shown (query succeeded); the failed toggle did not flip server state
    expect(screen.getByRole("switch", { name: /gpt-4o/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("test_switch_disabled_during_mutation", async () => {
    // §3 contract: disabled={mutation.isPending && pendingId===m.id}. The toggled
    // Switch is disabled while its PUT is in flight, then re-enabled when it settles.
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([GPT4O]))),
      http.put(`${APP}/api/gw/admin/models/:model_id`, async () => {
        await delay(50);
        return HttpResponse.json({ ...GPT4O, enabled: false });
      }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    expect(sw).not.toBeDisabled();
    await user.click(sw);

    // in-flight: the row's Switch is disabled
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /gpt-4o/i })).toBeDisabled(),
    );
    // settled: re-enabled
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /gpt-4o/i })).not.toBeDisabled(),
    );
  });
});

// ── a11y + keyboard ─────────────────────────────────────────────────────────────
describe("ModelsPage — axe + keyboard operable", () => {
  it("test_models_axe_and_keyboard", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;

    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([GPT4O]))),
      http.put(`${APP}/api/gw/admin/models/:model_id`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...GPT4O, enabled: false });
      }),
    );
    const { container } = render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    // every Switch has an accessible name
    expect(sw).toHaveAccessibleName();
    // populated container is axe-clean (serious/critical)
    expect(await axeSeriousCritical(container)).toEqual([]);

    // keyboard-operable: focus + Space toggles
    sw.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(putBody).toEqual({ enabled: false }));
  });
});

// ── nav + design-system consumption ─────────────────────────────────────────────
describe("ModelsPage — nav entry + shared primitive", () => {
  it("test_nav_exposes_models", () => {
    render(
      <AppShell activePath="/app/models">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    const link = within(nav).getByRole("link", { name: /models/i });
    expect(link).toHaveAttribute("href", "/app/models");
    expect(link).toHaveAttribute("aria-current", "page");
  });

  it("test_toggle_is_shared_switch_primitive", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([GPT4O]))),
    );
    render(<ModelsPage />, { wrapper: Wrapper });
    // the design-system Switch renders as role="switch" (a <button>), not an ad-hoc checkbox
    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    expect(sw.tagName).toBe("BUTTON");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

// ── Region column + residency ineligibility (residency-tiers-ui TASK.md §3 M6-M8) ──
//
// GET /admin/residency-policy is fetched independently by ModelsPage (own useQuery,
// never blocking the base table render — M8). A default {region:null,...} INITIAL
// handler lives in mocks/handlers.ts (unconditional query, same idiom as the
// impersonation/catalog-models defaults) — the tests above (list/toggle/a11y) rely
// on that default implicitly; tests below override per scenario.
const CLAUDE_EU = { id: "eu.anthropic/claude-opus-4", name: "Claude Opus 4 (EU)", context_length: 200000, enabled: true, region: "eu" };
const CLAUDE_US = { id: "us.anthropic/claude-opus-4", name: "Claude Opus 4 (US)", context_length: 200000, enabled: true, region: "us" };
const GPT4O_GLOBAL = { id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000, enabled: true, region: "global" };
const TITAN_AP = { id: "apac.amazon/titan", name: "Titan (AP)", context_length: 32000, enabled: false, region: "ap" };

function residencyGet(body: unknown = { region: null, updated_at: null }) {
  return http.get(`${APP}/api/gw/admin/residency-policy`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));
}

describe("ModelsPage — Region column", () => {
  it("test_region_badge_renders_per_row", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([CLAUDE_EU, CLAUDE_US, GPT4O_GLOBAL, TITAN_AP])),
      ),
      residencyGet(),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Claude Opus 4 (EU)");
    expect(screen.getByText("EU")).toBeInTheDocument();
    expect(screen.getByText("US")).toBeInTheDocument();
    expect(screen.getByText("GLOBAL")).toBeInTheDocument();
    expect(screen.getByText("AP")).toBeInTheDocument();
  });
});

describe("ModelsPage — pinned-tenant ineligibility (M7)", () => {
  it("test_ineligible_row_dimmed_disabled_badged_never_removed", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([CLAUDE_EU, CLAUDE_US])),
      ),
      residencyGet({ region: "eu", updated_at: "2026-07-01T00:00:00Z" }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Claude Opus 4 (EU)");
    // eligible row: enabled Switch stays interactive, no ineligibility badge
    const euSwitch = screen.getByRole("switch", { name: /claude opus 4 \(eu\)/i });
    expect(euSwitch).not.toBeDisabled();

    // ineligible row: Switch disabled, warning badge, row still present
    const usSwitch = await screen.findByRole("switch", { name: /claude opus 4 \(us\)/i });
    expect(usSwitch).toBeDisabled();
    expect(screen.getByText("Ineligible in EU")).toBeInTheDocument();
    expect(screen.getByText("Claude Opus 4 (US)")).toBeInTheDocument();
  });

  it("test_global_region_never_satisfies_a_specific_pin", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse([GPT4O_GLOBAL]))),
      residencyGet({ region: "us", updated_at: "2026-07-01T00:00:00Z" }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    const sw = await screen.findByRole("switch", { name: /gpt-4o/i });
    expect(sw).toBeDisabled();
    expect(screen.getByText("Ineligible in US")).toBeInTheDocument();
  });

  it("test_unpinned_tenant_zero_rows_dimmed", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([CLAUDE_EU, CLAUDE_US, GPT4O_GLOBAL])),
      ),
      residencyGet({ region: null, updated_at: null }),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Claude Opus 4 (EU)");
    expect(screen.queryByText(/ineligible in/i)).not.toBeInTheDocument();
    for (const sw of screen.getAllByRole("switch")) {
      expect(sw).not.toBeDisabled();
    }
  });
});

describe("ModelsPage — residency-read degrade (M8)", () => {
  it("test_residency_read_failure_degrades_gracefully_table_stays_usable", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([CLAUDE_EU, CLAUDE_US])),
      ),
      http.get(`${APP}/api/gw/admin/residency-policy`, () =>
        HttpResponse.json({ title: "Server error", status: 500 }, { status: 500 }),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    // Region badges still render...
    await screen.findByText("Claude Opus 4 (EU)");
    expect(screen.getByText("EU")).toBeInTheDocument();
    expect(screen.getByText("US")).toBeInTheDocument();
    // ...but no row is dimmed/disabled/badged as ineligible, and no page-level error.
    expect(screen.queryByText(/ineligible in/i)).not.toBeInTheDocument();
    for (const sw of screen.getAllByRole("switch")) {
      expect(sw).not.toBeDisabled();
    }
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ModelsPage — Region/ineligibility axe (M12)", () => {
  it("test_region_and_ineligibility_axe_clean", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(listResponse([CLAUDE_EU, CLAUDE_US])),
      ),
      residencyGet({ region: "eu", updated_at: "2026-07-01T00:00:00Z" }),
    );
    const { container } = render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Ineligible in EU");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});
