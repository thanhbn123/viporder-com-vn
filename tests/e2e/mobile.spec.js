/* Mobile rendering at a real device profile (Pixel 7, from playwright devices).
 *
 * The project has said since G07 that mobile was "not visually verified". This
 * file is what closes that: an actual browser at an actual viewport size.
 */

"use strict";

const { test, expect } = require("@playwright/test");
const { hasHorizontalOverflow } = require("./helpers");

test.describe("homepage, mobile", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("is a phone-sized viewport", async ({ page }) => {
    const width = page.viewportSize().width;
    expect(width).toBeLessThan(500);
  });

  test("does not scroll sideways", async ({ page }) => {
    /* The most common real mobile defect, and the one a static review cannot see. */
    expect(await hasHorizontalOverflow(page), "the page overflows horizontally").toBe(false);
  });

  test("does not scroll sideways with the registration form in view either", async ({ page }) => {
    await page.locator("#register").scrollIntoViewIfNeeded();
    expect(await hasHorizontalOverflow(page)).toBe(false);
  });

  test("the header is usable — the navigation control is reachable", async ({ page }) => {
    const header = page.locator("header").first();
    await expect(header).toBeVisible();
    /* Either a visible nav, or a real toggle button. Asserting "one of the two"
     * on purpose: the accessible pattern is the toggle, but a layout that shows
     * the links outright is also usable. What must NOT happen is neither. */
    const toggle = page.locator('header button, [aria-controls], .nav-toggle').first();
    const navLink = page.locator("header nav a, header a").first();
    const toggleVisible = (await toggle.count()) > 0 && (await toggle.isVisible());
    const linkVisible = (await navLink.count()) > 0 && (await navLink.isVisible());
    expect(toggleVisible || linkVisible, "the header offers no way to navigate").toBe(true);
  });

  test("the registration call to action is visible without zooming", async ({ page }) => {
    const cta = page.locator('a[href="#register"]').first();
    await expect(cta).toBeVisible();
    const box = await cta.boundingBox();
    expect(box.width, "the CTA is too small to tap").toBeGreaterThan(80);
    expect(box.height, "the CTA is too short to tap").toBeGreaterThan(24);
  });

  test("the registration form is usable — every control is on screen", async ({ page }) => {
    await page.locator("#register").scrollIntoViewIfNeeded();
    for (const name of ["full_name", "phone", "password"]) {
      const el = page.locator(`[name="${name}"]`);
      await expect(el).toBeVisible();
      const box = await el.boundingBox();
      expect(box.width, `${name} is too narrow to use`).toBeGreaterThan(100);
    }

    /* The consent checkbox is a bare 13x13px input — but its LABEL wraps it
     * (index.html:542-543), so the label is the real tap target and my first
     * version measured the wrong element. What matters is that the target a thumb
     * actually hits is big enough, and that tapping the words toggles the box. */
    const consentLabel = page.locator('label:has([name="consent"])');
    await expect(consentLabel).toBeVisible();
    const labelBox = await consentLabel.boundingBox();
    expect(labelBox.height, "the consent tap target is shorter than a thumb").toBeGreaterThanOrEqual(24);
    expect(labelBox.width, "the consent tap target is too narrow").toBeGreaterThan(100);
    /* Prove the label toggles the box: a bare input inside a label that does NOT
     * wrap its text looks identical in a screenshot and is unusable on a phone. */
    await page.locator('label:has([name="consent"]) span').first().click();
    await expect(page.locator('[name="consent"]')).toBeChecked();
    await page.locator('label:has([name="consent"]) span').first().click();
    await expect(page.locator('[name="consent"]')).not.toBeChecked();
    await expect(page.locator('#registerForm button[type="submit"]')).toBeVisible();
  });

  test("the submit button is not clipped off the screen", async ({ page }) => {
    await page.locator("#register").scrollIntoViewIfNeeded();
    const btn = page.locator('#registerForm button[type="submit"]');
    const box = await btn.boundingBox();
    const width = page.viewportSize().width;
    expect(box.x, "the submit button starts off-screen").toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width, "the submit button runs off the right edge").toBeLessThanOrEqual(width + 1);
  });

  test("the form can actually be completed at this size", async ({ page }) => {
    /* Filling it is the only proof that it is usable: an overlay, a clipped field
     * or a `readonly` mistake all look fine to `toBeVisible` but block input. */
    await page.locator("#register").scrollIntoViewIfNeeded();
    await page.fill('[name="full_name"]', "Khách Điện Thoại");
    await page.fill('[name="phone"]', "+84901234567");
    await page.fill('[name="password"]', "matkhau123");
    await page.check('[name="consent"]');
    await expect(page.locator('[name="full_name"]')).toHaveValue("Khách Điện Thoại");
    await expect(page.locator('[name="consent"]')).toBeChecked();
  });
});
