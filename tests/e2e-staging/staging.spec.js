/* STAGING ACCEPTANCE — real browser, real deployment.
 *
 * These run against a DEPLOYED host, not a dev server. They are deliberately
 * separate from tests/e2e/ so the CI gate never depends on a staging box.
 *
 * Every check here failed or passed on a real VPS; none is a local substitute.
 */
const { test, expect } = require("@playwright/test");

const LOGIN = "https://khachhang.viporder.com.vn/login";
const WAREHOUSE = "KY4001103376087-2-4-%7Cs";
const SEALING = "A1918106";

/* What "really served" has to mean, per asset.
 *
 * WHY THIS TABLE EXISTS. The check here used to be `status === 200` and
 * `body.length > 50`, and that is satisfied by a host that answers **200 +
 * index.html for every path**. Negative control: a local server returning
 * index.html for everything made it PASS while robots, sitemap, CSS and JS were
 * plainly not served — the four assets it names, "really served", all being the
 * same HTML page. A green check that a broken server also passes is not
 * evidence, so each asset now names the CONTENT-TYPE it must come back with and
 * a MARKER that only that file contains.
 *
 * `text/html` is refused explicitly for all four: that single assertion is what
 * makes the everything-is-HTML server fail.
 */
const ASSETS = [
  {
    path: "/robots.txt",
    type: /^text\/plain\b/,
    marker: "User-agent:",
    what: "robots.txt",
  },
  {
    path: "/sitemap.xml",
    type: /\bxml\b/,
    marker: "<urlset",
    what: "sitemap.xml",
  },
  {
    path: "/static/css/style.css",
    type: /^text\/css\b/,
    marker: "--surface-2:",
    what: "the stylesheet",
  },
  {
    path: "/static/js/app.js",
    type: /javascript\b/,
    marker: "VIPORDER",
    what: "the page script",
  },
];

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
    for (const asset of ASSETS) {
      const res = await page.goto(asset.path, { waitUntil: "domcontentloaded" });
      expect(res.status(), asset.path).toBe(200);

      const contentType = (res.headers()["content-type"] || "").toLowerCase();
      expect(contentType, `${asset.path}: content-type is not ${asset.what}`).toMatch(
        asset.type,
      );
      expect(contentType, `${asset.path}: served the HTML page instead`).not.toContain(
        "text/html",
      );

      const body = await res.text();
      expect(body.length, `${asset.path}: body too short to be ${asset.what}`).toBeGreaterThan(
        50,
      );
      expect(body, `${asset.path}: body is not ${asset.what}`).toContain(asset.marker);
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
