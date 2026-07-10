import { test, type Page, type BrowserContext } from "@playwright/test";
import { gwBody } from "./fixtures";

const TENANT = "00000000-0000-0000-0000-000000000099";
const OWNER = { user_id: "00000000-0000-0000-0000-000000000001", tenant_id: TENANT, email: "ada@acme.io", role: "owner", exp: 9999999999 };

test("debug members network", async ({ page, context }: { page: Page; context: BrowserContext }) => {
  test.setTimeout(60_000);
  await context.addCookies([{ name: "ai_proxy_session", value: "e30.e30.fakesig", domain: "127.0.0.1", path: "/" }]);

  page.on("console", (m) => console.log(`[console.${m.type()}] ${m.text()}`));
  page.on("requestfailed", (r) => console.log(`[reqfailed] ${r.method()} ${new URL(r.url()).pathname} — ${r.failure()?.errorText}`));
  page.on("response", (r) => {
    const u = new URL(r.url());
    if (u.pathname.includes("/api/")) console.log(`[response] ${r.status()} ${u.pathname}${u.search}`);
  });

  await page.route("**/api/**", (route) => {
    const u = new URL(route.request().url());
    const p = u.pathname;
    const method = route.request().method().toUpperCase();
    if (p === "/api/auth/me") { console.log(`[mock] auth/me -> owner`); return route.fulfill({ json: OWNER }); }
    if (p.startsWith("/api/platform/impersonation")) return route.fulfill({ json: { active: false } });
    if (p.startsWith("/api/gw/")) {
      const body = gwBody(p.slice("/api/gw/".length), method);
      console.log(`[mock] gw ${method} ${p.slice("/api/gw/".length)} -> ${body === undefined ? "UNDEFINED(->{})" : "data"}`);
      return route.fulfill({ json: body === undefined ? {} : body });
    }
    console.log(`[mock] passthrough-empty ${p}`);
    return route.fulfill({ json: {} });
  });

  await page.goto("/app/members", { waitUntil: "domcontentloaded" }).catch((e) => console.log(`goto err ${e}`));
  await page.waitForTimeout(5000);
  const bodyText = await page.evaluate(() => document.querySelector("main")?.textContent?.slice(0, 200) ?? "NO MAIN");
  console.log(`[main text] ${bodyText}`);
  const url = page.url();
  console.log(`[final url] ${url}`);
});
