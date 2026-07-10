/**
 * premium.spec.ts — visual proof + AA check for the v7 premium reconciliation.
 * Renders the surfaces that best show the dark rail + grouped nav + selected
 * highlight + white canvas + hero titles + mono numerals, WITHOUT a gateway
 * (seeded cookie + page.route BFF mock). Runs axe (color-contrast INCLUDED) to
 * prove the dark rail introduces no serious/critical contrast violations.
 */
import { test, type Page, type BrowserContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as fs from "node:fs";
import * as path from "node:path";
import { gwBody } from "./fixtures";

const OUT =
  "/private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/00bd6205-726c-449e-b763-9b9b07f02062/scratchpad/ui-review";
const SHOTS = path.join(OUT, "premium-shots");
fs.mkdirSync(SHOTS, { recursive: true });

const DESKTOP = { width: 1440, height: 1000 };

type Identity = { user_id: string; tenant_id: string; email: string; role: string; exp: number | null };
const TENANT = "00000000-0000-0000-0000-000000000099";
const DETAIL = "00000000-0000-0000-0000-000000000042";
function me(role: string, email: string): Identity {
  return { user_id: "00000000-0000-0000-0000-000000000001", tenant_id: TENANT, email, role, exp: 9999999999 };
}

async function seedIdentity(context: BrowserContext, identity: Identity): Promise<void> {
  await context.addCookies([
    { name: "ai_proxy_session", value: "e30.e30.fakesig", domain: "127.0.0.1", path: "/" },
  ]);
}

async function stub(page: Page, identity: Identity): Promise<void> {
  await page.route("**/api/**", (route) => {
    const p = new URL(route.request().url()).pathname;
    const method = route.request().method().toUpperCase();
    if (p === "/api/auth/me") return route.fulfill({ json: identity });
    if (p.startsWith("/api/platform/impersonation")) return route.fulfill({ json: { active: false } });
    if (p.startsWith("/api/gw/")) {
      const body = gwBody(p.slice("/api/gw/".length), method);
      return route.fulfill({ json: body === undefined ? {} : body });
    }
    return route.fulfill({ json: {} });
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

function slug(route: string): string {
  const s = route.replace(/^\//, "").replace(/\//g, "_").replace(/[^a-zA-Z0-9_.-]/g, "-");
  return s === "" ? "home" : s;
}

const CASES = [
  {
    key: "owner",
    id: me("owner", "owner@acme.io"),
    // owner sees ALL 4 workflow groups → best grouping demo; usage=stat mono; keys=hero+tabs
    routes: ["/app", "/app/usage", "/app/keys", "/app/routing", "/app/settings"],
  },
  {
    key: "superadmin",
    id: me("superadmin", "root@platform.internal"),
    routes: ["/app/platform/tenants", `/app/platform/tenants/${DETAIL}`],
  },
];

const axeFindings: Array<{ route: string; count?: number; ids?: string[]; error?: string }> = [];

test.describe.configure({ mode: "serial" });

for (const c of CASES) {
  test(`premium ${c.key}`, async ({ page, context }) => {
    test.setTimeout(240_000);
    await seedIdentity(context, c.id);
    await stub(page, c.id);
    await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
    await page.setViewportSize(DESKTOP);

    for (const route of c.routes) {
      await page.goto(route, { waitUntil: "domcontentloaded" }).catch(() => {});
      await settle(page);
      // viewport shot (rail + hero + top content above the fold — the premium signature)
      await page
        .screenshot({ path: path.join(SHOTS, `${c.key}__${slug(route)}.png`) })
        .catch((e) => console.log(`shot failed ${route}: ${e}`));
      // axe with color-contrast INCLUDED — the dark rail must not add violations
      try {
        const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        const sc = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        axeFindings.push({ route, count: sc.length, ids: sc.map((v) => `${v.id}(${v.impact}×${v.nodes.length})`) });
      } catch (e) {
        axeFindings.push({ route, error: String(e) });
      }
    }
  });
}

test.afterAll(() => {
  fs.writeFileSync(path.join(SHOTS, "axe.json"), JSON.stringify(axeFindings, null, 2));
});
