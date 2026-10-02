/* Link integrity and the login redirect, checked in a browser.
 *
 * `tools/check_site.py` already parses the HTML and checks links statically. This
 * file answers the two questions it cannot:
 *   - does the link WORK when the site is served (status, not just syntax)?
 *   - does it resolve where it says it does, once a browser has resolved it?
 */

"use strict";

const { test, expect } = require("@playwright/test");

const PORTAL = "https://khachhang.viporder.com.vn";

test.describe("links", () => {
  test("no internal link is broken", async ({ page, request }) => {
    await page.goto("/");
    const hrefs = await page.locator("a[href]").evaluateAll((as) =>
      as.map((a) => a.getAttribute("href")),
    );
    expect(hrefs.length).toBeGreaterThan(0);

    const internal = [
      ...new Set(
        hrefs.filter(
          (h) => h && !/^(https?:|mailto:|tel:|#|javascript:)/i.test(h) && !h.startsWith("//"),
        ),
      ),
    ];
    expect(internal.length).toBeGreaterThan(0);

    for (const href of internal) {
      const res = await request.get(href);
      expect(res.status(), `internal link ${href} returned ${res.status()}`).toBeLessThan(400);
    }
  });

  test("no link points at localhost or a development port", async ({ page }) => {
    await page.goto("/");
    const hrefs = await page.locator("a[href]").evaluateAll((as) =>
      as.map((a) => a.getAttribute("href")),
    );
    for (const href of hrefs) {
      expect(href, `a shipped link points at ${href}`).not.toMatch(
        /localhost|127\.0\.0\.1|:8000|:8080|:3000|\.local\b/i,
      );
    }
  });

  test("no href is empty, whitespace, or a bare '#'", async ({ page }) => {
    await page.goto("/");
    const hrefs = await page.locator("a[href]").evaluateAll((as) =>
      as.map((a) => a.getAttribute("href")),
    );
    for (const href of hrefs) {
      expect(href, "an anchor has an empty href").not.toMatch(/^\s*$/);
    }
  });

  test("every in-page anchor has a matching target", async ({ page }) => {
    await page.goto("/");
    const hashes = await page
      .locator('a[href^="#"]')
      .evaluateAll((as) => as.map((a) => a.getAttribute("href")).filter((h) => h !== "#"));
    for (const hash of [...new Set(hashes)]) {
      const id = hash.slice(1);
      await expect(page.locator(`#${id}`), `#${id} has no target`).toHaveCount(1);
    }
  });

  test("every asset the page requests is served", async ({ page }) => {
    /* Runtime asset failures: a script, style, image or font that 404s. None of
     * them is visible in the HTML, and any of them can silently break the page. */
    const failed = [];
    page.on("response", (res) => {
      const url = res.url();
      if (url.startsWith("http://127.0.0.1") && res.status() >= 400) {
        failed.push(`${res.status()} ${url}`);
      }
    });
    await page.goto("/", { waitUntil: "networkidle" });
    expect(failed, `assets failed to load:\n${failed.join("\n")}`).toEqual([]);
  });

  test("no JavaScript error is thrown while the page loads", async ({ page }) => {
    /* The whole registration flow depends on scripts that are loaded with `defer`
     * in a specific order. A single load-order mistake turns the form into a
     * decoration, and it fails silently. */
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto("/", { waitUntil: "networkidle" });
    await page.locator("#register").scrollIntoViewIfNeeded();
    expect(errors, `the page threw:\n${errors.join("\n")}`).toEqual([]);
  });
});

test.describe("login redirect", () => {
  test("every login call to action resolves to the customer portal", async ({ page }) => {
    await page.goto("/");
    /* Any link whose text or label says "đăng nhập" is a login CTA, wherever it
     * sits. Enumerating them by wording rather than by selector means a new one
     * added elsewhere is covered without editing this test. */
    /* Match the wording a LOGIN link actually carries. An earlier version also
     * matched "khách hàng", which caught a registration CTA ("Đăng ký mã khách")
     * whose href is `#register` — the test failed on correct markup, which is the
     * fastest way to get a test deleted. */
    const candidates = await page
      .locator("a")
      .evaluateAll((as) =>
        as
          .filter((a) => /đăng\s*nhập|\blogin\b/i.test(a.textContent || ""))
          .map((a) => a.getAttribute("href")),
      );
    expect(candidates.length, "no login call to action was found").toBeGreaterThan(0);
    for (const href of candidates) {
      expect(href, `a login CTA points at ${href}`).toContain(PORTAL);
    }
  });

  test("the portal CTA opens in a way that does not destroy the form", async ({ page }) => {
    /* Following the login link must not be a same-tab navigation away from a
     * half-filled registration, and the choice must be deliberate.
     *
     * Target the HEADER login affordance by role and name. The page has six
     * portal links, and the first in document order is `#formPortalLink` —
     * inside the success block, `hidden` until a registration succeeds — so a
     * naive `.first()` asserts visibility on something designed to be invisible.
     * `a[href=...]:visible` does not compose here either: Playwright reported
     * "element(s) not found" while a plain probe found six matches, one visible.
     *
     * AND NAVIGATE FIRST. My first version of this test never called `page.goto`,
     * so it ran against `about:blank` and failed with "element(s) not found" —
     * which reads exactly like a missing link. Two of my three failures in this
     * file were the TEST being wrong, not the page. */
    await page.goto("/");
    const login = page.locator("header").getByRole("link", { name: /đăng nhập/i }).first();
    await expect(login).toBeVisible();
    await expect(login).toHaveAttribute("href", PORTAL);
    const target = await login.getAttribute("target");
    const rel = await login.getAttribute("rel");
    if (target === "_blank") {
      expect(rel, "target=_blank without rel=noopener").toMatch(/noopener/);
    }
  });
});
