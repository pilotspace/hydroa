/**
 * tests/unified-signin-entry.test.tsx — RED suite for `unified-signin-entry`
 * TASK.md §3 (FROZEN @ v1): the `/login` surface gains a derived
 * `data-domain-class`, a per-class lead-in + affordance ORDER, a pristine-only
 * SSO auto-seed, and a new always-present "Create a workspace" route — while
 * password/SSO/SAML stay PRESENT, byte-identical in copy/href, in every class
 * (M9/R5 — this is what keeps login.test.tsx / sso-login.test.tsx /
 * saml-login-affordance.test.tsx / login-domain-query-seed.test.tsx green
 * WITHOUT being edited).
 *
 * Persona: Frontend Engineer (`.add/personas/frontend-engineer.md`), with
 * appsec-engineer as the advisor lens for the Q1-Q4 anti-enumeration checks —
 * mirrors tests/signup-domain-aware-routing.test.tsx's own P1-P4 proof shape
 * (domain-aware-auth-routing §3), adapted to LoginForm's entry-routing surface.
 *
 * Non-vacuity (§3): every Q1/Q2 test also asserts the expected
 * data-domain-class, so none of them can pass against a LoginForm that does
 * nothing at all.
 *
 * RED reason (today, before Build): LoginForm renders no
 * [data-slot="login-entry-routes"] region, no data-domain-class, no lead-in,
 * no create-workspace link, and does not reorder or auto-seed anything — so
 * every region/order/href/auto-seed assertion below fails on a missing
 * attribute, missing node, or an SSO field that stays empty.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { readFileSync } from "node:fs";
import path from "node:path";
import { server } from "./mocks/server";
import { useSearchParams } from "next/navigation";

import { LoginForm } from "@/components/auth/LoginForm";

const APP = "http://localhost:3000";
const OIDC_LOGIN = "/api/auth/oidc/login";

const REPO_ROOT = path.resolve(__dirname, "../../..");
const LOGIN_FORM_SRC = path.join(
  REPO_ROOT,
  "apps/dashboard/components/auth/LoginForm.tsx",
);

const CORPORATE_LEAD_IN =
  "If your team already uses Hydroa, sign in with your company account.";
const PUBLIC_LEAD_IN =
  "Looks like a personal address — sign in, or create your own workspace.";

/** §3 names this anchor verbatim — not an invented internal. */
function getRegion(container: HTMLElement): HTMLElement {
  const region = container.querySelector('[data-slot="login-entry-routes"]');
  if (!region) throw new Error('Region [data-slot="login-entry-routes"] not found');
  return region as HTMLElement;
}

function emailInput(container: HTMLElement): HTMLInputElement {
  const el = container.querySelector("#login_email");
  if (!el) throw new Error("login email input (#login_email) not found");
  return el as HTMLInputElement;
}

function ssoDomainInput(): HTMLInputElement {
  return screen.getByLabelText(/work email or domain/i) as HTMLInputElement;
}

/** The four affordances, located by their OWN observable copy (M9). */
function routeAnchors(region: HTMLElement) {
  const password = within(region).getByRole("button", { name: /^log in$/i });
  const sso = within(region).getByRole("button", { name: /sign in with sso/i });
  const saml = within(region).getByRole("button", { name: /sign in with saml/i });
  const createWorkspace = within(region).getByRole("link", { name: /create a workspace/i });
  return { password, sso, saml, createWorkspace };
}

/** The four affordances' names, in DOM order. */
function routeOrder(region: HTMLElement): string[] {
  const { password, sso, saml, createWorkspace } = routeAnchors(region);
  return (
    [
      ["password", password],
      ["sso", sso],
      ["saml", saml],
      ["create-workspace", createWorkspace],
    ] as const
  )
    .slice()
    .sort(([, a], [, b]) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    )
    .map(([name]) => name);
}

/**
 * Seed the relay so it WOULD answer differently for the two domains under
 * test. If entry-routing ever consulted the server, this asymmetry is what
 * would make Q1's two renders diverge — and what Q2's counters would catch.
 */
function seedAsymmetricServerKnowledge() {
  server.use(
    http.get(`${APP}${OIDC_LOGIN}`, ({ request }) => {
      const domain = new URL(request.url).searchParams.get("domain");
      if (domain === "acme-corp.com") {
        return new HttpResponse(null, {
          status: 302,
          headers: { Location: "https://idp.example/authorize" },
        });
      }
      return HttpResponse.json({ code: "ERR_OIDC_NOT_CONFIGURED" }, { status: 404 });
    }),
  );
}

function setDomainParam(value: string | null) {
  const params = new URLSearchParams();
  if (value !== null) params.set("domain", value);
  vi.mocked(useSearchParams).mockReturnValue(params as ReturnType<typeof useSearchParams>);
}

beforeEach(() => {
  setDomainParam(null);
  localStorage.clear();
});

describe("LoginForm — data-domain-class + lead-in + order (M1,M3,M4,M5,M6,M7)", () => {
  const user = userEvent.setup();

  it("test_corporate_email_classifies_corporate_and_orders_sso_first", async () => {
    // covers: M1,M3,M4,M5
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<LoginForm />);
    await user.type(emailInput(container), "dana@acme-corp.com");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));
    expect(within(region).getByText(CORPORATE_LEAD_IN)).toBeInTheDocument();
    expect(routeOrder(region)).toEqual(["sso", "saml", "password", "create-workspace"]);

    // Zero network requests for typing alone — the preflight was never clicked.
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("test_corporate_email_autofills_the_sso_domain_field", async () => {
    // covers: M10
    render(<LoginForm />);
    await user.type(emailInput(document.body), "dana@acme-corp.com");
    await waitFor(() => expect(ssoDomainInput()).toHaveValue("acme-corp.com"));
  });

  it("test_public_email_classifies_public_and_leads_with_create_workspace", async () => {
    // covers: M6,M8
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<LoginForm />);
    await user.type(emailInput(container), "Bob@GMAIL.com");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "public"));
    expect(within(region).getByText(PUBLIC_LEAD_IN)).toBeInTheDocument();
    expect(routeOrder(region)).toEqual(["create-workspace", "password", "sso", "saml"]);

    const { createWorkspace } = routeAnchors(region);
    expect(createWorkspace).toHaveAttribute("href", "/signup?email=Bob%40GMAIL.com");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("test_case_and_whitespace_do_not_change_the_class", async () => {
    // covers: M1 (inherited M2)
    const first = render(<LoginForm />);
    await user.type(emailInput(first.container), "  bob@GmAiL.CoM  ");
    await waitFor(() =>
      expect(getRegion(first.container)).toHaveAttribute("data-domain-class", "public"),
    );
    first.unmount();

    const second = render(<LoginForm />);
    await user.type(emailInput(second.container), "bob@gmail.com");
    await waitFor(() =>
      expect(getRegion(second.container)).toHaveAttribute("data-domain-class", "public"),
    );
    second.unmount();
  });

  it("test_non_customer_corporate_visitor_is_not_stranded", async () => {
    // covers: M8, R7 — THE COMMON CASE
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<LoginForm />);
    await user.click(emailInput(container));
    await user.type(emailInput(container), "dana@nobody-here.example");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));

    const { createWorkspace } = routeAnchors(region);
    expect(createWorkspace).toHaveAttribute(
      "href",
      "/signup?email=dana%40nobody-here.example&account_type=business",
    );

    // Keyboard-reachable from the email field, within a small bounded number of
    // tabs — never requires leaving the region to find it.
    let reached = false;
    for (let i = 0; i < 10 && !reached; i++) {
      await user.tab();
      reached = document.activeElement === createWorkspace;
    }
    expect(reached).toBe(true);

    // No request was issued that could have revealed "nobody-here.example" is a
    // stranger — classification and the link are both computed locally.
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("test_nothing_typed_renders_the_shipped_neutral_surface", async () => {
    // covers: M7, R3
    const { container } = render(<LoginForm />);
    const region = getRegion(container);

    expect(region).toHaveAttribute("data-domain-class", "unknown");
    expect(within(region).queryByText(CORPORATE_LEAD_IN)).not.toBeInTheDocument();
    expect(within(region).queryByText(PUBLIC_LEAD_IN)).not.toBeInTheDocument();
    expect(within(region).queryByRole("alert")).not.toBeInTheDocument();
    expect(routeOrder(region)).toEqual(["password", "sso", "saml", "create-workspace"]);
  });

  it("test_malformed_entry_falls_back_to_neutral_and_never_blocks_submit", async () => {
    // covers: R4, R-f
    const malformed = [
      "bob@localhost",
      "bob@192.168.1.1",
      "bob@ acme.com",
      "bob@-acme.com",
      `bob@${"a".repeat(300)}`,
    ];
    for (const raw of malformed) {
      const { container, unmount } = render(<LoginForm />);
      await user.type(emailInput(container), raw);

      const region = getRegion(container);
      await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "unknown"));
      expect(within(region).queryByRole("alert")).not.toBeInTheDocument();

      const loginButton = within(region).getByRole("button", { name: /^log in$/i });
      expect(loginButton).toBeEnabled();
      unmount();
    }
  });

  it("test_subdomain_of_public_provider_is_corporate", async () => {
    // covers: M1 edge (inherited)
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<LoginForm />);
    await user.type(emailInput(container), "bob@mail.gmail.com");
    await waitFor(() =>
      expect(getRegion(container)).toHaveAttribute("data-domain-class", "corporate"),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("LoginForm — M9/R5: every affordance stays present, byte-identical", () => {
  const user = userEvent.setup();

  it("test_all_affordances_present_in_public_corporate_and_unknown", async () => {
    // covers: M9, R5
    const cases: Array<{ typed: string; cls: string }> = [
      { typed: "", cls: "unknown" },
      { typed: "bob@gmail.com", cls: "public" },
      { typed: "dana@acme-corp.com", cls: "corporate" },
    ];

    const counts: Record<string, number> = {};
    for (const { typed, cls } of cases) {
      const { container, unmount } = render(<LoginForm />);
      if (typed) await user.type(emailInput(container), typed);

      const region = getRegion(container);
      await waitFor(() => expect(region).toHaveAttribute("data-domain-class", cls));

      // The password field and the "Work email or domain" field themselves
      // (not just their buttons) are asserted directly.
      expect(within(region).getByLabelText(/^password$/i)).toBeInTheDocument();
      expect(within(region).getByLabelText(/work email or domain/i)).toBeInTheDocument();

      const { password, sso, saml, createWorkspace } = routeAnchors(region);
      expect(password).toBeInTheDocument();
      expect(sso).toBeInTheDocument();
      expect(saml).toBeInTheDocument();
      expect(createWorkspace).toBeInTheDocument();
      counts[cls] = [password, sso, saml, createWorkspace].length;
      unmount();
    }

    expect(counts).toEqual({ unknown: 4, public: 4, corporate: 4 });
  });

  it("test_affordance_copy_href_and_handler_are_byte_identical", async () => {
    // covers: M9 — asserted in "corporate", the class that REORDERS them, so a
    // reorder-BY-REWRITE (rather than moving the whole subtree) fails here.
    const { container } = render(<LoginForm />);
    await user.type(emailInput(container), "dana@acme-corp.com");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));

    const passwordField = within(region).getByLabelText(/^password$/i);
    expect(passwordField).toHaveAttribute("id", "login_password");
    expect(passwordField).toHaveAttribute("type", "password");
    expect(passwordField).toHaveAttribute("autoComplete", "current-password");

    const ssoField = within(region).getByLabelText(/work email or domain/i);
    expect(ssoField).toHaveAttribute("id", "sso_domain");
    expect(ssoField).toHaveAttribute("placeholder", "you@company.com");

    expect(within(region).getByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Sign in with SSO" })).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Sign in with SAML" })).toBeInTheDocument();
  });
});

describe("LoginForm — M10/R6: pristine-only SSO auto-seed", () => {
  const user = userEvent.setup();

  it("test_visitor_typed_sso_domain_is_never_clobbered", async () => {
    // covers: M10, R6 — mirrors tests/sso-login.test.tsx:119-134's ordering.
    render(<LoginForm />);
    await user.type(ssoDomainInput(), "acme.com");
    await user.type(emailInput(document.body), "ada@acme.io");

    expect(ssoDomainInput()).toHaveValue("acme.com");
    const region = getRegion(document.body);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));
  });

  it("test_query_param_seeded_sso_domain_is_never_clobbered", async () => {
    // covers: M10, R6 — the non-clobber alone is already (trivially) true on
    // unbuilt code, since nothing there touches ssoDomain from email input at
    // all; also assert the region/classification the same keystrokes must
    // drive, so this is red on the missing region today, not vacuously green.
    setDomainParam("acme.com");
    const { container } = render(<LoginForm />);
    await waitFor(() => expect(ssoDomainInput()).toHaveValue("acme.com"));

    await user.type(emailInput(container), "ada@other-co.com");
    expect(ssoDomainInput()).toHaveValue("acme.com");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));
  });
});

/**
 * (Q1) THE M11 INVARIANT — byte-identical markup regardless of server state.
 */
describe("Q1 — a customer domain and a stranger domain render byte-identically (M11)", () => {
  const user = userEvent.setup();

  it("test_q1_customer_and_stranger_domain_surfaces_are_byte_identical", async () => {
    seedAsymmetricServerKnowledge();

    async function renderRegionHtml(domain: string): Promise<string> {
      const { container, unmount } = render(<LoginForm />);
      await user.type(emailInput(container), `dana@${domain}`);
      const region = getRegion(container);
      // BOTH must classify corporate — otherwise "identical" could pass on a
      // classifier that never classifies at all (the useless-but-leak-free
      // failure direction).
      await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));
      const html = region.outerHTML;
      unmount();
      return html.split(domain).join("«TYPED-DOMAIN»");
    }

    const claimed = await renderRegionHtml("acme-corp.com");
    const stranger = await renderRegionHtml("nobody-here.example");

    expect(claimed).toBe(stranger);
    expect(claimed).toContain('data-domain-class="corporate"');
    expect(claimed.length).toBeGreaterThan(200);
  });
});

/**
 * (Q2) ZERO NETWORK CALLS — R2 / ENTRY_NETWORK_PROBE_FORBIDDEN.
 */
describe("Q2 — classification/ordering/auto-seed issue no network request (M11,M12,R2)", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;
  let xhrOpenSpy: ReturnType<typeof vi.spyOn>;
  let beaconCalls: number;

  beforeEach(() => {
    beaconCalls = 0;
    fetchSpy = vi.spyOn(globalThis, "fetch");
    xhrOpenSpy = vi.spyOn(XMLHttpRequest.prototype, "open");
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

  it("test_q2_zero_requests_across_a_full_character_by_character_type_in", async () => {
    // covers: M11, M12, R2 — no outbound request for ANY typed character.
    seedAsymmetricServerKnowledge();
    const user = userEvent.setup();

    const { container } = render(<LoginForm />);
    const email = emailInput(container);
    const region = getRegion(container);

    const address = "dana@acme-corp.com";
    for (const ch of address) {
      await user.type(email, ch);
      expect(["public", "corporate", "unknown"]).toContain(
        region.getAttribute("data-domain-class"),
      );
    }

    expect(email).toHaveValue(address);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));

    expect({
      fetch: fetchSpy.mock.calls.length,
      xhrOpen: xhrOpenSpy.mock.calls.length,
      sendBeacon: beaconCalls,
    }).toEqual({ fetch: 0, xhrOpen: 0, sendBeacon: 0 });
  });

  it("test_q2_adversary_probing_many_domains_issues_no_request", async () => {
    // covers: M11 — THE ADVERSARY CASE, scaled down.
    seedAsymmetricServerKnowledge();
    const user = userEvent.setup();

    const candidates = [
      { domain: "acme-corp.com", cls: "corporate" },
      { domain: "nobody-here.example", cls: "corporate" },
      { domain: "gmail.com", cls: "public" },
      { domain: "not-a-domain", cls: "unknown" },
    ];

    for (const { domain, cls } of candidates) {
      const { container, unmount } = render(<LoginForm />);
      await user.type(emailInput(container), `probe@${domain}`);
      await waitFor(() =>
        expect(getRegion(container)).toHaveAttribute("data-domain-class", cls),
      );
      unmount();
    }

    expect({
      fetch: fetchSpy.mock.calls.length,
      xhrOpen: xhrOpenSpy.mock.calls.length,
      sendBeacon: beaconCalls,
    }).toEqual({ fetch: 0, xhrOpen: 0, sendBeacon: 0 });
  });

  it("test_q2_preflight_fires_only_on_an_explicit_sso_click_and_is_byte_unchanged", async () => {
    // covers: M12 — the preflight stays bound to exactly one deliberate click,
    // with SSO_PREFLIGHT_TIMEOUT_MS/SSO_NOT_CONFIGURED_MSG byte-unchanged.
    const assign = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...originalLocation, assign, href: "http://localhost:3000/login", origin: "http://localhost:3000" },
    });

    server.use(
      http.get(`${APP}${OIDC_LOGIN}`, () =>
        new HttpResponse(null, { status: 302, headers: { location: "https://idp.example/authorize" } }),
      ),
    );

    const user = userEvent.setup();
    const { container } = render(<LoginForm />);
    await user.type(emailInput(container), "dana@acme-corp.com");

    // Typing alone (which also drives the auto-seed) fires nothing.
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /sign in with sso/i }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme-corp.com`));
    expect(fetchSpy.mock.calls.length).toBe(1);

    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
  });
});

/**
 * (Q3) STATIC REACHABILITY — the entry-routing path names no lookup/probe.
 */
describe("Q3 — LoginForm's entry-routing path reaches no lookup, probe, or debounce timer (R1,R2)", () => {
  function loginFormSource(): string {
    return readFileSync(LOGIN_FORM_SRC, "utf8");
  }
  function codeOnly(src: string): string {
    return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  }
  function extractFunctionSource(code: string, signature: string): string {
    const start = code.indexOf(signature);
    if (start === -1) throw new Error(`function not found: ${signature}`);
    const braceStart = code.indexOf("{", start);
    let depth = 0;
    let i = braceStart;
    for (; i < code.length; i++) {
      if (code[i] === "{") depth++;
      else if (code[i] === "}") {
        depth--;
        if (depth === 0) {
          i++;
          break;
        }
      }
    }
    return code.slice(start, i);
  }

  it("test_q3_entry_routing_path_reaches_no_lookup_probe_or_debounce_timer", () => {
    const code = codeOnly(loginFormSource());

    // Classification is IMPORTED from the frozen, IO-free module — never
    // re-derived, never a second classifier.
    expect(code).toMatch(
      /import\s*{[^}]*classifyEmailDomain[^}]*}\s*from\s*["']@\/lib\/email-domain-routing["']/,
    );
    expect(code).toMatch(
      /import\s*{[^}]*normalizeEmailDomain[^}]*}\s*from\s*["']@\/lib\/email-domain-routing["']/,
    );

    // The derived value is computed directly in the render body — no timer.
    expect(code).toMatch(/const\s+entryClass[^=]*=\s*classifyEmailDomain\(email\)/);
    expect(code).not.toMatch(/setTimeout/);
    expect(code).not.toMatch(/debounce/i);

    // Classification/normalization is NEVER reached from inside the SSO
    // preflight, the SAML navigator, or the login submit handler — those
    // remain the ONLY fetch/navigation call sites, and none of them may
    // become a classification signal.
    const handleSsoSrc = extractFunctionSource(code, "async function handleSso()");
    const handleSamlSrc = extractFunctionSource(code, "function handleSamlSso()");
    const handleSubmitSrc = extractFunctionSource(code, "async function handleSubmit(");

    for (const fnSrc of [handleSsoSrc, handleSamlSrc, handleSubmitSrc]) {
      expect(fnSrc).not.toContain("classifyEmailDomain");
      expect(fnSrc).not.toContain("normalizeEmailDomain");
    }
  });
});

/**
 * (a11y) — M4, R-h.
 */
describe("LoginForm — a11y: the corporate path is reachable without guessing (M4,R-h)", () => {
  const user = userEvent.setup();

  it("test_screen_reader_reaches_sso_without_guessing", async () => {
    const { container } = render(<LoginForm />);
    await user.click(emailInput(container));
    await user.type(emailInput(container), "dana@acme-corp.com");

    const region = getRegion(container);
    await waitFor(() => expect(region).toHaveAttribute("data-domain-class", "corporate"));

    const leadIn = within(region).getByText(CORPORATE_LEAD_IN);
    // aria-live="polite" as a PROPERTY — never role="status" (PROJECT.md v40
    // reserves that role for the transient loading spinner).
    expect(leadIn).toHaveAttribute("aria-live", "polite");
    expect(leadIn).not.toHaveAttribute("role", "status");

    // The region is programmatically associated with its lead-in.
    const describedBy = region.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const describedNode = container.querySelector(`#${describedBy}`);
    expect(describedNode).toBe(leadIn);

    // The SSO field is described as optional and auto-filled.
    const ssoField = ssoDomainInput();
    const ssoDescribedBy = ssoField.getAttribute("aria-describedby") ?? "";
    const ssoDescriptionText = ssoDescribedBy
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => container.querySelector(`#${id}`)?.textContent ?? "")
      .join(" ");
    expect(ssoDescriptionText).toMatch(/optional/i);
    expect(ssoDescriptionText).toMatch(/auto-?fill/i);

    // Reachable by keyboard within a small bounded number of tabs.
    let reached = false;
    for (let i = 0; i < 6 && !reached; i++) {
      await user.tab();
      reached = document.activeElement === screen.getByRole("button", { name: /sign in with sso/i });
    }
    expect(reached).toBe(true);
  });
});
