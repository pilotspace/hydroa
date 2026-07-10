/**
 * polish.spec.ts — THROWAWAY visual review of the surfaces this branch changed:
 *   overview (W3 chart + formatters), usage (mono numerals), members (W5 undo
 *   banner — captured AFTER a real role-change interaction), health + alerts
 *   (W4 semantic badges), all with the light-Aurora grouped-nav rail. Full-page
 *   shots at desktop; seeded cookie + page.route BFF mock (no gateway needed).
 */
import { test, type Page, type BrowserContext } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { gwBody } from "./fixtures";

const OUT =
  "/private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/00bd6205-726c-449e-b763-9b9b07f02062/scratchpad/ui-review";
const SHOTS = path.join(OUT, "polish-shots");
fs.mkdirSync(SHOTS, { recursive: true });

const DESKTOP = { width: 1440, height: 1000 };
const TENANT = "00000000-0000-0000-0000-000000000099";
const OWNER = {
  user_id: "00000000-0000-0000-0000-000000000001",
  tenant_id: TENANT,
  email: "ada@acme.io",
  role: "owner",
  exp: 9999999999,
};

async function seed(context: BrowserContext): Promise<void> {
  await context.addCookies([
    { name: "ai_proxy_session", value: "e30.e30.fakesig", domain: "127.0.0.1", path: "/" },
  ]);
}

async function stub(page: Page): Promise<void> {
  await page.route("**/api/**", (route) => {
    const p = new URL(route.request().url()).pathname;
    const method = route.request().method().toUpperCase();
    if (p === "/api/auth/me") return route.fulfill({ json: OWNER });
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

function shot(page: Page, name: string) {
  return page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }).catch((e) => {
    console.log(`shot failed ${name}: ${e}`);
  });
}

test.describe.configure({ mode: "serial" });

test("polish captures", async ({ page, context }) => {
  test.setTimeout(240_000);
  await seed(context);
  await stub(page);
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  await page.setViewportSize(DESKTOP);

  const routes: Array<[string, string]> = [
    ["/app", "overview"],
    ["/app/usage", "usage"],
    ["/app/members", "members"],
    ["/app/health", "health"],
    ["/app/alerts", "alerts"],
  ];

  for (const [route, name] of routes) {
    await page.goto(route, { waitUntil: "domcontentloaded" }).catch(() => {});
    await settle(page);
    await shot(page, name);
  }

  // W5 — drive a real role change on a non-self row, then capture the undo banner.
  await page.goto("/app/members", { waitUntil: "domcontentloaded" }).catch(() => {});
  await settle(page);
  try {
    const select = page.getByLabel("Assign role to grace@acme.io");
    await select.selectOption("operator", { timeout: 10_000 });
    await page.waitForTimeout(800);
    await shot(page, "members-undo-banner");
    console.log("undo banner captured");
  } catch (e) {
    console.log(`members interaction failed: ${e}`);
  }
});
