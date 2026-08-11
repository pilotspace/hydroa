/**
 * tests/dns-verify-softeners.test.tsx — RED suite for dns-verify-softeners
 * TASK.md §3 CONTRACT (FROZEN @ v2). Softens the DNS-TXT verify flow on the
 * OWNER-only domain-claims console (`components/settings/DomainClaimsSettings.tsx`)
 * with an ADDITIVE, bounded client auto-poll layer — the verify endpoint,
 * its error taxonomy, the seal-flips-only-on-200 rule (R2), and AUTO-JOIN are
 * ALL unchanged.
 *
 * v2 narrowing (Tin 2026-07-20): the softening is the AUTO-POLL path only. The
 * MANUAL "Verify now" button (label + its LOUD role="alert" on a manual 400/503)
 * is owned UNCHANGED by the frozen `domain-claims-console.test.tsx` suite and is
 * NOT touched here — this suite only asserts the manual button still coexists.
 *
 * Timing seam: the component takes an optional `poll={{ intervalMs, ceilingMs }}`
 * prop defaulting to the frozen ~30_000 / ~900_000 constants. Tests inject fast
 * timings and use REAL timers with the repo's MSW server (the established suite
 * convention — `onUnhandledRequest: "error"`, no fake-timer precedent in tests/).
 *
 * Every assertion is OBSERVABLE (rendered text, role, data-slot, which fetch
 * fired) — never component internals.
 *
 * RED failure mode: `DomainClaimsSettings` does not yet accept `poll`, has no
 * auto-poll / calm-state / one-block-copy / hand-off / registrar-links /
 * verify-later / notify-opt-in — so these assertions fail on the current tree.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { DomainClaimsSettings } from "@/components/settings/DomainClaimsSettings";

const APP = "http://localhost:3000";
const CLAIMS_URL = `${APP}/api/gw/admin/domain-claims`;
const verifyUrl = (id: string) => `${CLAIMS_URL}/${id}/verify`;
const notifyUrl = (id: string) => `${CLAIMS_URL}/${id}/notify`;
const REGISTRAR_URL = `${CLAIMS_URL}/registrar-hint`;

const FUTURE = "2099-01-01T00:00:00Z";
const PAST = "2020-01-01T00:00:00Z";

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface ClaimItem {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
  verified_at: string | null;
  notify_requested_at: string | null;
  notified_at: string | null;
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
    notify_requested_at: null,
    notified_at: null,
    ...overrides,
  };
}

function claimsGet(claims: ClaimItem[]) {
  return http.get(CLAIMS_URL, () => HttpResponse.json({ claims }));
}

function problem(title: string, status: number, code: string, headers?: Record<string, string>) {
  return HttpResponse.json({ title, status, code }, { status, headers });
}

/** A registrar-hint stub — many card tests must mock it (unhandled → MSW errors). */
function registrarHint(body: {
  registrar: string | null;
  deep_link_url: string | null;
  fallback: boolean;
}) {
  return http.get(REGISTRAR_URL, ({ request }) => {
    const domain = new URL(request.url).searchParams.get("domain") ?? "";
    return HttpResponse.json({ domain, ...body });
  });
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

// A poll interval far longer than any test → auto-poll never fires; isolates the
// affordance/manual tests from the polling machinery.
const NO_POLL = { intervalMs: 10_000_000, ceilingMs: 10_000_000 };

function renderConsole(poll?: { intervalMs?: number; ceilingMs?: number }) {
  return render(<DomainClaimsSettings poll={poll} />, { wrapper: Wrapper });
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  return writeText;
}

function setTabHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: hidden ? "hidden" : "visible",
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  cleanup();
  setTabHidden(false);
});

// ── Create a domain so the dns-challenge card (which hosts M6–M10) renders ──────
async function openChallengeCard(user: ReturnType<typeof userEvent.setup>, domain = "acme.com") {
  await screen.findByLabelText(/^domain$/i);
  await user.type(screen.getByLabelText(/^domain$/i), domain);
  await user.click(screen.getByRole("button", { name: /^add domain$/i }));
  return screen.findByText(new RegExp(`_ai-proxy-challenge\\.${domain.replace(".", "\\.")}`));
}

function createHandler(domain = "acme.com") {
  return http.post(CLAIMS_URL, () =>
    HttpResponse.json(
      {
        claim_id: "claim-new",
        domain,
        status: "pending",
        dns_record_type: "TXT",
        dns_record_name: `_ai-proxy-challenge.${domain}`,
        dns_record_value: "ai-proxy-domain-verification=tok-fresh",
        expires_at: FUTURE,
      },
      { status: 201 },
    ),
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// AUTO-POLL — auto-flip + calm reframe + terminal alert + bounded/fail-safe (M1–M5)
// ══════════════════════════════════════════════════════════════════════════════
describe("dns-verify-softeners — auto-poll auto-flip (M1, M2)", () => {
  it("test_auto_flip_on_200", async () => {
    // covers: M1, M2 — a live TXT record flips the seal with NO click + announces it.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-flip", domain: "goes-live.com" })]),
      http.post(verifyUrl("c-flip"), () => {
        verifyCalls += 1;
        return HttpResponse.json({
          claim_id: "c-flip",
          domain: "goes-live.com",
          status: "verified",
          verified_at: "2026-07-20T00:00:00Z",
        });
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("goes-live.com");

    // no click — auto-poll flips it
    await waitFor(() => expect(screen.getByText("Verified")).toBeInTheDocument());
    expect(screen.queryByText("Pending DNS")).not.toBeInTheDocument();

    // aria-live announcement of the verified domain (a status region, not the seal)
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/goes-live\.com/);
    expect(status).toHaveTextContent(/verified/i);

    // removed from the poll set — no further verify after the flip
    const settled = verifyCalls;
    await sleep(120);
    expect(verifyCalls).toBe(settled);
  });
});

describe("dns-verify-softeners — calm 'still checking' reframe (M3, Ra)", () => {
  it("test_calm_state_on_400", async () => {
    // covers: M3, Ra — 400 during AUTO-poll is calm, NOT a red alert; seal stays pending.
    server.use(
      claimsGet([makeClaim({ claim_id: "c-400", domain: "propagating.com" })]),
      http.post(verifyUrl("c-400"), () =>
        problem("not found", 400, "ERR_DOMAIN_VERIFICATION_FAILED"),
      ),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("propagating.com");

    await waitFor(() =>
      expect(screen.getByText(/waiting for dns to propagate/i)).toBeInTheDocument(),
    );
    // calm, not destructive
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // frozen R2 — seal stays pending, never flips on a failure
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("test_calm_state_on_503", async () => {
    // covers: M3 — a transient 503 keeps the calm state + keeps polling.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-503", domain: "flaky.com" })]),
      http.post(verifyUrl("c-503"), () => {
        verifyCalls += 1;
        return problem("dns lookup failed", 503, "ERR_DNS_LOOKUP_FAILED");
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("flaky.com");

    await waitFor(() =>
      expect(screen.getByText(/waiting for dns to propagate/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // still polling — the count keeps climbing past the first tick
    const first = verifyCalls;
    await waitFor(() => expect(verifyCalls).toBeGreaterThan(first));
  });
});

describe("dns-verify-softeners — terminal-error alert stops the poll (M4)", () => {
  it("test_terminal_alert_on_410", async () => {
    // covers: M4 — 410 EXPIRED gets a loud actionable alert + the poll stops.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-410", domain: "expired.com" })]),
      http.post(verifyUrl("c-410"), () => {
        verifyCalls += 1;
        return problem("challenge expired", 410, "ERR_DOMAIN_CLAIM_EXPIRED");
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("expired.com");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/expired|request a new/i);

    // poll stops for that claim
    await waitFor(() => expect(verifyCalls).toBeGreaterThanOrEqual(1));
    const settled = verifyCalls;
    await sleep(120);
    expect(verifyCalls).toBe(settled);
  });
});

describe("dns-verify-softeners — bounded / fail-safe poll (M5, Rb, Rc, Rd)", () => {
  it("test_backoff_on_429", async () => {
    // covers: M5, Rb — a 429 backs off ≥ the retry window; no 2nd call before it.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-429", domain: "limited.com" })]),
      http.post(verifyUrl("c-429"), () => {
        verifyCalls += 1;
        return problem("rate limited", 429, "ERR_RATE_LIMITED", { "Retry-After": "1" });
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("limited.com");

    await waitFor(() => expect(verifyCalls).toBe(1));
    // Retry-After: 1s ≫ the 20ms interval — no second call within the backoff window.
    await sleep(300);
    expect(verifyCalls).toBe(1);
  });

  it("test_pause_on_tab_hidden", async () => {
    // covers: M5, Rd — a hidden tab pauses the poll entirely.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-hidden", domain: "background.com" })]),
      http.post(verifyUrl("c-hidden"), () => {
        verifyCalls += 1;
        return problem("not found", 400, "ERR_DOMAIN_VERIFICATION_FAILED");
      }),
    );

    // Hide the tab BEFORE rendering, so there is no window in which a tick could legally
    // fire. This used to hide it AFTER `findByText` resolved, which left the 20ms interval
    // free to fire once in the gap between the claim becoming pollable and the tab going
    // hidden. That race never lost on a fast dev box (0/20 locally) and DID lose in CI —
    // `expected 1 to be +0`, run 31464010863, on 4 vCPUs where module import alone took
    // 111.93s. The property under test is "hidden => no polling", which does not require the
    // tab to start visible, so removing the window costs the test nothing.
    setTabHidden(true);
    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("background.com");

    // NEGATIVE WAIT: 200ms is 10x the 20ms interval, so a missing visibility pause has ample
    // opportunity to show itself. A negative assertion needs some duration by nature; this
    // one is bounded and deliberately generous rather than tuned to just-barely-pass.
    await sleep(200);
    expect(verifyCalls).toBe(0);

    // resumes when visible again
    setTabHidden(false);
    await waitFor(() => expect(verifyCalls).toBeGreaterThan(0));
  });

  it("test_verified_never_polled", async () => {
    // covers: Rc — a verified claim is never added to the poll set.
    let verifyCalled = false;
    server.use(
      claimsGet([
        makeClaim({
          claim_id: "c-done",
          domain: "already.com",
          status: "verified",
          verified_at: "2026-07-01T00:00:00Z",
        }),
      ]),
      http.post(verifyUrl("c-done"), () => {
        verifyCalled = true;
        return HttpResponse.json({});
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await screen.findByText("already.com");
    expect(screen.getByText("Verified")).toBeInTheDocument();

    await sleep(200);
    expect(verifyCalled).toBe(false);
  });

  it("test_ceiling_stops_polling", async () => {
    // covers: M5 — after the ceiling, the poll stops but "Verify now" remains.
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "c-ceil", domain: "slow.com" })]),
      http.post(verifyUrl("c-ceil"), () => {
        verifyCalls += 1;
        return problem("not found", 400, "ERR_DOMAIN_VERIFICATION_FAILED");
      }),
    );

    renderConsole({ intervalMs: 20, ceilingMs: 70 });
    await screen.findByText("slow.com");

    await waitFor(() => expect(verifyCalls).toBeGreaterThanOrEqual(1));
    await sleep(250); // well past the 70ms ceiling
    const settled = verifyCalls;
    await sleep(200);
    expect(verifyCalls).toBe(settled); // no more ticks after the ceiling

    // the manual escape hatch is still there
    expect(screen.getByRole("button", { name: /verify now/i })).toBeInTheDocument();
  });
});

describe("dns-verify-softeners — manual 'Verify now' coexists, unchanged (M5 v2)", () => {
  it("test_manual_verify_now_still_immediate", async () => {
    // covers: M5 (v2) — the existing manual button still forces an immediate verify.
    const user = userEvent.setup();
    server.use(
      claimsGet([makeClaim({ claim_id: "c-man", domain: "manual.com" })]),
      http.post(verifyUrl("c-man"), () =>
        HttpResponse.json({
          claim_id: "c-man",
          domain: "manual.com",
          status: "verified",
          verified_at: "2026-07-20T00:00:00Z",
        }),
      ),
    );

    renderConsole(NO_POLL); // auto-poll disabled → the flip can only come from the click
    await screen.findByText("manual.com");
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /verify now/i }));

    await waitFor(() => expect(screen.getByText("Verified")).toBeInTheDocument());
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// CHALLENGE-CARD AFFORDANCES — copy / hand-off / registrar / verify-later / notify
// ══════════════════════════════════════════════════════════════════════════════
describe("dns-verify-softeners — one-block copy (M6)", () => {
  it("test_copy_record_one_block", async () => {
    // covers: M6 — "Copy record" writes type+name+value as one block; per-field copies stay.
    const user = userEvent.setup();
    const writeText = stubClipboard();
    server.use(
      claimsGet([]),
      createHandler("acme.com"),
      registrarHint({ registrar: null, deep_link_url: null, fallback: true }),
    );

    renderConsole(NO_POLL);
    await openChallengeCard(user);

    await user.click(screen.getByRole("button", { name: /^copy record$/i }));

    expect(writeText).toHaveBeenCalledTimes(1);
    const block = writeText.mock.calls[0][0] as string;
    expect(block).toContain("TXT");
    expect(block).toContain("_ai-proxy-challenge.acme.com");
    expect(block).toContain("ai-proxy-domain-verification=tok-fresh");

    // additive — the per-field "Copy" buttons still exist (≥3 fields + the block one)
    expect(screen.getAllByRole("button", { name: /copy/i }).length).toBeGreaterThanOrEqual(3);
  });
});

describe("dns-verify-softeners — not-the-DNS-owner hand-off (M7)", () => {
  it("test_handoff_composes_message", async () => {
    // covers: M7 — a shareable mailto with the domain + record; state unchanged.
    const user = userEvent.setup();
    server.use(
      claimsGet([]),
      createHandler("acme.com"),
      registrarHint({ registrar: null, deep_link_url: null, fallback: true }),
    );

    const { container } = renderConsole(NO_POLL);
    await openChallengeCard(user);

    const handoff = container.querySelector('[data-slot="dns-handoff"]');
    expect(handoff).toBeTruthy();
    const mail = handoff!.querySelector('a[href^="mailto:"]') as HTMLAnchorElement | null;
    expect(mail).toBeTruthy();
    const decoded = decodeURIComponent(mail!.getAttribute("href")!);
    expect(decoded).toContain("acme.com");
    expect(decoded).toContain("_ai-proxy-challenge.acme.com");
    expect(decoded).toContain("ai-proxy-domain-verification=tok-fresh");

    // nothing about verification state changed
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });
});

describe("dns-verify-softeners — registrar deep-link display (M8)", () => {
  it("test_registrar_deeplink_and_fallback", async () => {
    // covers: M8 — deep link when fallback:false; static provider list when fallback:true.
    const user = userEvent.setup();

    // (a) fallback:false → a deep-link anchor to the registrar's DNS panel
    server.use(
      claimsGet([]),
      createHandler("acme.com"),
      registrarHint({
        registrar: "Cloudflare",
        deep_link_url: "https://dash.cloudflare.com/login",
        fallback: false,
      }),
    );
    const first = renderConsole(NO_POLL);
    await openChallengeCard(user);

    const links1 = first.container.querySelector('[data-slot="registrar-links"]');
    expect(links1).toBeTruthy();
    const deep = await within(links1 as HTMLElement).findByRole("link", { name: /cloudflare/i });
    expect(deep).toHaveAttribute("href", "https://dash.cloudflare.com/login");
    expect(deep).toHaveAttribute("target", "_blank");
    expect(deep).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(deep.getAttribute("rel")).toContain("noreferrer");

    cleanup();

    // (b) fallback:true → the static "Open your DNS provider" common-registrar list
    server.use(
      claimsGet([]),
      createHandler("beta.com"),
      registrarHint({ registrar: null, deep_link_url: null, fallback: true }),
    );
    const user2 = userEvent.setup();
    const second = renderConsole(NO_POLL);
    await openChallengeCard(user2, "beta.com");

    const links2 = second.container.querySelector('[data-slot="registrar-links"]');
    expect(links2).toBeTruthy();
    expect(within(links2 as HTMLElement).getByText(/open your dns provider/i)).toBeInTheDocument();
    // a static list of common registrars, each a safe new-tab external link
    const anchors = (links2 as HTMLElement).querySelectorAll('a[target="_blank"]');
    expect(anchors.length).toBeGreaterThanOrEqual(2);
    anchors.forEach((a) => expect(a.getAttribute("rel")).toContain("noopener"));
  });
});

describe("dns-verify-softeners — verify later (M9)", () => {
  it("test_verify_later_dismisses_nonblocking", async () => {
    // covers: M9 — dismiss the card; the claim stays pending in the table + keeps polling.
    const user = userEvent.setup();
    let verifyCalls = 0;
    server.use(
      claimsGet([makeClaim({ claim_id: "claim-new", domain: "acme.com" })]),
      createHandler("acme.com"),
      registrarHint({ registrar: null, deep_link_url: null, fallback: true }),
      http.post(verifyUrl("claim-new"), () => {
        verifyCalls += 1;
        return problem("not found", 400, "ERR_DOMAIN_VERIFICATION_FAILED");
      }),
    );

    const { container } = renderConsole({ intervalMs: 20, ceilingMs: 900_000 });
    await openChallengeCard(user);
    expect(container.querySelector('[data-slot="dns-challenge"]')).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /verify later/i }));

    // card dismissed, non-blocking
    await waitFor(() =>
      expect(container.querySelector('[data-slot="dns-challenge"]')).toBeFalsy(),
    );
    // claim still pending in the table + still auto-polling
    expect(screen.getByText("acme.com")).toBeInTheDocument();
    expect(screen.getByText("Pending DNS")).toBeInTheDocument();
    await waitFor(() => expect(verifyCalls).toBeGreaterThanOrEqual(1));
  });
});

describe("dns-verify-softeners — notify-me opt-in (M10)", () => {
  it("test_notify_optin_calls_backend_no_email", async () => {
    // covers: M10 — toggle POSTs …/notify with NO email; toggle off → DELETE …/notify.
    const user = userEvent.setup();
    const captured: { body: string | null; deleteCalled: boolean } = {
      body: null,
      deleteCalled: false,
    };
    server.use(
      claimsGet([makeClaim({ claim_id: "claim-new", domain: "acme.com" })]),
      createHandler("acme.com"),
      registrarHint({ registrar: null, deep_link_url: null, fallback: true }),
      http.post(notifyUrl("claim-new"), async ({ request }) => {
        captured.body = await request.text();
        return HttpResponse.json(
          makeClaim({
            claim_id: "claim-new",
            domain: "acme.com",
            notify_requested_at: "2026-07-20T00:00:00Z",
          }),
        );
      }),
      http.delete(notifyUrl("claim-new"), () => {
        captured.deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const { container } = renderConsole(NO_POLL);
    await openChallengeCard(user);

    const optin = container.querySelector('[data-slot="notify-optin"]') as HTMLElement | null;
    expect(optin).toBeTruthy();
    const toggle = within(optin!).getByRole("button", { name: /email me when it'?s live/i });

    await user.click(toggle);

    // POST fired with an empty body — never an email field (server-derived recipient)
    await waitFor(() => expect(captured.body).not.toBeNull());
    const raw = captured.body ?? "";
    const parsed = raw.length ? JSON.parse(raw) : {};
    expect("email" in parsed).toBe(false);
    expect(Object.keys(parsed).length).toBe(0);

    // opted-in state reflected
    await waitFor(() =>
      expect(within(optin!).getByText(/we'?ll email you|opted in|you'?ll be notified/i)).toBeInTheDocument(),
    );

    // toggling off → DELETE …/notify
    await user.click(within(optin!).getByRole("button", { name: /email me when it'?s live|cancel notification|stop|opted in/i }));
    await waitFor(() => expect(captured.deleteCalled).toBe(true));
  });
});
