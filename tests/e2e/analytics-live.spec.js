/* GA4 + Meta Pixel are ENABLED (2026-10-10). The third-party scripts are
 * stubbed here, so this proves what the PAGE asks for and what it queues, not
 * what Google or Meta receive.
 *
 * What matters, and is easy to get wrong with gtag.js loaded without GTM:
 *   - gtag only reads `arguments`-shaped dataLayer entries, so `js`/`config`/
 *     `event` must be pushed as Arguments objects, not arrays or plain objects;
 *   - `config` must come before any `event`, or the event has no destination;
 *   - page_view comes from config (send_page_view: true), not a second event.
 */

"use strict";

const { test, expect } = require("@playwright/test");

const GA4_ID = "G-581GP4TP51";

async function stubThirdParties(page) {
  const asked = [];
  await page.route(/googletagmanager\.com|connect\.facebook\.net|google-analytics\.com|facebook\.com\/tr/, (route) => {
    asked.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
  });
  return asked;
}

function gtagEntries(page) {
  return page.evaluate(() =>
    (window.dataLayer || [])
      .filter((e) => Object.prototype.toString.call(e) === "[object Arguments]")
      .map((e) => Array.prototype.slice.call(e).map((v) => (v instanceof Date ? "<date>" : v))),
  );
}

test.describe("analytics, enabled", () => {
  test("loads GA4 (direct) and Meta Pixel, not GTM", async ({ page }) => {
    const asked = await stubThirdParties(page);
    await page.goto("/");
    await expect.poll(() => asked.length).toBeGreaterThanOrEqual(2);

    expect(asked.some((u) => u.includes(`gtag/js?id=${GA4_ID}`))).toBe(true);
    expect(asked.some((u) => u.includes("connect.facebook.net/en_US/fbevents.js"))).toBe(true);
    expect(asked.some((u) => u.includes("gtm.js"))).toBe(false);
  });

  test("config is queued in gtag shape, with GA4 sending the page_view", async ({ page }) => {
    await stubThirdParties(page);
    await page.goto("/");
    await expect.poll(async () => (await gtagEntries(page)).length).toBeGreaterThanOrEqual(2);

    const entries = await gtagEntries(page);
    expect(entries[0]).toEqual(["js", "<date>"]);
    expect(entries[1][0]).toBe("config");
    expect(entries[1][1]).toBe(GA4_ID);
    expect(entries[1][2].send_page_view).toBe(true);
    expect(entries.filter((e) => e[0] === "event" && e[1] === "page_view")).toHaveLength(0);
  });

  test("a click becomes a gtag event, after config", async ({ page }) => {
    await stubThirdParties(page);
    await page.goto("/");
    await expect.poll(async () => (await gtagEntries(page)).length).toBeGreaterThanOrEqual(2);

    await page.evaluate(() => window.VIPOrderAnalytics.phoneClick({ placement: "test" }));

    const entries = await gtagEntries(page);
    const configAt = entries.findIndex((e) => e[0] === "config");
    const eventAt = entries.findIndex((e) => e[0] === "event" && e[1] === "phone_click");
    expect(eventAt).toBeGreaterThan(configAt);
    const params = entries[eventAt][2];
    expect(params.placement).toBe("test");
    expect(typeof params.viporder_event_id).toBe("string");
    for (const value of Object.values(params)) {
      expect(value === null || typeof value !== "object").toBe(true);
    }
  });
});
