/**
 * tests/signup-domain-aware-routing.test.tsx — RED suite for
 * `domain-aware-auth-routing` TASK.md §3 (FROZEN @ v1), the COMPONENT half:
 * the data-domain-class observable, the three static lead-in strings, the
 * per-class route ORDER, the presence-in-every-class floor (M6/R5), and the two
 * behavioural proofs of the M11 anti-enumeration invariant:
 *
 *   (P1) a domain WITH a seeded verified claim and one WITHOUT produce
 *        BYTE-IDENTICAL panel markup
 *   (P2) fetch / XMLHttpRequest / navigator.sendBeacon are called ZERO times
 *        across a full character-by-character type-in of an email address
 *
 * Persona: Application Security Engineer (`.add/personas/appsec-engineer.md`).
 * Its Default Requirement — "verified against BOTH failure directions by
 * default" — is why this suite probes for the LEAK (P1/P2) *and* for the
 * feature silently not working (a classifier stuck on "unknown" leaks nothing
 * and is also useless; every P1/P2 test therefore also asserts the class it
 * expects, so neither test can pass on an unbuilt feature).
 *
 * A failure of P1 or P2 is a SECURITY HARD-STOP (§3), never a flaky retry.
 *
 * RED reason (today, before Build): SignupForm's shipped
 * [data-slot="signup-alt-routes"] panel carries no `data-domain-class`
 * attribute, renders no lead-in line, and never reorders its three routes —
 * so every class/lead-in/order assertion below fails on a missing attribute or
 * absent text. P1 and P2 additionally assert the class attribute alongside
 * their invariant, so they too are red today rather than passing vacuously on
 * a component that simply does nothing.
 *
 * FROZEN NEIGHBOUR (re-verified by reading, never modified): the three routes'
 * own copy, hrefs, and submit behavior belong to signup-refusal-router §3 v1
 * and are asserted here ONLY as "still present / still identical", never
 * rewritten. tests/signup-refusal-router.test.tsx remains the owner of their
 * behavior and is untouched by this task.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { SignupForm } from "@/components/auth/SignupForm";

const APP = "http://localhost:3000";

/** §3 names this anchor verbatim — not an invented internal. */
function getPanel(container: HTMLElement): HTMLElement {
  const panel = container.querySelector('[data-slot="signup-alt-routes"]');
  if (!panel) throw new Error('Panel [data-slot="signup-alt-routes"] not found');
  return panel as HTMLElement;
}

function mainSignupEmailInput(container: HTMLElement): HTMLInputElement {
  const el = container.querySelector("#signup_email");
  if (!el) throw new Error("main signup email input (#signup_email) not found");
  return el as HTMLInputElement;
}

/** The three frozen routes, located by their OWN observable copy (M9). */
function routeNodes(panel: HTMLElement) {
  const sso = within(panel).getByRole("link", { name: /sign in with sso instead/i });
  const invite = within(panel).getByText(/have an invite link/i);
  const requestAccess = within(panel).getByRole("button", { name: /request access/i });
  return { sso, invite, requestAccess };
}

/** The three routes' names, in DOM order. */
function routeOrder(panel: HTMLElement): string[] {
  const { sso, invite, requestAccess } = routeNodes(panel);
  return (
    [
      ["sso", sso],
      ["invite", invite],
      ["request-access", requestAccess],
    ] as const
  )
    .slice()
    .sort(([, a], [, b]) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    )
    .map(([name]) => name);
}

const PUBLIC_LEAD_IN = "Looks like a personal address — you can create your own workspace.";
const CORPORATE_LEAD_IN = "If your team already uses Hydroa, here's how to get in.";

/**
 * Seed the server so it WOULD answer differently per domain. If the panel ever
 * consulted the server, these handlers are what would make P1's two renders
 * diverge — and P2's counters are what would catch the request. Seeding a real
 * asymmetry is what makes the invariant tests evidence rather than decoration.
 */
function seedAsymmetricServerKnowledge() {
  server.use(
    // "acme-corp.com" — a tenant with a VERIFIED domain claim + OIDC configured
    http.get(`${APP}/api/auth/oidc/login`, ({ request }) => {
      const domain = new URL(request.url).searchParams.get("domain");
      if (domain === "acme-corp.com") {
        return new HttpResponse(null, { status: 302, headers: { Location: "https://idp.example/authorize" } });
      }
      // "nobody-here.example" — a stranger to the platform
      return HttpResponse.json({ code: "ERR_OIDC_NOT_CONFIGURED", status: 404 }, { status: 404 });
    }),
  );
}

describe("SignupForm — data-domain-class + lead-in (M1, M2, M7, M8)", () => {
  const user = userEvent.setup();

  it("test_public_address_classifies_public_and_leads_with_self_serve", async () => {
    // covers: M1, M2, M7 — "Bob@GMAIL.com" -> public; the self-serve lead-in is
    // presented first, SSO is de-emphasized (present, ordered last).
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(container), "Bob@GMAIL.com");

    const panel = getPanel(container);
    await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "public"));

    const leadIn = within(panel).getByText(PUBLIC_LEAD_IN);
    expect(leadIn).toBeInTheDocument();

    // The lead-in precedes all three routes.
    const { sso } = routeNodes(panel);
    expect(leadIn.compareDocumentPosition(sso) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // §3 public order: [self-serve emphasis, invite-link copy, request-access, SSO link]
    expect(routeOrder(panel)).toEqual(["invite", "request-access", "sso"]);
  });

  it("test_case_and_whitespace_do_not_change_the_class", async () => {
    // covers: M2 — "  bob@GmAiL.CoM  " classifies identically to "bob@gmail.com"
    const first = render(<SignupForm />);
    await user.type(mainSignupEmailInput(first.container), "  bob@GmAiL.CoM  ");
    await waitFor(() =>
      expect(getPanel(first.container)).toHaveAttribute("data-domain-class", "public"),
    );
    first.unmount();

    const second = render(<SignupForm />);
    await user.type(mainSignupEmailInput(second.container), "bob@gmail.com");
    await waitFor(() =>
      expect(getPanel(second.container)).toHaveAttribute("data-domain-class", "public"),
    );
  });

  it("test_corporate_address_leads_with_the_three_team_routes", async () => {
    // covers: M4, M8 — "dana@acme-corp.com" -> corporate; order SSO -> invite ->
    // request-access, with the corporate lead-in line.
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(container), "dana@acme-corp.com");

    const panel = getPanel(container);
    await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "corporate"));

    expect(within(panel).getByText(CORPORATE_LEAD_IN)).toBeInTheDocument();
    expect(routeOrder(panel)).toEqual(["sso", "invite", "request-access"]);
  });

  it("test_nothing_typed_renders_the_neutral_panel_with_no_lead_in", async () => {
    // covers: M5, R3 (ROUTE_NEUTRAL_NO_DOMAIN) — class "unknown", today's
    // shipped order, no lead-in, and NO error/hint/warning shown to the visitor.
    const { container } = render(<SignupForm />);
    const panel = getPanel(container);

    expect(panel).toHaveAttribute("data-domain-class", "unknown");
    expect(within(panel).queryByText(PUBLIC_LEAD_IN)).not.toBeInTheDocument();
    expect(within(panel).queryByText(CORPORATE_LEAD_IN)).not.toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();

    // Today's shipped order, byte-identical.
    expect(routeOrder(panel)).toEqual(["sso", "invite", "request-access"]);
  });

  it("test_malformed_domain_falls_back_to_neutral_unknown", async () => {
    // covers: M5, R4 (ROUTE_NEUTRAL_MALFORMED_DOMAIN) — the §2 scenario list,
    // each one re-rendered fresh. Never an error to the visitor, never a guess.
    for (const raw of ["bob@localhost", "bob@192.168.1.1", "bob@-acme.com"]) {
      const { container, unmount } = render(<SignupForm />);
      await user.type(mainSignupEmailInput(container), raw);

      const panel = getPanel(container);
      await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "unknown"));
      expect(within(panel).queryByText(PUBLIC_LEAD_IN)).not.toBeInTheDocument();
      expect(within(panel).queryByText(CORPORATE_LEAD_IN)).not.toBeInTheDocument();
      expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("test_subdomain_of_public_provider_renders_corporate", async () => {
    // covers: M3/M4 edge case — exact match only, at the render layer
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(container), "bob@mail.gmail.com");
    await waitFor(() =>
      expect(getPanel(container)).toHaveAttribute("data-domain-class", "corporate"),
    );
  });
});

describe("SignupForm — M6/R5: all three routes stay PRESENT in every class", () => {
  const user = userEvent.setup();

  it("test_all_three_routes_present_in_public_corporate_and_unknown", async () => {
    // covers: M6, R5 (ROUTE_HIDES_ROUTE) — classification changes only ORDER and
    // EMPHASIS, never presence. This is what keeps the FROZEN
    // signup-refusal-router M1 (unconditional render) true.
    const cases: Array<{ typed: string; cls: string }> = [
      { typed: "", cls: "unknown" },
      { typed: "bob@gmail.com", cls: "public" },
      { typed: "dana@acme-corp.com", cls: "corporate" },
    ];

    const counts: Record<string, number> = {};
    for (const { typed, cls } of cases) {
      const { container, unmount } = render(<SignupForm />);
      if (typed) await user.type(mainSignupEmailInput(container), typed);

      const panel = getPanel(container);
      await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", cls));

      const { sso, invite, requestAccess } = routeNodes(panel);
      expect(sso).toBeInTheDocument();
      expect(invite).toBeInTheDocument();
      expect(requestAccess).toBeInTheDocument();
      counts[cls] = [sso, invite, requestAccess].length;
      unmount();
    }

    expect(counts).toEqual({ unknown: 3, public: 3, corporate: 3 });
  });

  it("test_route_copy_href_and_behavior_stay_byte_identical", async () => {
    // covers: M9 — this task adds ONLY a lead-in line and an ordering. Each
    // route's own copy/href is asserted against the FROZEN
    // signup-refusal-router strings, in a class that reorders them (public),
    // so a reorder-by-rewrite would fail here.
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(container), "bob@gmail.com");

    const panel = getPanel(container);
    await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "public"));

    // Panel header — unchanged.
    expect(within(panel).getByText("Already have access another way?")).toBeInTheDocument();
    // Route (a): the SSO link's href derivation is untouched (?domain=<typed>).
    expect(within(panel).getByRole("link", { name: "Sign in with SSO instead" })).toHaveAttribute(
      "href",
      "/login?domain=gmail.com",
    );
    // Route (b): the invite-link copy, verbatim.
    expect(
      within(panel).getByText("Have an invite link? Open it in this browser to join your team."),
    ).toBeInTheDocument();
    // Route (c): the request-access mini-form, still one input + its button.
    expect(within(panel).getAllByRole("textbox")).toHaveLength(1);
    expect(within(panel).getByRole("button", { name: "Request access" })).toBeInTheDocument();
  });
});

/**
 * (P1) THE M11 INVARIANT — byte-identical markup regardless of server state.
 */
describe("P1 — a claimed domain and a stranger domain render byte-identically (M11)", () => {
  const user = userEvent.setup();

  it("test_p1_claimed_and_unclaimed_domain_panels_are_byte_identical", async () => {
    // covers: M11 — "Acme" holds a VERIFIED claim on acme-corp.com;
    // nobody-here.example has no tenant, no claim, no SSO config. The server is
    // seeded to answer DIFFERENTLY for the two (302 vs 404), so a panel that
    // consulted it would diverge here.
    seedAsymmetricServerKnowledge();

    async function renderPanelHtml(domain: string): Promise<string> {
      const { container, unmount } = render(<SignupForm />);
      await user.type(mainSignupEmailInput(container), `dana@${domain}`);
      const panel = getPanel(container);
      // BOTH must be corporate — otherwise "identical" could be satisfied by a
      // classifier that never classifies at all (the useless-but-leak-free
      // failure direction).
      await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "corporate"));
      const html = panel.outerHTML;
      unmount();
      // Normalize ONLY the domain string the visitor themselves typed, which §3
      // explicitly exempts ("apart from the domain string echoed back").
      return html.split(domain).join("«TYPED-DOMAIN»");
    }

    const claimed = await renderPanelHtml("acme-corp.com");
    const stranger = await renderPanelHtml("nobody-here.example");

    expect(claimed).toBe(stranger);
    // Guard the comparison itself against passing on two empty strings.
    expect(claimed).toContain('data-domain-class="corporate"');
    expect(claimed.length).toBeGreaterThan(200);
  });
});

/**
 * (P2) ZERO NETWORK CALLS — R2 / ROUTE_NETWORK_PROBE_FORBIDDEN.
 */
describe("P2 — classification issues no network request (M10, R2)", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;
  let xhrOpenSpy: ReturnType<typeof vi.spyOn>;
  let beaconCalls: number;

  beforeEach(() => {
    beaconCalls = 0;
    fetchSpy = vi.spyOn(globalThis, "fetch");
    xhrOpenSpy = vi.spyOn(XMLHttpRequest.prototype, "open");
    // jsdom does not implement sendBeacon; define a counting stub either way so
    // a future call would be recorded rather than silently throwing.
    Object.defineProperty(globalThis.navigator, "sendBeacon", {
      configurable: true,
      writable: true,
      value: () => {
        beaconCalls += 1;
        return true;
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("test_p2_zero_requests_across_a_full_character_by_character_type_in", async () => {
    // covers: M10, R2, and the ADVERSARY scenario's core claim — no outbound
    // request is issued for ANY typed domain. The server is seeded to answer
    // asymmetrically, so a probe would have something to learn if one were made.
    seedAsymmetricServerKnowledge();
    const user = userEvent.setup();

    const { container } = render(<SignupForm />);
    const emailInput = mainSignupEmailInput(container);
    const panel = getPanel(container);

    // Type the whole address one character at a time — userEvent.type already
    // dispatches per-character key events; assert the class after EVERY
    // character so the classification is genuinely exercised at each keystroke.
    const address = "dana@acme-corp.com";
    for (const ch of address) {
      await user.type(emailInput, ch);
      // Array-contains rather than a regex: getAttribute returns null when the
      // attribute is absent, and `toMatch(regex)` on null throws a TypeError —
      // which would make this test red for a HARNESS reason instead of the
      // missing-implementation reason it must be red for.
      expect(["public", "corporate", "unknown"]).toContain(
        panel.getAttribute("data-domain-class"),
      );
    }

    // The feature actually worked (not the useless-but-silent failure direction).
    expect(emailInput).toHaveValue(address);
    await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "corporate"));

    // …and it cost ZERO network calls. Counts recorded for the §6 evidence line.
    expect({
      fetch: fetchSpy.mock.calls.length,
      xhrOpen: xhrOpenSpy.mock.calls.length,
      sendBeacon: beaconCalls,
    }).toEqual({ fetch: 0, xhrOpen: 0, sendBeacon: 0 });
  });

  it("test_p2_adversary_probing_many_domains_issues_no_request", async () => {
    // covers: M11, R2 — THE ADVERSARY CASE, scaled down. A list of candidate
    // domains, some seeded as customers and some as strangers, produces renders
    // that differ ONLY by shape class — which the attacker could compute offline
    // from the public provider list alone.
    seedAsymmetricServerKnowledge();
    const user = userEvent.setup();

    const candidates = [
      { domain: "acme-corp.com", cls: "corporate" }, // seeded: verified claim + OIDC
      { domain: "nobody-here.example", cls: "corporate" }, // stranger
      { domain: "gmail.com", cls: "public" },
      { domain: "not-a-domain", cls: "unknown" },
    ];

    for (const { domain, cls } of candidates) {
      const { container, unmount } = render(<SignupForm />);
      await user.type(mainSignupEmailInput(container), `probe@${domain}`);
      await waitFor(() => expect(getPanel(container)).toHaveAttribute("data-domain-class", cls));
      unmount();
    }

    expect({
      fetch: fetchSpy.mock.calls.length,
      xhrOpen: xhrOpenSpy.mock.calls.length,
      sendBeacon: beaconCalls,
    }).toEqual({ fetch: 0, xhrOpen: 0, sendBeacon: 0 });
  });

  it("test_p2_sso_preflight_is_not_invoked_by_classification", async () => {
    // covers: R2 — the shipped handleSso preflight (a live per-domain existence
    // oracle, §0 R-a) is NOT reused as a classification signal. It fires only on
    // an explicit SSO click, which this task does not change. Asserted as: the
    // SSO route is a plain <a href>, not a click-handler that probes.
    const user = userEvent.setup();
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(container), "dana@acme-corp.com");

    const panel = getPanel(container);
    await waitFor(() => expect(panel).toHaveAttribute("data-domain-class", "corporate"));

    const sso = within(panel).getByRole("link", { name: /sign in with sso instead/i });
    expect(sso.tagName).toBe("A");
    expect(sso).toHaveAttribute("href", "/login?domain=acme-corp.com");
    expect(fetchSpy.mock.calls.length).toBe(0);
  });
});
