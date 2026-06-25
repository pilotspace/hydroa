/**
 * marketing-shell.test.tsx — RED suite for the v38 marketing shell + /app route split
 *
 * Covers all 9 §4 test-plan items from TASK.md (frozen contract v1).
 * All tests MUST fail for the right reason (missing implementation) before Build.
 *
 * Suite: "legacy" project (tests/ directory, tests/setup.ts).
 *
 * RED expectations:
 *   - test_root_is_public:              MarketingShell component doesn't exist yet
 *   - test_marketing_uses_marketing_shell: MarketingShell doesn't exist yet
 *   - test_app_overview_authed:         app/(app)/page.tsx doesn't exist yet
 *   - test_proxy_guards_app:            proxy.ts matcher still has old paths (/keys, /usage)
 *   - test_nav_targets_under_app:       NAV_ITEMS still point to bare /keys /usage etc.
 *   - test_legacy_path_gone:            bare refs still exist in app-shell.tsx
 *   - test_marketing_a11y:              MarketingShell doesn't exist yet
 *   - test_reject_anon_app:             proxy.ts matcher doesn't cover /app yet
 *   - test_reject_public_not_gated:     marketing layout doesn't exist yet
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextRequest } from "next/server";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import React from "react";

// ── Module-level vi.mock for next/headers (used in marketing layout) ──────────
vi.mock("next/headers", () => ({
  cookies: vi.fn(() => ({
    get: vi.fn(() => undefined),
  })),
}));

// ── Helpers ────────────────────────────────────────────────────────────────────
const DASHBOARD_ROOT = resolve(__dirname, "..");

function readSource(relPath: string): string {
  const abs = resolve(DASHBOARD_ROOT, relPath);
  if (!existsSync(abs)) return "";
  return readFileSync(abs, "utf-8");
}

// ─────────────────────────────────────────────────────────────────────────────
// test_root_is_public
// The marketing root must render without a cookie check or redirect.
// RED: MarketingShell doesn't exist yet → import fails or component is missing.
// ─────────────────────────────────────────────────────────────────────────────
describe("test_root_is_public", () => {
  it("MarketingShell renders without cookie read and shows landing content", async () => {
    // Dynamic import so a missing module gives a clean test error
    const { MarketingShell } = await import("@/components/marketing-shell");
    const { container } = render(
      <MarketingShell>
        <main id="main">
          <h1>Welcome to Hydroa</h1>
        </main>
      </MarketingShell>
    );
    // Must render the landmark structure
    expect(container.querySelector("header")).not.toBeNull();
    expect(container.querySelector("footer")).not.toBeNull();
    // Children are rendered
    expect(screen.getByRole("heading", { name: /welcome to hydroa/i })).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_marketing_uses_marketing_shell — AND NOT DashboardShell
// ─────────────────────────────────────────────────────────────────────────────
describe("test_marketing_uses_marketing_shell", () => {
  it("marketing layout exports MarketingShell chrome (banner/contentinfo), not DashboardShell", async () => {
    const { MarketingShell } = await import("@/components/marketing-shell");
    const { container } = render(
      <MarketingShell>
        <main id="main">content</main>
      </MarketingShell>
    );
    // WCAG landmarks: banner (header) and contentinfo (footer)
    const header = container.querySelector("[role='banner'], header");
    const footer = container.querySelector("[role='contentinfo'], footer");
    expect(header).not.toBeNull();
    expect(footer).not.toBeNull();

    // The rendered output must NOT contain the DashboardShell/AppShell sidebar classes
    // (the sidebar uses data-slot="sidebar" and "Primary" aria-label)
    const sidebar = container.querySelector("[aria-label='Primary']");
    expect(sidebar).toBeNull();
  });

  it("marketing layout source does NOT import DashboardShell", () => {
    const src = readSource("app/(marketing)/layout.tsx");
    expect(src).not.toBe(""); // file must exist
    // Must not have an import statement for DashboardShell
    expect(src).not.toMatch(/import[^;]*DashboardShell/);
    expect(src).not.toMatch(/from ["'].*dashboard-shell["']/);
  });

  it("marketing layout source does NOT call cookies()", () => {
    const src = readSource("app/(marketing)/layout.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/cookies\(\)/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_app_overview_authed
// /app should be the relocated Overview under app/(app)/page.tsx
// ─────────────────────────────────────────────────────────────────────────────
describe("test_app_overview_authed", () => {
  it("app/(app)/app/page.tsx exists and renders OverviewPage", () => {
    const src = readSource("app/(app)/app/page.tsx");
    expect(src).not.toBe(""); // file must exist
    expect(src).toContain("OverviewPage");
  });

  it("app/(app)/app/layout.tsx exists and wraps DashboardShell", () => {
    const src = readSource("app/(app)/app/layout.tsx");
    expect(src).not.toBe(""); // file must exist
    expect(src).toContain("DashboardShell");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_proxy_guards_app
// proxy.ts matcher must cover /app and /app/:path* (not the old /keys /usage)
// ─────────────────────────────────────────────────────────────────────────────
describe("test_proxy_guards_app", () => {
  it("proxy redirects /app/keys to /login when no cookie", async () => {
    const { proxy } = await import("@/proxy");
    const req = new NextRequest("http://localhost:3000/app/keys", { method: "GET" });
    const res = proxy(req);
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("proxy passes through /app/keys when cookie present", async () => {
    const { proxy } = await import("@/proxy");
    const req = new NextRequest("http://localhost:3000/app/keys", {
      method: "GET",
      headers: { Cookie: "ai_proxy_session=some.jwt.token" },
    });
    const res = proxy(req);
    expect(res.status).not.toBe(307);
    expect(res.headers.get("location")).toBeNull();
  });

  it("proxy config.matcher includes /app and /app/:path*", async () => {
    const { config } = await import("@/proxy");
    expect(config.matcher).toContain("/app");
    expect(config.matcher).toContain("/app/:path*");
  });

  it("proxy config.matcher does NOT include old /keys or /usage entries", async () => {
    const { config } = await import("@/proxy");
    // The old matcher entries must be gone — legacy paths are hard-cut 404
    const matcherStr = JSON.stringify(config.matcher);
    expect(matcherStr).not.toContain('"/keys"');
    expect(matcherStr).not.toContain('"/usage"');
    expect(matcherStr).not.toContain('"/keys/:path*"');
    expect(matcherStr).not.toContain('"/usage/:path*"');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_nav_targets_under_app
// Every NAV_ITEMS href in AppShell must start with /app
// ─────────────────────────────────────────────────────────────────────────────
describe("test_nav_targets_under_app", () => {
  it("all AppShell NAV_ITEMS hrefs start with /app", async () => {
    // Read the source to find the NAV_ITEMS array (pattern check on the source
    // is more reliable than runtime inspection in jsdom environment)
    const src = readSource("components/ui/app-shell.tsx");
    expect(src).not.toBe("");

    // Extract hrefs from the NAV_ITEMS array via a simple regex
    // Match: href: "/something"
    const hrefPattern = /href:\s*["']\/([^"']+)["']/g;
    const matches = [...src.matchAll(hrefPattern)];
    expect(matches.length).toBeGreaterThan(0);

    for (const match of matches) {
      const href = "/" + match[1];
      expect(href).toMatch(/^\/app\//);
    }
  });

  it("DashboardShell renders nav links that all point under /app", async () => {
    vi.mock("next/navigation", () => ({ usePathname: vi.fn(() => "/app") }));
    vi.mock("@/lib/hooks/use-current-user", () => ({
      useCurrentUser: vi.fn(() => ({
        data: { role: "owner", email: "ada@hydroa.io", user_id: "u1", tenant_id: "t1", exp: null },
        isLoading: false,
        isError: false,
      })),
    }));

    const { DashboardShell } = await import("@/components/dashboard-shell");
    render(
      <DashboardShell>
        <div>content</div>
      </DashboardShell>
    );

    const links = screen.getAllByRole("link");
    const navLinks = links.filter((l) => l.getAttribute("href")?.startsWith("/"));
    for (const link of navLinks) {
      const href = link.getAttribute("href")!;
      expect(href).toMatch(/^\/app\//);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_legacy_path_gone
// No internal reference to bare /keys /usage /spend /teams /routing /alerts /health /models /settings
// ─────────────────────────────────────────────────────────────────────────────
describe("test_legacy_path_gone", () => {
  const LEGACY_PATHS = ["keys", "usage", "spend", "teams", "routing", "alerts", "health", "models", "settings"];

  it("app-shell.tsx NAV_ITEMS contain no bare /<route> hrefs (all must be /app/<route>)", () => {
    const src = readSource("components/ui/app-shell.tsx");
    expect(src).not.toBe("");

    for (const route of LEGACY_PATHS) {
      // Must NOT have href: "/<route>" (bare, without /app prefix)
      // The positive lookahead ensures we don't match /app/keys as /keys
      const barePattern = new RegExp(`href:\\s*["']\\/${route}["']`);
      expect(src, `Found bare /${route} href in app-shell.tsx`).not.toMatch(barePattern);
    }
  });

  it("LoginForm.tsx pushes to /app/keys (not bare /keys)", () => {
    const src = readSource("components/auth/LoginForm.tsx");
    expect(src).not.toBe("");
    // Must contain /app/keys push, NOT bare /keys push
    expect(src).not.toMatch(/router\.push\(["']\/keys["']\)/);
    expect(src).toMatch(/router\.push\(["']\/app[/"][^)]*\)/);
  });

  it("SignupForm.tsx pushes to /app/keys (not bare /keys)", () => {
    const src = readSource("components/auth/SignupForm.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/router\.push\(["']\/keys["']\)/);
    expect(src).toMatch(/router\.push\(["']\/app[/"][^)]*\)/);
  });

  it("old app/page.tsx does not exist (deleted — / is now public marketing)", () => {
    // The old authed page.tsx redirected to /login; it must be replaced
    // by the (marketing) page. We check the (marketing) page exists instead.
    const marketingPageExists = existsSync(resolve(DASHBOARD_ROOT, "app/(marketing)/page.tsx"));
    expect(marketingPageExists).toBe(true);

    // The old page had `redirect("/login")` — the (marketing)/page.tsx must NOT
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toContain('redirect("/login")');
    expect(src).not.toContain("ai_proxy_session");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_marketing_a11y
// axe (serious/critical) + skip-link + heading order on MarketingShell
// ─────────────────────────────────────────────────────────────────────────────
describe("test_marketing_a11y", () => {
  it("MarketingShell has a skip-link as the first focusable element", async () => {
    const { MarketingShell } = await import("@/components/marketing-shell");
    const { container } = render(
      <MarketingShell>
        <main id="main">
          <h1>Get started with Hydroa</h1>
          <p>The AI proxy for your team.</p>
        </main>
      </MarketingShell>
    );

    // The skip link must be the first <a> in DOM order
    const allLinks = container.querySelectorAll("a");
    const firstLink = allLinks[0];
    expect(firstLink).not.toBeNull();
    expect(firstLink.getAttribute("href")).toBe("#main");
  });

  it("MarketingShell has required WCAG landmarks: header, nav, main, footer", async () => {
    const { MarketingShell } = await import("@/components/marketing-shell");
    const { container } = render(
      <MarketingShell>
        <main id="main">
          <h1>Hydroa</h1>
        </main>
      </MarketingShell>
    );

    expect(container.querySelector("header")).not.toBeNull();
    expect(container.querySelector("nav")).not.toBeNull();
    expect(container.querySelector("footer")).not.toBeNull();
    // main is provided by the child in our fixture
    expect(container.querySelector("main#main")).not.toBeNull();
  });

  it("MarketingShell passes axe serious/critical (WCAG-AA)", async () => {
    const { MarketingShell } = await import("@/components/marketing-shell");
    const { container } = render(
      <MarketingShell>
        <main id="main">
          <h1>Welcome to Hydroa</h1>
          <p>AI proxy for enterprise teams.</p>
        </main>
      </MarketingShell>
    );
    // Use the same pattern as enterprise-ext.test.tsx: filter serious/critical,
    // disable color-contrast (jsdom has no canvas — true contrast is browser-only)
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical"
    );
    expect(seriousCritical).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_anon_app
// proxy() to /app with no cookie → 307 /login
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_anon_app", () => {
  it("proxy redirects anonymous request to /app → 307 /login", async () => {
    const { proxy } = await import("@/proxy");
    const req = new NextRequest("http://localhost:3000/app", { method: "GET" });
    const res = proxy(req);
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("proxy does NOT change the cookie/JWT contract — only checks presence", async () => {
    // The guard must use simple cookie-header string match (regex), not verify JWT
    const src = readSource("proxy.ts");
    expect(src).not.toBe("");
    // Must not import any JWT library
    expect(src).not.toContain("jsonwebtoken");
    expect(src).not.toContain("jose");
    // Must use simple cookie-presence regex (same as before)
    expect(src).toMatch(/ai_proxy_session=/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_public_not_gated
// Marketing layout must NOT import DashboardShell OR read cookies()
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_public_not_gated", () => {
  it("marketing layout module source does not import DashboardShell", () => {
    const src = readSource("app/(marketing)/layout.tsx");
    expect(src).not.toBe("");
    // Must not have an import statement for DashboardShell (comment refs are OK)
    expect(src).not.toMatch(/import[^;]*DashboardShell/);
    expect(src).not.toMatch(/from ["'].*dashboard-shell["']/);
  });

  it("marketing layout module source does not call cookies() from next/headers", () => {
    const src = readSource("app/(marketing)/layout.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/cookies\(\)/);
  });

  it("marketing shell component source does not import DashboardShell or AppShell", () => {
    const src = readSource("components/marketing-shell.tsx");
    expect(src).not.toBe("");
    // Must not have import statements for DashboardShell or AppShell (comment refs OK)
    expect(src).not.toMatch(/import[^;]*DashboardShell/);
    // The shell is a NEW component — it does NOT reuse AppShell
    expect(src).not.toMatch(/from ["'].*app-shell["']/);
    expect(src).not.toMatch(/import[^;]*AppShell/);
  });
});
