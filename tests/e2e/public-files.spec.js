/* Public files and crawl surface, fetched over HTTP by the server that serves the
 * site — not read off disk.
 *
 * Reading `robots.txt` from the repository proved that the file is correct. It did
 * not prove the file is SERVED, and the nginx configuration is where serving
 * actually gets decided.
 */

"use strict";

const { test, expect } = require("@playwright/test");

test.describe("public files", () => {
  test("robots.txt is served and allows crawling", async ({ request }) => {
    const res = await request.get("/robots.txt");
    expect(res.status()).toBe(200);
    const body = await res.text();
    expect(body).toMatch(/User-agent:\s*\*/i);
    expect(body).toMatch(/Allow:\s*\//i);
  });

  test("robots.txt advertises the sitemap with an absolute URL", async ({ request }) => {
    const body = await (await request.get("/robots.txt")).text();
    expect(body).toMatch(/Sitemap:\s*https:\/\/viporder\.com\.vn\/sitemap\.xml/i);
  });

  test("robots.txt does NOT disallow the 404 page", async ({ request }) => {
    /* The 404 page carries `noindex`, which is the correct control. `Disallow`
     * would stop a crawler reading the noindex — the opposite of the intent. */
    const body = await (await request.get("/robots.txt")).text();
    expect(body).not.toMatch(/^Disallow:\s*\/404/m);
  });

  test("sitemap.xml is served, is well formed, and every entry is absolute", async ({ request }) => {
    const res = await request.get("/sitemap.xml");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toMatch(/xml/i);
    const body = await res.text();
    expect(body).toMatch(/<urlset/);
    const locs = [...body.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs.length, "the sitemap lists no URLs").toBeGreaterThan(0);
    for (const loc of locs) {
      expect(loc, `sitemap entry is not absolute: ${loc}`).toMatch(/^https:\/\/viporder\.com\.vn\//);
    }
  });

  test("the sitemap does not list the error page", async ({ request }) => {
    /* An error page in a sitemap asks a crawler to index a URL that must not be
     * indexed. tools/check_site.py enforces this statically; this proves the
     * SERVED file agrees. */
    const body = await (await request.get("/sitemap.xml")).text();
    expect(body).not.toMatch(/<loc>[^<]*404[^<]*<\/loc>/);
  });

  test("every URL in the sitemap actually resolves", async ({ request }) => {
    const body = await (await request.get("/sitemap.xml")).text();
    const locs = [...body.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) =>
      new URL(m[1]).pathname,
    );
    for (const path of locs) {
      const res = await request.get(path);
      expect(res.status(), `${path} is in the sitemap but does not resolve`).toBeLessThan(400);
    }
  });

  test("the favicon and the Open Graph image are really served", async ({ request }) => {
    /* A 200 with an empty body, or an HTML error page, is a broken asset that
     * every static check would pass. */
    for (const [path, type] of [
      ["/static/img/favicon.svg", /svg/],
      ["/static/img/favicon-32.png", /png/],
      ["/static/img/og-viporder.png", /png/],
      ["/static/img/apple-touch-icon.png", /png/],
    ]) {
      const res = await request.get(path);
      expect(res.status(), `${path} is not served`).toBe(200);
      expect(res.headers()["content-type"], `${path} has the wrong content type`).toMatch(type);
      expect((await res.body()).length, `${path} is an empty file`).toBeGreaterThan(200);
    }
  });

  test("the canonical points at production over https", async ({ page }) => {
    await page.goto("/");
    const canonical = await page.locator('link[rel="canonical"]').getAttribute("href");
    expect(canonical).toBe("https://viporder.com.vn/");
  });

  test("Open Graph tags are present and absolute", async ({ page }) => {
    await page.goto("/");
    for (const prop of ["og:title", "og:description", "og:image", "og:url", "og:type"]) {
      const content = await page.locator(`meta[property="${prop}"]`).getAttribute("content");
      expect(content, `${prop} is missing`).toBeTruthy();
      if (prop === "og:image" || prop === "og:url") {
        expect(content, `${prop} must be absolute`).toMatch(/^https:\/\//);
      }
    }
  });

  test("the JSON-LD block is valid JSON", async ({ page }) => {
    await page.goto("/");
    const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
    expect(blocks.length).toBeGreaterThan(0);
    for (const raw of blocks) {
      expect(() => JSON.parse(raw), "a JSON-LD block is not valid JSON").not.toThrow();
    }
  });

  test("a missing page returns a real 404 with a noindex error page", async ({ page }) => {
    const res = await page.goto("/this-path-does-not-exist-9f3a");
    expect(res.status(), "a missing path did not return 404").toBe(404);
    /* `noindex` on the error page — and NOT a canonical, which would map every
     * mistyped URL onto a real page. */
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
    expect(await page.locator('link[rel="canonical"]').count()).toBe(0);
    /* And the page must be usable, not a bare server error. */
    await expect(page.locator("h1")).toHaveCount(1);
  });

  test("the 404 page links back to the site", async ({ page }) => {
    await page.goto("/this-path-does-not-exist-9f3a");
    const back = page.locator('a[href="/"], a[href="https://viporder.com.vn/"]').first();
    await expect(back).toBeVisible();
  });
});
