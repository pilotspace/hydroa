/**
 * tests/domain-claims-console.test.tsx — RED suite for the domain-claims-console
 * TASK.md §3 CONTRACT (FROZEN @ v1). Covers the console body
 * `components/settings/DomainClaimsSettings.tsx` consuming the frozen gateway
 * `/admin/domain-claims` API via the existing BFF pass-through
 * (bffGet/bffPost/bffDelete -> /api/gw/admin/domain-claims*).
 *
 * Every assertion is OBSERVABLE (rendered text, role, which fetch was/wasn't
 * called) — never component internals. The seal states are DERIVED
 * client-side per §3 `sealState(claim)`:
 *   status=="verified"                        -> "Verified"
 *   status=="pending" && expires_at > now      -> "Pending DNS"
 *   status=="pending" && expires_at <= now     -> "Expired"
 * A failed verify (422/503) is EPHEMERAL — the claim stays pending, never a
 * seal state of its own (R2).
 *
 * RED failure mode: `@/components/settings/DomainClaimsSettings` does not exist
 * yet -> the import fails at collect (MODULE_NOT_FOUND), the established
 * true-red convention in this repo (see tests-bff/compliance-report-center.test.tsx,
 * tests/billing-plan.test.tsx).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/settings/DomainClaimsSettings.tsx ──
import { DomainClaimsSettings } from "@/components/settings/DomainClaimsSettings";

const APP = "http://localhost:3000";
const CLAIMS_URL = `${APP}/api/gw/admin/domain-claims`;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

function renderConsole() {
  return render(<DomainClaimsSettings />, { wrapper: Wrapper });
}

const FUTURE = "2099-01-01T00:00:00Z";
const PAST = "2020-01-01T00:00:00Z";

interface ClaimItem {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
  verified_at: string | null;
}

function makeClaim(overrides: Partial<ClaimItem> = {}): ClaimItem {
  return {
    claim_id: "claim-1",
    domain: "acme.com",
    status: "pending",
    dns_record_name: "_ai-proxy-challenge.acme.com",
    dns_record_value: "ai-proxy-domain-verification=tok-acme",
    expires_at: FUTURE,
    verified_at: null,
    ...overrides,
  };
}

function claimsGet(claims: ClaimItem[]) {
  return http.get(CLAIMS_URL, () => HttpResponse.json({ claims }));
}

describe("DomainClaimsSettings — list + seals (M1, M2)", () => {
  it("test_owner_sees_claims_with_seals", async () => {
    // covers: M1, M2
    const verified = makeClaim({
      claim_id: "claim-verified",
      domain: "acme.com",
      status: "verified",
      verified_at: "2026-07-01T00:00:00Z",
    });
    const pending = makeClaim({
      claim_id: "claim-pending",
      domain: "acme.dev",
      status: "pending",
      expires_at: FUTURE,
    });
    server.use(claimsGet([verified, pending]));

    const { container } = renderConsole();

    expect(await screen.findByText("acme.com")).toBeInTheDocument();
    expect(screen.getByText("acme.dev")).toBeInTheDocument();

    // Verified seal — reuses the InvoiceStatusSeal immutability-marker idiom:
    // a visible "Verified" label, an sr-only ownership assertion, and a lock icon.
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText(/ownership/i)).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeTruthy();

    // Pending claim renders the distinct "Pending DNS" seal, not "Verified".
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
  });

  it("test_expired_seal_derived", async () => {
    // covers: M2
    const expiredPending = makeClaim({
      claim_id: "claim-expired",
      domain: "acme.stale",
      status: "pending",
      expires_at: PAST,
    });
    server.use(claimsGet([expiredPending]));

    renderConsole();

    expect(await screen.findByText("acme.stale")).toBeInTheDocument();
    // client-DERIVED from status+expires_at — NOT the raw "pending" status label.
    expect(await screen.findByText("Expired")).toBeInTheDocument();
    expect(screen.queryByText("Pending DNS")).not.toBeInTheDocument();
  });
});

describe("DomainClaimsSettings — create reveals the DNS-TXT challenge (M3)", () => {
  it("test_create_reveals_dns_challenge", async () => {
    // covers: M3
    const user = userEvent.setup();
    server.use(
      claimsGet([]),
      http.post(CLAIMS_URL, () =>
        HttpResponse.json(
          {
            claim_id: "claim-new",
            domain: "team.acme.com",
            status: "pending",
            dns_record_type: "TXT",
            dns_record_name: "_ai-proxy-challenge.team.acme.com",
            dns_record_value: "ai-proxy-domain-verification=tok-fresh-123",
            expires_at: FUTURE,
          },
          { status: 201 },
        ),
      ),
    );

    renderConsole();
    await screen.findByLabelText(/^domain$/i);

    await user.type(screen.getByLabelText(/^domain$/i), "team.acme.com");
    await user.click(screen.getByRole("button", { name: /^add domain$/i }));

    expect(await screen.findByText("_ai-proxy-challenge.team.acme.com")).toBeInTheDocument();
    expect(screen.getByText("ai-proxy-domain-verification=tok-fresh-123")).toBeInTheDocument();
    expect(screen.getByText("TXT")).toBeInTheDocument();
    // per-field copy affordances — at least one for the record name, one for the value.
    expect(screen.getAllByRole("button", { name: /copy/i }).length).toBeGreaterThanOrEqual(2);
  });
});

describe("DomainClaimsSettings — verify now (M4, R2)", () => {
  it("test_verify_now_success_flips_seal", async () => {
    // covers: M4
    const user = userEvent.setup();
    const pending = makeClaim({ claim_id: "claim-v1", domain: "verify-ok.com", status: "pending" });
    server.use(
      claimsGet([pending]),
      http.post(`${CLAIMS_URL}/claim-v1/verify`, () =>
        HttpResponse.json({
          claim_id: "claim-v1",
          domain: "verify-ok.com",
          status: "verified",
          verified_at: "2026-07-19T00:00:00Z",
        }),
      ),
    );

    renderConsole();
    await screen.findByText("verify-ok.com");
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /verify now/i }));

    await waitFor(() => expect(screen.getByText("Verified")).toBeInTheDocument());
    expect(screen.queryByText("Pending DNS")).not.toBeInTheDocument();
  });

  it("test_verify_mismatch_alert_no_flip", async () => {
    // covers: M4, R2
    const user = userEvent.setup();
    const pending = makeClaim({ claim_id: "claim-v2", domain: "mismatch.com", status: "pending" });
    let deleteCalled = false;
    server.use(
      claimsGet([pending]),
      http.post(`${CLAIMS_URL}/claim-v2/verify`, () =>
        problem("DNS record not found or does not match", 422, "ERR_DOMAIN_VERIFICATION_FAILED"),
      ),
      http.delete(`${CLAIMS_URL}/claim-v2`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderConsole();
    await screen.findByText("mismatch.com");

    await user.click(screen.getByRole("button", { name: /verify now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/absent|mismatch/i);
    // distinct from the 503 lookup-failure copy (never collapsed together)
    expect(alert).not.toHaveTextContent(/dns lookup failed/i);

    // R2: the claim is not verified, deleted, or otherwise mutated.
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
    expect(deleteCalled).toBe(false);
  });

  it("test_verify_dns_lookup_distinct_alert", async () => {
    // covers: M4
    const user = userEvent.setup();
    const pending = makeClaim({ claim_id: "claim-v3", domain: "flaky-dns.com", status: "pending" });
    server.use(
      claimsGet([pending]),
      http.post(`${CLAIMS_URL}/claim-v3/verify`, () =>
        problem("DNS lookup failed", 503, "ERR_DNS_LOOKUP_FAILED"),
      ),
    );

    renderConsole();
    await screen.findByText("flaky-dns.com");

    await user.click(screen.getByRole("button", { name: /verify now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/dns lookup failed|try again/i);
    // distinct from the 422 mismatch copy (never collapsed together)
    expect(alert).not.toHaveTextContent(/mismatch/i);

    // the claim is untouched — still pending, no verified flip.
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });
});

describe("DomainClaimsSettings — revoke (M5)", () => {
  it("test_revoke_removes_row", async () => {
    // covers: M5
    const user = userEvent.setup();
    const verified = makeClaim({
      claim_id: "claim-revoke",
      domain: "goodbye.com",
      status: "verified",
      verified_at: "2026-07-01T00:00:00Z",
    });
    let listCallCount = 0;
    server.use(
      http.get(CLAIMS_URL, () => {
        listCallCount += 1;
        return HttpResponse.json({ claims: listCallCount === 1 ? [verified] : [] });
      }),
      http.delete(`${CLAIMS_URL}/claim-revoke`, () => new HttpResponse(null, { status: 204 })),
    );

    renderConsole();
    await screen.findByText("goodbye.com");

    await user.click(screen.getByRole("button", { name: /^revoke$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^revoke$/i }));

    await waitFor(() => expect(screen.queryByText("goodbye.com")).not.toBeInTheDocument());
  });
});

describe("DomainClaimsSettings — non-owner refusal (R1)", () => {
  it("test_non_owner_error_state", async () => {
    // covers: R1
    server.use(
      http.get(CLAIMS_URL, () => problem("Owner role required", 403, "ERR_AUTH_FORBIDDEN")),
    );

    renderConsole();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/owner role required/i);
    // no claim data or mutating control is shown — the tab renders ErrorState, not a broken form.
    expect(screen.queryByLabelText(/^domain$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^add domain$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /verify now/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^revoke$/i })).not.toBeInTheDocument();
  });
});
