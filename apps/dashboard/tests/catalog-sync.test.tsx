/**
 * tests/catalog-sync.test.tsx — RED suite for the catalog-sync-trigger button on /models.
 *
 * ModelsPage gains an owner/admin-only "Re-sync catalog" button that POSTs to
 * /admin/catalog/sync (via the BFF catch-all), shows the returned last-sync time, refetches
 * the model list, and surfaces an error on failure. Members never see the button.
 *
 * RED before Build: ModelsPage has no Re-sync button yet → getByRole("button", {name:/re-sync/i})
 * throws (BEHAVIORAL-RED against the current component).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { ModelsPage } from "@/components/models/ModelsPage";

const APP = "http://localhost:3000";

const MODELS_RESPONSE = {
  object: "list",
  data: [
    { id: "anthropic/claude-opus-4", name: "Claude Opus 4", context_length: 200000, enabled: true },
  ],
};

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

function renderModels() {
  return render(<ModelsPage />, { wrapper: Wrapper });
}

describe("ModelsPage — catalog re-sync", () => {
  const user = userEvent.setup();

  it("test_owner_resync_success_shows_time", async () => {
    let syncCalls = 0;
    let modelsCalls = 0;
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/models`, () => {
        modelsCalls++;
        return HttpResponse.json(MODELS_RESPONSE);
      }),
      http.post(`${APP}/api/gw/admin/catalog/sync`, () => {
        syncCalls++;
        return HttpResponse.json({ synced: 2, synced_at: "2026-06-23T10:30:00+00:00" });
      }),
    );

    renderModels();

    const button = await screen.findByRole("button", { name: /re-sync catalog/i });
    const modelsCallsBefore = modelsCalls;
    await user.click(button);

    await waitFor(() => {
      expect(syncCalls).toBe(1);
    });
    // last-sync time shown
    await waitFor(() => {
      expect(screen.getByText(/last synced/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/2026-06-23T10:30:00/)).toBeInTheDocument();
    // model list refetched (invalidate ["admin-models"])
    await waitFor(() => {
      expect(modelsCalls).toBeGreaterThan(modelsCallsBefore);
    });
  });

  it("test_resync_error_surfaces", async () => {
    server.use(
      me("owner"),
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(MODELS_RESPONSE)),
      http.post(`${APP}/api/gw/admin/catalog/sync`, () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Upstream catalog source unavailable",
            status: 502,
            code: "ERR_UPSTREAM_UNAVAILABLE",
          },
          { status: 502 },
        ),
      ),
    );

    renderModels();

    const button = await screen.findByRole("button", { name: /re-sync catalog/i });
    await user.click(button);

    await waitFor(() => {
      expect(screen.getByText(/upstream catalog source unavailable/i)).toBeInTheDocument();
    });
    // no last-sync time on failure
    expect(screen.queryByText(/last synced/i)).not.toBeInTheDocument();
  });

  it("test_member_no_resync_button", async () => {
    server.use(
      me("member"),
      http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(MODELS_RESPONSE)),
    );

    renderModels();

    // model list renders (so the page is mounted) but the re-sync button is absent for members
    await waitFor(() => {
      expect(screen.getByText("Claude Opus 4")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /re-sync catalog/i }),
    ).not.toBeInTheDocument();
  });
});
