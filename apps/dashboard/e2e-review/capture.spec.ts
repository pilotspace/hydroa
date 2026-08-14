/**
 * capture.spec.ts — persona-driven screenshot + axe evidence capture for the
 * UI/UX review. Renders every in-scope surface per persona WITHOUT a gateway
 * (seeded session cookie + page.route() BFF interception), captures full-content
 * desktop + mobile screenshots (+ dark for a hero set), and runs axe-core per
 * route. All evidence is written to the session scratchpad.
 */

import { test, type Page, type BrowserContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as fs from "node:fs";
import * as path from "node:path";
import { gwBody } from "./fixtures";

const OUT =
  "/private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/00bd6205-726c-449e-b763-9b9b07f02062/scratchpad/ui-review";
const SHOTS = path.join(OUT, "shots");
const AXE = path.join(OUT, "axe");
for (const d of [SHOTS, AXE]) fs.mkdirSync(d, { recursive: true });

const DESKTOP = { width: 1440, height: 1000 };
const MOBILE = { width: 390, height: 844 };

type Identity = {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
  exp: number | null;
};

const IDS = {
  tenant: "00000000-0000-0000-0000-000000000099",
  detailTenant: "00000000-0000-0000-0000-000000000042",
};

function me(role: string, email: string): Identity {
  return { user_id: "00000000-0000-0000-0000-000000000001", tenant_id: IDS.tenant, email, role, exp: 9999999999 };
}

type Persona = {
  key: string;
  identity: Identity | null; // null = unauthenticated (public)
  routes: string[];
};

const PERSONAS: Persona[] = [
  {
    key: "1-prospect",
    identity: null,
    routes: [
      "/",
      "/pricing",
      "/docs",
      "/blog",
      "/status",
      "/legal/privacy",
      "/legal/terms",
      "/legal/security",
    ],
  },
  {
    key: "2-signup",
    identity: null,
    routes: ["/login", "/signup"],
  },
  {
    key: "3-developer",
    identity: me("member", "dev@acme.io"),
    routes: [
      "/app",
      "/app/chat",
      "/app/voice",
      "/app/memory",
      "/app/artifacts",
      "/app/vision",
      "/app/video",
      "/app/usage",
      "/app/spend",
      "/app/evals",
      "/app/evals/es_a1b2c3d4",
      "/app/evals/es_a1b2c3d4/runs/er_e5f6a7b8",
      "/app/keys",
      "/app/settings",
    ],
  },
  {
    key: "4-org-admin",
    identity: me("owner", "owner@acme.io"),
    routes: [
      "/app/models",
      "/app/teams",
      "/app/members",
      "/app/routing",
      "/app/batches",
      "/app/presets",
    ],
  },
  {
    key: "5-ops",
    identity: me("admin", "ops@acme.io"),
    routes: ["/app/health", "/app/slo", "/app/alerts", "/app/audit"],
  },
  {
    key: "6-superadmin",
    identity: me("superadmin", "root@platform.internal"),
    routes: [
      "/app",
      "/app/platform/tenants",
      `/app/platform/tenants/${IDS.detailTenant}`,
      "/app/platform/plans",
    ],
  },
];

// Dark-mode "hero" set — pages where dark polish matters most.
const DARK_HERO = new Set([
  "/",
  "/pricing",
  "/app",
  "/app/chat",
  "/app/usage",
  "/app/keys",
  "/app/models",
  "/app/platform/tenants",
]);

function slug(route: string): string {
  const s = route.replace(/^\//, "").replace(/\//g, "_").replace(/[^a-zA-Z0-9_.-]/g, "-");
  return s === "" ? "home" : s;
}

// Accumulated axe evidence, flushed in afterAll.
type AxeRow = {
  persona: string;
  route: string;
  violations: Array<{ id: string; impact: string; help: string; nodes: number; targets: string[] }>;
};
const axeRows: AxeRow[] = [];

async function seedIdentity(context: BrowserContext, identity: Identity | null): Promise<void> {
  if (!identity) return;
  await context.addCookies([
    { name: "ai_proxy_session", value: "e30.e30.fakesig", domain: "127.0.0.1", path: "/" },
  ]);
}

/** Intercept every same-origin API call so pages render without a gateway. */
async function stub(page: Page, identity: Identity | null): Promise<void> {
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method().toUpperCase();

    if (p === "/api/auth/me") {
      if (identity) return route.fulfill({ json: identity });
      return route.fulfill({ status: 401, json: { detail: "unauthenticated" } });
    }
    if (p.startsWith("/api/platform/impersonation")) {
      return route.fulfill({ json: { active: false } });
    }
    if (p.startsWith("/api/gw/")) {
      const gatewayPath = p.slice("/api/gw/".length);
      const body = gwBody(gatewayPath, method);
      return route.fulfill({ json: body === undefined ? {} : body });
    }
    return route.fulfill({ json: {} });
  });
}

/** Unlock the fixed-viewport shell so a fullPage shot captures ALL content
 *  (desktop uses an internal #main scroll region that fullPage would otherwise clip). */
async function unlockScroll(page: Page): Promise<void> {
  await page.evaluate(() => {
    const main = document.getElementById("main");
    document.documentElement.style.height = "auto";
    document.body.style.height = "auto";
    document.body.style.overflow = "visible";
    if (main) {
      let el: HTMLElement | null = main;
      while (el && el !== document.body) {
        el.style.height = "auto";
        el.style.maxHeight = "none";
        el.style.overflow = "visible";
        el = el.parentElement;
      }
    }
  });
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page
    .evaluate(async () => {
      const d = document as Document & { fonts?: { ready?: Promise<unknown> } };
      if (d.fonts?.ready) await d.fonts.ready;
    })
    .catch(() => {});
  await page.waitForTimeout(700);
}

async function shoot(page: Page, persona: string, route: string, tag: string): Promise<void> {
  const dir = path.join(SHOTS, persona);
  fs.mkdirSync(dir, { recursive: true });
  await unlockScroll(page);
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(dir, `${slug(route)}__${tag}.png`), fullPage: true }).catch((e) => {
    console.log(`screenshot failed ${persona} ${route} ${tag}: ${e}`);
  });
}

async function runAxe(page: Page, persona: string, route: string): Promise<void> {
  try {
    const results = await new AxeBuilder({ page }).analyze();
    const violations = results.violations.map((v) => ({
      id: v.id,
      impact: v.impact ?? "unknown",
      help: v.help,
      nodes: v.nodes.length,
      targets: v.nodes.slice(0, 4).map((n) => (Array.isArray(n.target) ? n.target.join(" ") : String(n.target))),
    }));
    axeRows.push({ persona, route, violations });
    fs.writeFileSync(path.join(AXE, `${persona}__${slug(route)}.json`), JSON.stringify(violations, null, 2));
  } catch (e) {
    console.log(`axe failed ${persona} ${route}: ${e}`);
    axeRows.push({ persona, route, violations: [] });
  }
}

test.describe.configure({ mode: "serial" });

for (const persona of PERSONAS) {
  test(`capture ${persona.key}`, async ({ page, context }) => {
    test.setTimeout(240_000);
    await seedIdentity(context, persona.identity);
    await stub(page, persona.identity);
    await page.emulateMedia({ reducedMotion: "reduce" });

    for (const route of persona.routes) {
      // ── desktop light: axe + screenshot ──
      await page.emulateMedia({ colorScheme: "light" });
      await page.setViewportSize(DESKTOP);
      await page.goto(route, { waitUntil: "domcontentloaded" }).catch(() => {});
      await settle(page);
      await runAxe(page, persona.key, route);
      await shoot(page, persona.key, route, "desktop");

      // ── mobile light: screenshot ──
      await page.setViewportSize(MOBILE);
      await page.waitForTimeout(300);
      await shoot(page, persona.key, route, "mobile");

      // ── dark hero: desktop dark screenshot ──
      if (DARK_HERO.has(route)) {
        await page.emulateMedia({ colorScheme: "dark" });
        await page.setViewportSize(DESKTOP);
        await page.goto(route, { waitUntil: "domcontentloaded" }).catch(() => {});
        await settle(page);
        await shoot(page, persona.key, route, "dark");
      }

      // ── superadmin: also capture the ⌘K command palette open ──
      if (persona.key === "6-superadmin" && route === "/app") {
        try {
          await page.emulateMedia({ colorScheme: "light" });
          await page.setViewportSize(DESKTOP);
          await page.goto(route, { waitUntil: "domcontentloaded" });
          await settle(page);
          await page.keyboard.press("Control+k");
          await page.getByRole("dialog").waitFor({ state: "visible", timeout: 5000 });
          await page.waitForTimeout(400);
          await page.screenshot({ path: path.join(SHOTS, persona.key, "app__palette.png") });
        } catch (e) {
          console.log(`palette capture skipped: ${e}`);
        }
      }
    }
  });
}

test.afterAll(async () => {
  const totalViolations = axeRows.reduce((n, r) => n + r.violations.length, 0);
  const summary = {
    generatedRoutes: axeRows.length,
    totalViolationTypes: totalViolations,
    byRoute: axeRows
      .map((r) => ({
        persona: r.persona,
        route: r.route,
        count: r.violations.length,
        seriousCritical: r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").length,
        ids: r.violations.map((v) => `${v.id}(${v.impact}×${v.nodes})`),
      }))
      .sort((a, b) => b.seriousCritical - a.seriousCritical || b.count - a.count),
  };
  fs.writeFileSync(path.join(OUT, "axe-summary.json"), JSON.stringify(summary, null, 2));
  // Also drop a flat screenshot index for the audit subagents.
  const index = fs
    .readdirSync(SHOTS)
    .flatMap((persona) =>
      fs.existsSync(path.join(SHOTS, persona))
        ? fs.readdirSync(path.join(SHOTS, persona)).map((f) => `${persona}/${f}`)
        : [],
    );
  fs.writeFileSync(path.join(OUT, "shots-index.json"), JSON.stringify(index, null, 2));
});
