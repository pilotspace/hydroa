/**
 * device-activate-page.test.tsx — RED suite for the /activate approval surface
 * (device-activate-page §2 SCENARIOS, §4 TESTS).
 *
 * AuthorizationSeal: state by icon+text (not color alone) with sr-only final-state copy
 * (M4, M10). ActivationCard: auto-previews a prefilled code over the BFF (code in the POST
 * body), shows scope + expiry + default-budget, wires Approve/Deny to the frozen endpoints,
 * and collapses a not-previewable 404 to ONE generic message (M2, M3, M4, M7 — no oracle).
 *
 * TRUE-RED REASON: @/components/agent-activation/* do not exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { AuthorizationSeal } from "@/components/agent-activation/AuthorizationSeal";
import { ActivationCard } from "@/components/agent-activation/ActivationCard";

const GW = "http://localhost:3000/api/gw/oauth/device";

const PENDING_FACTS = {
  scope: "proxy",
  status: "pending" as const,
  expires_in: 540,
  interval: 5,
  default_budget_usd: "100.00",
};

// ---------------------------------------------------------------------------
// AuthorizationSeal — state conveyed by icon + text, not color alone (M10)
// ---------------------------------------------------------------------------

describe("AuthorizationSeal", () => {
  it.each([
    ["pending", "Pending"],
    ["granted", "Granted"],
    ["denied", "Denied"],
    ["expired", "Expired"],
  ])("renders the %s state with a visible text label (not color alone)", (state, label) => {
    render(<AuthorizationSeal status={state as "pending" | "granted" | "denied" | "expired"} />);
    // Exact visible label — the badge also carries an aria-hidden icon + sr-only copy.
    expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
  });

  it("marks a granted authorization final for screen readers (icon+text, not color)", () => {
    render(<AuthorizationSeal status="granted" />);
    // sr-only copy names the final state (translated InvoiceStatusSeal idiom).
    expect(screen.getByText(/this authorization is final/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ActivationCard — auto-preview a prefilled pending code (M2, M3)
// ---------------------------------------------------------------------------

describe("ActivationCard", () => {
  it("auto-previews a prefilled code and shows scope + expiry + default budget", async () => {
    let previewBody: unknown = null;
    server.use(
      http.post(`${GW}/preview`, async ({ request }) => {
        previewBody = await request.json();
        return HttpResponse.json(PENDING_FACTS);
      }),
    );

    render(<ActivationCard initialUserCode="BCDF-GHJK" />);

    await waitFor(() => expect(screen.getByText(/proxy/i)).toBeInTheDocument());
    // The default budget cap is shown, labeled a system default (not agent-specific).
    expect(screen.getByText(/100\.00/)).toBeInTheDocument();
    expect(screen.getByText(/system default/i)).toBeInTheDocument();
    // The code went in the POST body, never a URL.
    expect(previewBody).toEqual({ user_code: "BCDF-GHJK" });
  });

  it("approves over the frozen endpoint and shows a granted seal (M4)", async () => {
    let approveBody: unknown = null;
    server.use(
      http.post(`${GW}/preview`, () => HttpResponse.json(PENDING_FACTS)),
      http.post(`${GW}/approve`, async ({ request }) => {
        approveBody = await request.json();
        return HttpResponse.json({ status: "approved" });
      }),
    );

    render(<ActivationCard initialUserCode="BCDF-GHJK" />);
    await waitFor(() => expect(screen.getByText(/proxy/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(screen.getByText("Granted", { exact: true })).toBeInTheDocument());
    expect(approveBody).toEqual({ user_code: "BCDF-GHJK" });
  });

  it("denies over the frozen endpoint and shows a denied seal (M4)", async () => {
    server.use(
      http.post(`${GW}/preview`, () => HttpResponse.json(PENDING_FACTS)),
      http.post(`${GW}/deny`, () => HttpResponse.json({ status: "denied" })),
    );

    render(<ActivationCard initialUserCode="BCDF-GHJK" />);
    await waitFor(() => expect(screen.getByText(/proxy/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /deny/i }));

    await waitFor(() => expect(screen.getByText("Denied", { exact: true })).toBeInTheDocument());
  });

  it("shows ONE generic message for a not-previewable code (no oracle, M7)", async () => {
    server.use(
      http.post(`${GW}/preview`, () =>
        HttpResponse.json({ error: "authorization_not_previewable" }, { status: 404 }),
      ),
    );

    render(<ActivationCard initialUserCode="ZZZZ-ZZZZ" />);

    await waitFor(() =>
      expect(screen.getByText(/invalid or (has )?expired/i)).toBeInTheDocument(),
    );
    // No server-known facts leak for a non-pending code.
    expect(screen.queryByText(/proxy/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("normalizes a loosely typed code before previewing (M2)", async () => {
    let previewBody: unknown = null;
    server.use(
      http.post(`${GW}/preview`, async ({ request }) => {
        previewBody = await request.json();
        return HttpResponse.json(PENDING_FACTS);
      }),
    );

    render(<ActivationCard />);
    const input = screen.getByLabelText("Device code");
    await userEvent.type(input, "  bcdf ghjk ");
    await userEvent.click(screen.getByRole("button", { name: /continue|preview|check/i }));

    await waitFor(() => expect(previewBody).toEqual({ user_code: "BCDF-GHJK" }));
  });

  it("renders an accessible labeled code input with a visible submit control", () => {
    render(<ActivationCard />);
    expect(screen.getByLabelText("Device code")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /continue|preview|check/i }),
    ).toBeInTheDocument();
  });
});
