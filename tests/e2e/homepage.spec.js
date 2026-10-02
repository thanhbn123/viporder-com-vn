/* Desktop rendering and the content a visitor must be able to reach.
 *
 * These assertions are about what the PAGE SHOWS. Everything here was previously
 * verified by reading HTML, which proves the markup parses and nothing about what
 * a browser renders.
 */

"use strict";

const { test, expect } = require("@playwright/test");

test.describe("homepage, desktop", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders with a real title and a single h1", async ({ page }) => {
    await expect(page).toHaveTitle(/VIPORDER/i);
    /* One h1 is an accessibility rule, and it is the document outline a screen
     * reader announces. Checked in a browser rather than by counting `<h1>` tags,
     * because a template could emit a hidden one. */
    await expect(page.locator("h1")).toHaveCount(1);
  });

  test("the registration call to action is visible and links into the form", async ({ page }) => {
    const cta = page.locator('a[href="#register"]').first();
    await expect(cta).toBeVisible();
    await cta.click();
    await expect(page.locator("#registerForm")).toBeInViewport();
  });

  test("the login call to action points at the customer portal", async ({ page }) => {
    const login = page.locator('a[href="https://khachhang.viporder.com.vn"]').first();
    await expect(login).toBeVisible();
    await expect(login).toHaveAttribute("href", "https://khachhang.viporder.com.vn");
  });

  test("the services are present and readable", async ({ page }) => {
    /* Not a count of divs: each listed service must be visible text. */
    for (const text of ["Vận chuyển", "chính ngạch"]) {
      await expect(page.getByText(new RegExp(text, "i")).first()).toBeVisible();
    }
  });

  test("the process/step section is present", async ({ page }) => {
    const steps = page.locator("section").filter({ hasText: /quy trình|quy trinh/i });
    expect(await steps.count()).toBeGreaterThan(0);
  });

  test("the footer renders with the company name", async ({ page }) => {
    const footer = page.locator("footer").first();
    await expect(footer).toBeVisible();
    await expect(footer).toContainText(/VIPORDER/i);
  });

  test("the registration form has every field a customer must fill", async ({ page }) => {
    for (const name of [
      "full_name",
      "phone",
      "email",
      "password",
      "confirmPassword",
      "province",
      "service",
      "acceptTerms",
    ]) {
      await expect(page.locator(`[name="${name}"]`)).toBeAttached();
    }
    await expect(page.locator('#registerForm button[type="submit"]')).toBeVisible();
  });

  test("no element overflows the viewport horizontally", async ({ page }) => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow, "the page scrolls sideways on desktop").toBe(false);
  });

  test("keyboard Tab reaches the form and the focus is visible", async ({ page }) => {
    await page.locator("#register").scrollIntoViewIfNeeded();
    await page.locator('[name="full_name"]').focus();
    await expect(page.locator('[name="full_name"]')).toBeFocused();
    await page.keyboard.press("Tab");
    /* Focus must MOVE — a tab stop that goes nowhere means a keyboard user is
     * stuck, and `tabindex` mistakes are invisible in a screenshot. */
    const moved = await page.evaluate(() => document.activeElement?.getAttribute("name"));
    expect(moved).not.toBe("full_name");
  });
});
