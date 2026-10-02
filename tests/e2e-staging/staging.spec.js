/* STAGING ACCEPTANCE — real browser, real deployment.
 *
 * These run against a DEPLOYED host, not a dev server. They are deliberately
 * separate from tests/e2e/ so the CI gate never depends on a staging box.
 *
 * Every check here failed or passed on a real VPS; none is a local substitute.
 */
const { test, expect } = require("@playwright/test");

const LOGIN = "https://khachhang.viporder.com.vn";
const WAREHOUSE = "KY4001103376087-2-4-%7Cs";
const SEALING = "A1918106";

test.describe("staging: public pages", () => {
  test("homepage renders with a real title and one h1", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle(/VIPORDER/i);
    await expect(page.locator("h1")).toHaveCount(1);
  });

  test("robots, sitemap, CSS and JS are really served", async ({ page }) => {
    // Through page.goto, NOT page.request: the APIRequestContext is a separate
    // HTTP stack that ignores Chrome's --host-resolver-rules, so `viporder.com.vn`
    // resolved by real DNS and these checks hit a different server entirely.
    for (const p of ["/robots.txt", "/sitemap.xml", "/static/css/style.css", "/static/js/app.js"]) {
      const res = await page.goto(p, { waitUntil: "domcontentloaded" });
      expect(res.status(), p).toBe(200);
      expect((await res.body()).length, p).toBeGreaterThan(50);
    }
  });
});

test.describe("staging: source exposure", () => {
  test("no non-public path is served", async ({ page }) => {
    // Measured with curl too; this asserts it through the same path a browser
    // would take. Any 200 here is a release blocker.
    const leaked = [];
    for (const p of [
      "/backend/app/config.py", "/docs/SECURITY.md", "/.git/config", "/.env",
      "/docker-compose.yml", "/package.json", "/package-lock.json",
      "/playwright.config.js", "/tests/e2e/registration.spec.js", "/README.md",
    ]) {
      const res = await page.goto(p, { waitUntil: "domcontentloaded" });
      if (res.status() === 200) leaked.push(`${p} -> 200`);
    }
    expect(leaked, leaked.join("\n")).toEqual([]);
  });
});

test.describe("staging: tracking", () => {
  test("a valid warehouse code returns a customer-facing result", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.locator("#tracking").scrollIntoViewIfNeeded();
    await page.fill("#trackingKeyword", decodeURIComponent(WAREHOUSE));
    await page.click("#trackingSubmit");
    // The result region must carry real content, not an error.
    await expect(page.locator("#trackingResults")).toContainText(/A1918106|túi quà/i, {
      timeout: 30_000,
    });
  });

  test("a valid sealing code returns a customer-facing result", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.locator("#tracking").scrollIntoViewIfNeeded();
    // The form defaults to warehouse-import mode; a sealing code must be searched
    // in its own mode. Measured on the real form (`[data-mode]` buttons).
    await page.click('[data-mode="package_sealing"]');
    await page.fill("#trackingKeyword", SEALING);
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toContainText(/A1918106|Hạ hàng/i, {
      timeout: 30_000,
    });
  });

  test("a WRONG code shows the specific message, not a generic failure", async ({ page }) => {
    // This is the check that caught the API-404 defect: the app's JSON 404 was
    // being replaced by the site's HTML page, so the user lost the one message
    // that tells them to check the code.
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.locator("#tracking").scrollIntoViewIfNeeded();
    await page.fill("#trackingKeyword", "KHONG-CO-MA-NAY");
    await page.click("#trackingSubmit");
    // On a not-found the RESULT region stays hidden and the message goes to the
    // status line — measured on staging, not assumed. Asserting on the results
    // region here would fail against a working site.
    await expect(page.locator("#trackingStatus")).toContainText(/[Kk]hông tìm thấy/i, {
      timeout: 30_000,
    });
    await expect(page.locator("#trackingResults")).toBeHidden();
  });
});

test.describe("staging: login destination", () => {
  test("the login CTA points at the customer portal", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const links = await page.locator(`a[href^="${LOGIN}"]`).count();
    expect(links, "no link to the customer portal").toBeGreaterThan(0);
  });
});

test.describe("staging: registration form", () => {
  test("the form is present and refuses an empty submit", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.locator("#register").scrollIntoViewIfNeeded();
    await page.click('#registerForm button[type="submit"]');
    // Something must be reported; silence would mean the guard did not run.
    // The form reports through #formMessage and per-field #err-<name>.
    await expect(page.locator("#formMessage")).not.toBeEmpty({ timeout: 15_000 });
  });
});
