/**
 * tests/member-verified-code-entry.test.tsx — RED suite for
 * member-verified-code-entry TASK.md §3 CONTRACT (FROZEN @ v1). One test per
 * §2 scenario (27 total) — mirrors tests/dns-verify-softeners.test.tsx's
 * conventions: REAL timers, MSW `onUnhandledRequest:"error"` (every BFF route
 * touched is mocked), APP-based same-origin URLs.
 *
 * RED failure mode: `@/components/settings/OtpInput`,
 * `@/components/settings/MemberVerifyCodeEntry` do not exist yet
 * (MODULE_NOT_FOUND at collect) and `DomainStatusSeal`/`DomainClaimsSettings`/
 * `OnboardingChecklist` do not yet know about `member_verified_at` — the
 * established true-red convention in this repo.
 *
 * Every assertion is OBSERVABLE (rendered text, role, which fetch fired, the
 * request body) — never component internals.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { DomainStatusSeal, sealState } from "@/components/settings/DomainStatusSeal";
import { DomainClaimsSettings } from "@/components/settings/DomainClaimsSettings";
import { OtpInput } from "@/components/settings/OtpInput";
import { MemberVerifyCodeEntry } from "@/components/settings/MemberVerifyCodeEntry";
import { OnboardingChecklist } from "@/components/overview/OnboardingChecklist";

const APP = "http://localhost:3000";
const CLAIMS_URL = `${APP}/api/gw/admin/domain-claims`;
const memberVerifyUrl = (id: string) => `${CLAIMS_URL}/${id}/member-verify`;
const resendUrl = (id: string) => `${CLAIMS_URL}/${id}/member-verify/resend`;
const verifyUrl = (id: string) => `${CLAIMS_URL}/${id}/verify`;
const REGISTRAR_URL = `${CLAIMS_URL}/registrar-hint`;

const FUTURE = "2099-01-01T00:00:00Z";
const PAST = "2020-01-01T00:00:00Z";

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
function registrarHint() {
  return http.get(REGISTRAR_URL, ({ request }) => {
    const domain = new URL(request.url).searchParams.get("domain") ?? "";
    return HttpResponse.json({ domain, registrar: null, deep_link_url: null, fallback: true });
  });
}

afterEach(() => {
  cleanup();
});

// ══════════════════════════════════════════════════════════════════════════════
// SEAL / sealState — pure-fn + render (M1, M2, M3)
// ══════════════════════════════════════════════════════════════════════════════
describe("member-verified-code-entry — seal precedence (M1, M2, M3)", () => {
  it("test_member_verified_seal_arm", () => {
    // covers: M1 — member_verified_at set + status pending -> "Member-verified"
    const claim = { status: "pending", expires_at: FUTURE, member_verified_at: "2026-07-20T00:00:00Z" };
    expect(sealState(claim)).toBe("member-verified");

    const { container } = render(<DomainStatusSeal claim={claim} />);
    expect(screen.getByText("Member-verified")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeTruthy();
    // sr-only membership assertion — icon AND label both carry meaning (WCAG 1.4.1)
    expect(screen.getByText(/membership/i)).toBeInTheDocument();
  });

  it("test_owner_wins_over_member", () => {
    // covers: M2 — status verified + member_verified_at set -> "Verified", never "Member-verified"
    const claim = { status: "verified", expires_at: FUTURE, member_verified_at: "2026-07-20T00:00:00Z" };
    expect(sealState(claim)).toBe("verified");

    render(<DomainStatusSeal claim={claim} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.queryByText("Member-verified")).not.toBeInTheDocument();
  });

  it("test_member_wins_over_elapsed_expiry", () => {
    // covers: M2 ⚠ — member_verified_at set + status pending + expires_at PAST -> "Member-verified", never "Expired"
    const claim = { status: "pending", expires_at: PAST, member_verified_at: "2026-07-20T00:00:00Z" };
    expect(sealState(claim)).toBe("member-verified");

    render(<DomainStatusSeal claim={claim} />);
    expect(screen.getByText("Member-verified")).toBeInTheDocument();
    expect(screen.queryByText("Expired")).not.toBeInTheDocument();
  });

  it("test_existing_labels_verbatim", () => {
    // covers: M3 — the 3 frozen seal labels stay VERBATIM (additive-only)
    const pending = { status: "pending", expires_at: FUTURE, member_verified_at: null };
    const verified = { status: "verified", expires_at: FUTURE, member_verified_at: null };

    const r1 = render(<DomainStatusSeal claim={pending} />);
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
    r1.unmount();

    render(<DomainStatusSeal claim={verified} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// CODE BLOCK visibility (M4) + CONFIRM/SUCCESS integration (M8, M9)
// ══════════════════════════════════════════════════════════════════════════════
describe("member-verified-code-entry — code block visibility (M4)", () => {
  it("test_code_block_on_actionable_pending", async () => {
    // covers: M4 — the code block renders above the reused DNS challenge card
    // for an actionable (member_verified_at == null, not verified) pending claim.
    const user = userEvent.setup();
    const claimsState: Array<Record<string, unknown>> = [];
    server.use(
      http.get(CLAIMS_URL, () => HttpResponse.json({ claims: claimsState })),
      http.post(CLAIMS_URL, () => {
        const claim = {
          claim_id: "claim-new",
          domain: "acme.com",
          status: "pending",
          dns_record_type: "TXT",
          dns_record_name: "_ai-proxy-challenge.acme.com",
          dns_record_value: "ai-proxy-domain-verification=tok-fresh",
          expires_at: FUTURE,
          member_verified_at: null,
          verified_at: null,
          notify_requested_at: null,
          notified_at: null,
        };
        claimsState.push(claim);
        return HttpResponse.json(claim, { status: 201 });
      }),
      registrarHint(),
    );

    const { container } = render(<DomainClaimsSettings />, { wrapper: Wrapper });
    await screen.findByLabelText(/^domain$/i);
    await user.type(screen.getByLabelText(/^domain$/i), "acme.com");
    await user.click(screen.getByRole("button", { name: /^add domain$/i }));

    await screen.findByText("_ai-proxy-challenge.acme.com");
    const codeBlock = container.querySelector('[data-slot="member-verify-code-entry"]');
    const dnsCard = container.querySelector('[data-slot="dns-challenge"]');
    expect(codeBlock).toBeTruthy();
    expect(dnsCard).toBeTruthy();
    // eslint-disable-next-line no-bitwise
    expect(codeBlock!.compareDocumentPosition(dnsCard!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("member-verified-code-entry — success flips seal + collapses (M9)", () => {
  it("test_success_flips_seal_collapses", async () => {
    // covers: M9 — a 200 flips the seal to Member-verified, collapses the code
    // block, keeps the DNS card, and status stays "pending".
    const user = userEvent.setup();
    const claimsState: Array<Record<string, unknown>> = [];
    server.use(
      http.get(CLAIMS_URL, () => HttpResponse.json({ claims: claimsState })),
      http.post(CLAIMS_URL, () => {
        const claim = {
          claim_id: "claim-new",
          domain: "acme.com",
          status: "pending",
          dns_record_type: "TXT",
          dns_record_name: "_ai-proxy-challenge.acme.com",
          dns_record_value: "ai-proxy-domain-verification=tok-fresh",
          expires_at: FUTURE,
          member_verified_at: null,
          verified_at: null,
          notify_requested_at: null,
          notified_at: null,
        };
        claimsState.push(claim);
        return HttpResponse.json(claim, { status: 201 });
      }),
      registrarHint(),
      http.post(memberVerifyUrl("claim-new"), () =>
        HttpResponse.json({
          claim_id: "claim-new",
          domain: "acme.com",
          status: "pending",
          member_verified_at: "2026-07-20T00:00:00Z",
        }),
      ),
    );

    const { container } = render(<DomainClaimsSettings />, { wrapper: Wrapper });
    await screen.findByLabelText(/^domain$/i);
    await user.type(screen.getByLabelText(/^domain$/i), "acme.com");
    await user.click(screen.getByRole("button", { name: /^add domain$/i }));
    await screen.findByText("_ai-proxy-challenge.acme.com");
    // wait for the claims-list refetch so the new row (and its seal) exist —
    // "acme.com" also appears in the DNS card's own copy, so assert via the table row.
    await waitFor(() => expect(screen.getAllByText("acme.com").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();

    const digits = within(container).getAllByLabelText(/digit \d of 6/i);
    for (let i = 0; i < 6; i++) {
      await user.type(digits[i], String((i + 4) % 10));
    }
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    await waitFor(() => expect(screen.getByText("Member-verified")).toBeInTheDocument());
    expect(screen.queryByText("Pending DNS")).not.toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
    expect(container.querySelector('[data-slot="member-verify-code-entry"]')).toBeFalsy();
    expect(container.querySelector('[data-slot="dns-challenge"]')).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// OTP input (M5, M6, M7)
// ══════════════════════════════════════════════════════════════════════════════
describe("member-verified-code-entry — OTP input (M5, M6, M7)", () => {
  it("test_only_digits_auto_advance", async () => {
    // covers: M5
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<OtpInput onChange={onChange} />);
    const digits = screen.getAllByLabelText(/digit \d of 6/i);

    await user.type(digits[0], "4");
    expect(digits[0]).toHaveValue("4");
    expect(document.activeElement).toBe(digits[1]);

    await user.type(digits[1], "x");
    expect(digits[1]).toHaveValue("");
    expect(document.activeElement).toBe(digits[1]);
  });

  it("test_backspace_steps_back", async () => {
    // covers: M6
    const user = userEvent.setup();
    render(<OtpInput />);
    const digits = screen.getAllByLabelText(/digit \d of 6/i);

    await user.type(digits[0], "1");
    await user.type(digits[1], "2");
    await user.type(digits[2], "3");
    // focus is now on segment 4 (empty)
    expect(document.activeElement).toBe(digits[3]);

    await user.type(digits[3], "{Backspace}");
    expect(document.activeElement).toBe(digits[2]);
    expect(digits[2]).toHaveValue("");
  });

  it("test_paste_fills_all", async () => {
    // covers: M7
    const user = userEvent.setup();
    render(<OtpInput />);
    const digits = screen.getAllByLabelText(/digit \d of 6/i);

    await user.click(digits[0]);
    await user.paste("41 92-07");
    expect(digits.map((d) => (d as HTMLInputElement).value)).toEqual(["4", "1", "9", "2", "0", "7"]);

    // paste of zero digits -> no-op, segments unchanged
    await user.click(digits[0]);
    await user.paste("abc");
    expect(digits.map((d) => (d as HTMLInputElement).value)).toEqual(["4", "1", "9", "2", "0", "7"]);
  });
});

describe("member-verified-code-entry — Confirm gating (M8)", () => {
  it("test_confirm_disabled_until_full", async () => {
    // covers: M8
    const user = userEvent.setup();
    server.use(registrarHint());
    render(<MemberVerifyCodeEntry claimId="claim-x" onVerified={vi.fn()} />, { wrapper: Wrapper });
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    const confirmBtn = screen.getByRole("button", { name: /^confirm$/i });
    expect(confirmBtn).toBeDisabled();

    for (let i = 0; i < 5; i++) {
      await user.type(digits[i], String(i));
    }
    expect(confirmBtn).toBeDisabled();

    await user.type(digits[5], "9");
    expect(confirmBtn).toBeEnabled();
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// CONFIRM submits / RESEND / SEND-ONLY-CODE (M8, M10, M14)
// ══════════════════════════════════════════════════════════════════════════════
describe("member-verified-code-entry — confirm submits the code (M8)", () => {
  it("test_confirm_submits_code", async () => {
    // covers: M8
    const user = userEvent.setup();
    let captured: { body: unknown } = { body: null };
    server.use(
      http.post(memberVerifyUrl("claim-x"), async ({ request }) => {
        captured.body = await request.json();
        return HttpResponse.json({ claim_id: "claim-x", domain: "acme.com", status: "pending", member_verified_at: "2026-07-20T00:00:00Z" });
      }),
    );
    render(<MemberVerifyCodeEntry claimId="claim-x" onVerified={vi.fn()} />, { wrapper: Wrapper });
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    const code = "419207";
    for (let i = 0; i < 6; i++) {
      await user.type(digits[i], code[i]);
    }
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    await waitFor(() => expect(captured.body).toEqual({ code: "419207" }));
  });
});

describe("member-verified-code-entry — resend re-arms and clears (M10)", () => {
  it("test_resend_rearms_clears", async () => {
    // covers: M10
    const user = userEvent.setup();
    server.use(
      http.post(resendUrl("claim-x"), () =>
        HttpResponse.json({ claim_id: "claim-x", domain: "acme.com", status: "pending", member_verified_at: null }),
      ),
    );
    render(<MemberVerifyCodeEntry claimId="claim-x" onVerified={vi.fn()} />, { wrapper: Wrapper });
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    for (let i = 0; i < 3; i++) {
      await user.type(digits[i], String(i + 1));
    }

    await user.click(screen.getByRole("button", { name: /resend code/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/fresh code/i);
    await waitFor(() => {
      const freshDigits = screen.getAllByLabelText(/digit \d of 6/i);
      freshDigits.forEach((d) => expect(d).toHaveValue(""));
    });
  });
});

describe("member-verified-code-entry — the UI never sends an email or domain (M14)", () => {
  it("test_no_email_or_domain_input", async () => {
    // covers: M14
    const user = userEvent.setup();
    let verifyBody: unknown = null;
    let resendBody: unknown = null;
    server.use(
      http.post(memberVerifyUrl("claim-x"), async ({ request }) => {
        verifyBody = await request.text();
        return HttpResponse.json({ claim_id: "claim-x", domain: "acme.com", status: "pending", member_verified_at: "2026-07-20T00:00:00Z" });
      }),
      http.post(resendUrl("claim-x"), async ({ request }) => {
        resendBody = await request.text();
        return HttpResponse.json({ claim_id: "claim-x", domain: "acme.com", status: "pending", member_verified_at: null });
      }),
    );
    const { container } = render(<MemberVerifyCodeEntry claimId="claim-x" onVerified={vi.fn()} />, {
      wrapper: Wrapper,
    });

    // no email/domain input anywhere in the block
    expect(container.querySelector('input[type="email"]')).toBeFalsy();
    expect(screen.queryByLabelText(/e-?mail/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^domain$/i)).not.toBeInTheDocument();

    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    for (let i = 0; i < 6; i++) {
      await user.type(digits[i], String(i));
    }
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => expect(verifyBody).toBe(JSON.stringify({ code: "012345" })));

    await user.click(screen.getByRole("button", { name: /resend code/i }));
    await waitFor(() => expect(resendBody).toBe(JSON.stringify({})));
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// CALM ERRORS — one per frozen code (M11, M12)
// ══════════════════════════════════════════════════════════════════════════════
function renderFilled() {
  return render(<MemberVerifyCodeEntry claimId="claim-x" onVerified={vi.fn()} />, { wrapper: Wrapper });
}
async function fillAll(user: ReturnType<typeof userEvent.setup>) {
  const digits = screen.getAllByLabelText(/digit \d of 6/i);
  const code = "419207";
  for (let i = 0; i < 6; i++) {
    await user.type(digits[i], code[i]);
  }
  return digits;
}

describe("member-verified-code-entry — calm errors, never loud (M11, M12)", () => {
  it("test_err_invalid_400", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("bad code", 400, "ERR_MEMBER_VERIFY_CODE_INVALID")));
    renderFilled();
    const digits = await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/doesn't match/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    digits.forEach((d, i) => expect(d).toHaveValue("419207"[i]));
  });

  it("test_err_expired_410_offers_resend", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("expired", 410, "ERR_MEMBER_VERIFY_CODE_EXPIRED")));
    renderFilled();
    await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/expired/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resend code/i })).toBeInTheDocument();
  });

  it("test_err_too_many_429_offers_resend", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(memberVerifyUrl("claim-x"), () => problem("too many", 429, "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS")),
    );
    renderFilled();
    await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/too many tries/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resend code/i })).toBeInTheDocument();
  });

  it("test_err_rate_limited_429_no_countdown", async () => {
    // covers: M12 — self-contained, no countdown promise
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("slow down", 429, "ERR_RATE_LIMITED")));
    renderFilled();
    const digits = await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/wait a moment/i);
    expect(status.textContent).not.toMatch(/\d+\s*(s|sec|second)/i);
    digits.forEach((d, i) => expect(d).toHaveValue("419207"[i]));
  });

  it("test_err_not_pending_409", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("already verified", 409, "ERR_DOMAIN_CLAIM_NOT_PENDING")));
    renderFilled();
    await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/already verified/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_err_domain_mismatch_403", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(memberVerifyUrl("claim-x"), () => problem("mismatch", 403, "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH")),
    );
    renderFilled();
    const digits = await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/different email domain/i);
    digits.forEach((d, i) => expect(d).toHaveValue("419207"[i]));
  });

  it("test_err_forbidden_403", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("forbidden", 403, "ERR_AUTH_FORBIDDEN")));
    renderFilled();
    await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/don't have permission/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_err_not_found_404", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("not found", 404, "ERR_DOMAIN_CLAIM_NOT_FOUND")));
    renderFilled();
    await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/couldn't find/i);
  });

  it("test_resend_err_generic_422", async () => {
    const user = userEvent.setup();
    server.use(http.post(resendUrl("claim-x"), () => problem("generic domain", 422, "ERR_DOMAIN_GENERIC")));
    renderFilled();
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    for (let i = 0; i < 3; i++) {
      await user.type(digits[i], String(i + 1));
    }
    await user.click(screen.getByRole("button", { name: /resend code/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/public email domains/i);
    // segments NOT cleared on a resend error
    expect(digits[0]).toHaveValue("1");
    expect(digits[1]).toHaveValue("2");
    expect(digits[2]).toHaveValue("3");
  });

  it("test_resend_err_not_eligible_403", async () => {
    const user = userEvent.setup();
    server.use(http.post(resendUrl("claim-x"), () => problem("not eligible", 403, "ERR_MEMBER_VERIFY_NOT_ELIGIBLE")));
    renderFilled();
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    await user.type(digits[0], "7");
    await user.click(screen.getByRole("button", { name: /resend code/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/personal accounts/i);
    expect(digits[0]).toHaveValue("7");
  });

  it("test_unmapped_error_calm_fallback", async () => {
    const user = userEvent.setup();
    server.use(http.post(memberVerifyUrl("claim-x"), () => problem("weird", 418, "ERR_SOMETHING_NOBODY_MAPPED")));
    renderFilled();
    const digits = await fillAll(user);
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/something went wrong/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    digits.forEach((d, i) => expect(d).toHaveValue("419207"[i]));
  });

  it("test_non_digit_no_request", async () => {
    // covers: client-side reject — no request sent for a non-digit keystroke
    const user = userEvent.setup();
    let called = false;
    server.use(
      http.post(memberVerifyUrl("claim-x"), () => {
        called = true;
        return HttpResponse.json({ claim_id: "claim-x", domain: "acme.com", status: "pending", member_verified_at: "2026-07-20T00:00:00Z" });
      }),
    );
    renderFilled();
    const digits = screen.getAllByLabelText(/digit \d of 6/i);
    await user.type(digits[0], "x");
    expect(digits[0]).toHaveValue("");
    expect(called).toBe(false);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// ONBOARDING (M13)
// ══════════════════════════════════════════════════════════════════════════════
function meHandler(role: string) {
  return http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "u-1",
      tenant_id: "tenant-a",
      email: "ada@acme.io",
      role,
      exp: Math.floor(Date.now() / 1000) + 86400,
    }),
  );
}
function coreOnboardingMocks(opts: { totalRequests?: number } = {}) {
  server.use(
    http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([])),
    http.get(`${APP}/api/gw/admin/usage`, () =>
      HttpResponse.json({ total_requests: opts.totalRequests ?? 0 }),
    ),
    http.get(`${APP}/api/gw/admin/provider-keys`, () => HttpResponse.json({ keys: [] })),
    http.get(`${APP}/api/gw/admin/invites`, () => HttpResponse.json({ invites: [] })),
  );
}

describe("member-verified-code-entry — onboarding step (M13)", () => {
  it("test_checklist_step_appears_deeplinks", async () => {
    // covers: M13
    server.use(meHandler("owner"));
    coreOnboardingMocks();
    let claimsCalled = false;
    server.use(
      http.get(CLAIMS_URL, () => {
        claimsCalled = true;
        return HttpResponse.json({
          claims: [{ claim_id: "c1", status: "pending", member_verified_at: "2026-07-20T00:00:00Z" }],
        });
      }),
    );

    render(<OnboardingChecklist />, { wrapper: Wrapper });
    const step = await screen.findByTestId("step-confirm_domain");
    expect(within(step).getByText(/confirm your work email domain/i)).toBeInTheDocument();
    const link = within(step).getByRole("link", { name: /confirm your work email domain/i });
    expect(link).toHaveAttribute("href", "/app/settings?tab=domains");
    expect(step).toHaveAttribute("data-complete", "true");
    expect(claimsCalled).toBe(true);
    cleanup();

    // absent for a non-owner role — and never issues the domain-claims read
    server.use(meHandler("member"));
    coreOnboardingMocks();
    let secondCallFired = false;
    server.use(
      http.get(CLAIMS_URL, () => {
        secondCallFired = true;
        return HttpResponse.json({ claims: [] });
      }),
    );
    render(<OnboardingChecklist />, { wrapper: Wrapper });
    await screen.findByTestId("step-create_key");
    expect(screen.queryByTestId("step-confirm_domain")).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(secondCallFired).toBe(false);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// REUSE UNTOUCHED — smoke check only (M15); full coverage owned by the frozen
// dns-verify-softeners / domain-claims-console suites (do not duplicate).
// ══════════════════════════════════════════════════════════════════════════════
describe("member-verified-code-entry — reuse stays untouched (M15)", () => {
  it("test_manual_verify_alert_untouched", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(CLAIMS_URL, () =>
        HttpResponse.json({
          claims: [
            {
              claim_id: "claim-m15",
              domain: "manual-check.com",
              status: "pending",
              dns_record_name: "_ai-proxy-challenge.manual-check.com",
              dns_record_value: "ai-proxy-domain-verification=tok",
              expires_at: FUTURE,
              verified_at: null,
              notify_requested_at: null,
              notified_at: null,
              member_verified_at: null,
            },
          ],
        }),
      ),
      http.post(verifyUrl("claim-m15"), () => problem("DNS record not found or does not match", 422, "ERR_DOMAIN_VERIFICATION_FAILED")),
    );

    render(<DomainClaimsSettings />, { wrapper: Wrapper });
    await screen.findByText("manual-check.com");

    await user.click(screen.getByRole("button", { name: /verify now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/absent|mismatch/i);
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
  });
});
