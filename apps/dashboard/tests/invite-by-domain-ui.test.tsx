/**
 * tests/invite-by-domain-ui.test.tsx — RED suite for task 6b (invite-by-domain-ui):
 * the admin "Invite your team by link" section wired into
 * components/settings/DomainClaimsSettings.tsx (via the new
 * components/settings/DomainInviteLinkSection.tsx), and the public two-phase
 * redeem form components/auth/JoinByDomainForm.tsx.
 *
 * Presentation + BFF pass-through ONLY over the FROZEN 6a gateway core
 * (invite-by-domain TASK.md, commit 71641c5) — every assertion here is
 * OBSERVABLE (rendered text/role, which fetch was/wasn't called), never a
 * component internal.
 *
 * RED failure mode: `@/components/settings/DomainInviteLinkSection` and
 * `@/components/auth/JoinByDomainForm` do not exist yet -> MODULE_NOT_FOUND,
 * the established true-red convention in this repo (see
 * tests/domain-claims-console.test.tsx, tests/accept-invite-page.test.tsx).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import React from "react";

// ── RED: these imports fail until Build writes the two new components ─────────
import { DomainClaimsSettings } from "@/components/settings/DomainClaimsSettings";
import { JoinByDomainForm } from "@/components/auth/JoinByDomainForm";

const APP = "http://localhost:3000";
const CLAIMS_URL = `${APP}/api/gw/admin/domain-claims`;
const LINKS_URL = `${APP}/api/gw/admin/domain-invite-links`;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderConsole() {
  return render(<DomainClaimsSettings />, { wrapper: Wrapper });
}

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const FUTURE = "2099-01-01T00:00:00Z";

interface ClaimItem {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
  verified_at: string | null;
  member_verified_at?: string | null;
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
    member_verified_at: null,
    ...overrides,
  };
}

function claimsGet(claims: ClaimItem[]) {
  return http.get(CLAIMS_URL, () => HttpResponse.json({ claims }));
}

interface LinkItem {
  id: string;
  domain: string;
  status: "active";
  expires_at: string;
  created_at: string;
}

function linksGet(links: LinkItem[]) {
  return http.get(LINKS_URL, () => HttpResponse.json({ links }));
}

const NO_ACTIVE_LINKS = linksGet([]);

describe("DomainInviteLinkSection — section gate (M1, R1)", () => {
  it("test_section_only_on_verified_row", async () => {
    const memberVerified = makeClaim({
      claim_id: "claim-member",
      domain: "acme.com",
      status: "pending",
      member_verified_at: "2026-07-19T00:00:00Z",
    });
    const pending = makeClaim({
      claim_id: "claim-pending",
      domain: "contractor-acme.com",
      status: "pending",
      member_verified_at: null,
      expires_at: FUTURE,
    });
    server.use(claimsGet([memberVerified, pending]), NO_ACTIVE_LINKS);

    renderConsole();

    expect(await screen.findByText("acme.com")).toBeInTheDocument();
    expect(screen.getByText("contractor-acme.com")).toBeInTheDocument();

    const sections = await screen.findAllByText("Invite your team by link");
    expect(sections).toHaveLength(1);
  });
});

describe("DomainInviteLinkSection — load state (M2)", () => {
  it("test_load_reflects_active_link", async () => {
    const verified = makeClaim({
      claim_id: "claim-verified",
      domain: "acme.com",
      status: "verified",
      verified_at: "2026-07-01T00:00:00Z",
    });
    server.use(
      claimsGet([verified]),
      linksGet([
        { id: "link-1", domain: "acme.com", status: "active", expires_at: FUTURE, created_at: "2026-07-01T00:00:00Z" },
      ]),
    );

    renderConsole();

    expect(await screen.findByText("Invite your team by link")).toBeInTheDocument();
    expect(await screen.findByText(/link is active/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /revoke link/i })).toBeInTheDocument();
    expect(screen.queryByText(/\/join\//)).not.toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — mint (M3, M7)", () => {
  it("test_mint_shows_url_once", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-mint", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, async ({ request }) => {
        const body = (await request.json()) as { domain: string };
        expect(body).toEqual({ domain: "acme.com" });
        return HttpResponse.json(
          {
            id: "link-new",
            domain: "acme.com",
            token: "tok-fresh-1",
            status: "active",
            expires_at: FUTURE,
            created_at: "2026-07-20T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    renderConsole();
    await screen.findByText("acme.com");

    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));

    expect(await screen.findByText("http://localhost:3000/join/tok-fresh-1")).toBeInTheDocument();
    expect(screen.getByText(/won't see it again/i)).toBeInTheDocument();
    expect(screen.getByText(/expires in 30 days/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /revoke link/i })).toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — supersede confirm (M4)", () => {
  it("test_supersede_confirm_gates_create", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-super", domain: "acme.com", status: "verified" });
    let mintCalls = 0;
    server.use(
      claimsGet([verified]),
      linksGet([
        { id: "link-old", domain: "acme.com", status: "active", expires_at: FUTURE, created_at: "2026-07-01T00:00:00Z" },
      ]),
      http.post(`${LINKS_URL}`, () => {
        mintCalls += 1;
        return HttpResponse.json(
          {
            id: "link-newer",
            domain: "acme.com",
            token: "tok-superseded",
            status: "active",
            expires_at: FUTURE,
            created_at: "2026-07-20T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    renderConsole();
    await screen.findByText(/link is active/i);

    await user.click(screen.getByRole("button", { name: /create again/i }));

    expect(await screen.findByText(/replaces the current link/i)).toBeInTheDocument();
    expect(mintCalls).toBe(0);

    // Cancel -> no request ever sent, back to the active (non-confirm) view.
    await user.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(mintCalls).toBe(0);
    expect(screen.queryByText(/replaces the current link/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/link is active/i)).toBeInTheDocument();

    // Confirm -> mints, new URL shown once.
    await user.click(screen.getByRole("button", { name: /create again/i }));
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    expect(mintCalls).toBe(1);
    expect(await screen.findByText("http://localhost:3000/join/tok-superseded")).toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — revoke (M5)", () => {
  it("test_revoke_returns_to_empty", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-revoke", domain: "acme.com", status: "verified" });
    let listCallCount = 0;
    server.use(
      claimsGet([verified]),
      http.get(LINKS_URL, () => {
        listCallCount += 1;
        const links =
          listCallCount === 1
            ? [{ id: "link-rev", domain: "acme.com", status: "active" as const, expires_at: FUTURE, created_at: "2026-07-01T00:00:00Z" }]
            : [];
        return HttpResponse.json({ links });
      }),
      http.delete(`${LINKS_URL}/link-rev`, () =>
        HttpResponse.json({ id: "link-rev", status: "revoked" }, { status: 200 }),
      ),
    );

    renderConsole();
    await screen.findByText(/link is active/i);

    await user.click(screen.getByRole("button", { name: /revoke link/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^create invite link$/i })).toBeInTheDocument(),
    );
    expect(screen.queryByText(/link is active/i)).not.toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — create 403 is calm (M6, R2)", () => {
  it("test_create_403_is_calm", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-403", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, () =>
        problem("Domain verification lapsed", 403, "ERR_DOMAIN_INVITE_NOT_ELIGIBLE"),
      ),
    );

    renderConsole();
    await screen.findByText("acme.com");

    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));

    const status = await screen.findByRole("status");
    expect(status).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // the row stays — the create control is still present, no crash.
    expect(screen.getByRole("button", { name: /^create invite link$/i })).toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — token never reappears (R9)", () => {
  it("test_token_never_in_list", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-r9", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, () =>
        HttpResponse.json(
          {
            id: "link-r9",
            domain: "acme.com",
            token: "tok-secret-once",
            status: "active",
            expires_at: FUTURE,
            created_at: "2026-07-20T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
    );

    const { unmount } = renderConsole();
    await screen.findByText("acme.com");
    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));
    expect(await screen.findByText("http://localhost:3000/join/tok-secret-once")).toBeInTheDocument();

    // simulate a reload: unmount and re-render fresh (a new component instance,
    // no leftover local state) against a list GET that (correctly) omits the token.
    unmount();
    server.use(
      claimsGet([verified]),
      linksGet([
        { id: "link-r9", domain: "acme.com", status: "active", expires_at: FUTURE, created_at: "2026-07-20T00:00:00Z" },
      ]),
    );

    renderConsole();
    await screen.findByText(/link is active/i);
    expect(screen.queryByText(/tok-secret-once/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/join\//)).not.toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — copy control (M7)", () => {
  it("test_copy_button_writes_minted_url_to_clipboard", async () => {
    const user = userEvent.setup();
    // @testing-library/user-event's setup() attaches its OWN clipboard stub to
    // `navigator.clipboard` (a real, non-spy async implementation) — so the
    // mock MUST be installed AFTER setup(), not in a shared beforeEach (which
    // would run, then be clobbered by the next setup() call).
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
    const verified = makeClaim({ claim_id: "claim-copy", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, () =>
        HttpResponse.json(
          {
            id: "link-copy",
            domain: "acme.com",
            token: "tok-copy-me",
            status: "active",
            expires_at: FUTURE,
            created_at: "2026-07-20T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
    );

    renderConsole();
    await screen.findByText("acme.com");
    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));
    await screen.findByText("http://localhost:3000/join/tok-copy-me");

    await user.click(screen.getByRole("button", { name: /copy invite link url/i }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "http://localhost:3000/join/tok-copy-me",
    );
  });
});

describe("DomainInviteLinkSection — revoke directly from the minted-once view", () => {
  it("test_revoke_from_minted_once_returns_to_empty", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-revoke2", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, () =>
        HttpResponse.json(
          {
            id: "link-revoke2",
            domain: "acme.com",
            token: "tok-revoke2",
            status: "active",
            expires_at: FUTURE,
            created_at: "2026-07-20T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
      http.delete(`${LINKS_URL}/link-revoke2`, () =>
        HttpResponse.json({ id: "link-revoke2", status: "revoked" }, { status: 200 }),
      ),
    );

    renderConsole();
    await screen.findByText("acme.com");
    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));
    await screen.findByText("http://localhost:3000/join/tok-revoke2");

    await user.click(screen.getByRole("button", { name: /revoke link/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^create invite link$/i })).toBeInTheDocument(),
    );
    expect(screen.queryByText("http://localhost:3000/join/tok-revoke2")).not.toBeInTheDocument();
  });
});

describe("DomainInviteLinkSection — create error fallback + revoke error calm", () => {
  it("test_create_error_falls_back_to_upstream_title", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-fallback", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      NO_ACTIVE_LINKS,
      http.post(`${LINKS_URL}`, () => problem("Upstream is temporarily unavailable", 503, "ERR_UPSTREAM_UNAVAILABLE")),
    );

    renderConsole();
    await screen.findByText("acme.com");
    await user.click(screen.getByRole("button", { name: /^create invite link$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/upstream is temporarily unavailable/i);
  });

  it("test_revoke_error_is_calm_link_stays_active", async () => {
    const user = userEvent.setup();
    const verified = makeClaim({ claim_id: "claim-revoke-err", domain: "acme.com", status: "verified" });
    server.use(
      claimsGet([verified]),
      linksGet([
        { id: "link-err", domain: "acme.com", status: "active", expires_at: FUTURE, created_at: "2026-07-01T00:00:00Z" },
      ]),
      http.delete(`${LINKS_URL}/link-err`, () => problem("Link already revoked", 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE")),
    );

    renderConsole();
    await screen.findByText(/link is active/i);

    await user.click(screen.getByRole("button", { name: /revoke link/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/link already revoked/i);
    // the row stays exactly as it was — still the active link, no crash.
    expect(screen.getByText(/link is active/i)).toBeInTheDocument();
  });
});

// ── JoinByDomainForm (public two-phase redeem page) ─────────────────────────

const TOKEN = "tok-domain-invite";
const JOIN_URL = `${APP}/api/auth/join/${TOKEN}`;
const VERIFY_URL = `${JOIN_URL}/verify`;

function renderJoinForm() {
  return render(<JoinByDomainForm token={TOKEN} />);
}

async function advanceToCodePhase(user: ReturnType<typeof userEvent.setup>, email = "sam@acme.com") {
  await user.type(screen.getByLabelText(/work email/i), email);
  await user.click(screen.getByRole("button", { name: /send me a code/i }));
  await screen.findByRole("group", { name: /6-digit confirmation code/i });
}

async function typeOtp(user: ReturnType<typeof userEvent.setup>, code: string) {
  const digits = screen.getAllByLabelText(/digit \d of 6/i);
  for (let i = 0; i < code.length; i++) {
    await user.type(digits[i], code[i]);
  }
}

describe("JoinByDomainForm — phase 1 (M8, R3)", () => {
  it("test_redeem_phase1_advances_on_202", async () => {
    const user = userEvent.setup();
    let capturedBody: unknown = null;
    server.use(
      http.post(JOIN_URL, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ email: "sam@acme.com" }, { status: 202 });
      }),
    );

    renderJoinForm();
    await advanceToCodePhase(user);

    expect(screen.getByText(/sam@acme\.com/)).toBeInTheDocument();
    expect(capturedBody).toEqual({ email: "sam@acme.com" });
  });

  it("test_redeem_phase1_domain_mismatch", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () =>
        problem("Domain mismatch", 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH"),
      ),
    );

    renderJoinForm();
    await user.type(screen.getByLabelText(/work email/i), "jordan@gmail.com");
    await user.click(screen.getByRole("button", { name: /send me a code/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/work/i);
    // stays in phase 1 — the email field is still present, no OTP shown.
    expect(screen.getByLabelText(/work email/i)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /6-digit confirmation code/i })).not.toBeInTheDocument();
  });
});

describe("JoinByDomainForm — phase 2 success + auto-login (M9, M10)", () => {
  it("test_redeem_phase2_success_redirects", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toEqual({ email: "sam@acme.com", code: "123456", password: "hunter123456" });
        return HttpResponse.json({ ok: true, session: true }, { status: 201 });
      }),
    );

    renderJoinForm();
    await advanceToCodePhase(user);

    await typeOtp(user, "123456");
    await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    await waitFor(() => {
      const router = getRouterMock();
      expect(router.push).toHaveBeenCalledWith("/app");
    });
  });
});

describe("JoinByDomainForm — wrong code stays in phase 2 (R4)", () => {
  it("test_redeem_wrong_code_stays", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, () =>
        problem("Invalid code", 400, "ERR_MEMBER_VERIFY_CODE_INVALID"),
      ),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    await typeOtp(user, "111111");
    await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/code/i);
    expect(screen.getByRole("button", { name: /resend code/i })).toBeInTheDocument();

    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/app");
    expect(screen.getByRole("group", { name: /6-digit confirmation code/i })).toBeInTheDocument();
  });
});

describe("JoinByDomainForm — expired / too-many code stays in phase 2 (R5)", () => {
  it("test_redeem_expired_toomany_stays", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, () =>
        problem("Code expired", 410, "ERR_MEMBER_VERIFY_CODE_EXPIRED"),
      ),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    await typeOtp(user, "222222");
    await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/expired/i);
    // self-contained — never a countdown.
    expect(status).not.toHaveTextContent(/\d+\s*(second|minute)s?/i);
    expect(screen.getByRole("button", { name: /resend code/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /6-digit confirmation code/i })).toBeInTheDocument();
  });

  it("test_redeem_too_many_stays", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, () =>
        problem("Too many attempts", 429, "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS"),
      ),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    await typeOtp(user, "333333");
    await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/too many/i);
    expect(status).not.toHaveTextContent(/\d+\s*(second|minute)s?/i);
    expect(screen.getByRole("button", { name: /resend code/i })).toBeInTheDocument();
  });
});

describe("JoinByDomainForm — weak password inline (R6)", () => {
  it("test_redeem_weak_password_inline", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, () =>
        problem("Password too weak", 400, "ERR_AUTH_PASSWORD_WEAK"),
      ),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    await typeOtp(user, "444444");
    await user.type(screen.getByLabelText(/set a password/i), "short1234");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/app");
  });
});

describe("JoinByDomainForm — other redeem errors are calm (R7, M11)", () => {
  const cases: Array<[number, string, RegExp]> = [
    [409, "ERR_TENANT_EMAIL_TAKEN", /already exists|taken/i],
    [403, "ERR_PLAN_SEAT_CAP_EXCEEDED", /seat/i],
    [409, "ERR_DOMAIN_INVITE_LINK_INACTIVE", /no longer active/i],
    [410, "ERR_INVITE_EXPIRED", /expired/i],
    [404, "ERR_INVITE_NOT_FOUND", /invalid/i],
    [429, "ERR_RATE_LIMITED", /fast|wait/i],
  ];

  for (const [status, code, expectedText] of cases) {
    it(`test_redeem_other_errors_calm_${code}`, async () => {
      const user = userEvent.setup();
      server.use(
        http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
        http.post(VERIFY_URL, () => problem("Error", status, code)),
      );

      renderJoinForm();
      await advanceToCodePhase(user);
      await typeOtp(user, "555555");
      await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
      await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

      const statusEl = await screen.findByRole("status");
      expect(statusEl).toHaveTextContent(expectedText);
      const router = getRouterMock();
      expect(router.push).not.toHaveBeenCalledWith("/app");
    });
  }
});

describe("JoinByDomainForm — auto-login fallback (R8)", () => {
  it("test_redeem_auto_login_fallback_routes_to_login", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(JOIN_URL, () => HttpResponse.json({ email: "sam@acme.com" }, { status: 202 })),
      http.post(VERIFY_URL, () => HttpResponse.json({ ok: true, session: false }, { status: 201 })),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    await typeOtp(user, "666666");
    await user.type(screen.getByLabelText(/set a password/i), "hunter123456");
    await user.click(screen.getByRole("button", { name: /^join acme\.com$/i }));

    await waitFor(() => {
      const router = getRouterMock();
      expect(router.push).toHaveBeenCalledWith(expect.stringMatching(/^\/login\?joined=/));
    });
    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/app");
  });
});

describe("JoinByDomainForm — resend re-arms phase 2 (M12)", () => {
  it("test_redeem_resend_reposts_phase1", async () => {
    const user = userEvent.setup();
    let phase1Calls = 0;
    let capturedResendBody: unknown = null;
    server.use(
      http.post(JOIN_URL, async ({ request }) => {
        phase1Calls += 1;
        capturedResendBody = await request.json();
        return HttpResponse.json({ email: "sam@acme.com" }, { status: 202 });
      }),
    );

    renderJoinForm();
    await advanceToCodePhase(user);
    expect(phase1Calls).toBe(1);

    await user.click(screen.getByRole("button", { name: /resend code/i }));

    await waitFor(() => expect(phase1Calls).toBe(2));
    expect(capturedResendBody).toEqual({ email: "sam@acme.com" });
    // stays in phase 2 — the OTP + password form is still present.
    expect(screen.getByRole("group", { name: /6-digit confirmation code/i })).toBeInTheDocument();
  });
});
