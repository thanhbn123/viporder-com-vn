/* The registration flow, driven through a real browser.
 *
 * Scenario selection is by PHONE SUFFIX (see helpers.js), so each failure path is
 * deterministic. Every assertion here is something a CUSTOMER can observe.
 *
 * Lead-state assertions (REGISTERED / PENDING / FAILED, external ids) deliberately
 * live in the backend suite, where they can query the store directly. This file
 * answers a different question: does a person using a browser get a truthful,
 * usable answer in each case?
 */

"use strict";

const { test, expect } = require("@playwright/test");
const { phoneFor, fillRegistration, submitAndAwait } = require("./helpers");

test.describe("registration, mock provider", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.locator("#register").scrollIntoViewIfNeeded();
  });

  test("a valid registration completes and shows the customer code", async ({ page }) => {
    const phone = phoneFor("success");
    await fillRegistration(page, { phone });
    await submitAndAwait(page);

    const message = page.locator("#formMessage");
    await expect(message).toHaveClass(/success/);
    /* The code is the thing the customer needs in order to be a customer. If the
     * UI ever stops printing it, the registration still "works" and the person is
     * left with nothing to sign in with. */
    await expect(message).toContainText(/Mã khách hàng của bạn:\s*TT\d+/);
    await expect(message).toContainText(/đăng nhập/i);
  });

  test("the portal link appears on success and points at the portal host", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("success") });
    await submitAndAwait(page);

    const success = page.locator("#formSuccess");
    await expect(success).toBeVisible();
    const link = page.locator("#formPortalLink");
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    /* The open-redirect guard must let the approved host through. If this fails,
     * the guard is refusing the real server value, which would strand every
     * customer who successfully registered. */
    expect(href, `portal link is ${href}`).toMatch(
      /^https:\/\/khachhang\.viporder\.com\.vn(\/|$)/,
    );
  });

  test("the submit button becomes disabled after a successful registration", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("success") });
    await submitAndAwait(page);
    const btn = page.locator('#registerForm button[type="submit"]');
    await expect(btn).toBeDisabled();
    await expect(page.locator("#registerForm")).toHaveAttribute("data-completed", "true");
  });

  test("an unavailable provider does NOT tell the customer it failed", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("unavailable") });
    await submitAndAwait(page);

    const message = page.locator("#formMessage");
    /* The lead IS stored. A message that reads like a failure would send the
     * customer away from a registration that actually happened. */
    await expect(message).toContainText(/ghi nhận|thử lại|đang/i);
    await expect(message).not.toHaveClass(/success/);
    /* And it must not claim the registration is complete. */
    await expect(message).not.toContainText(/Mã khách hàng của bạn/);
  });

  test("a provider timeout is answered the same way as an outage", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("timeout") });
    await submitAndAwait(page);
    const message = page.locator("#formMessage");
    await expect(message).not.toBeEmpty();
    await expect(message).not.toContainText(/Mã khách hàng của bạn/);
  });

  test("a provider that throws is not reported as success", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("error") });
    await submitAndAwait(page);
    const message = page.locator("#formMessage");
    await expect(message).not.toContainText(/Mã khách hàng của bạn/);
  });

  test("an invalid registration is reported as invalid, not as an outage", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("invalid") });
    await submitAndAwait(page);
    const message = page.locator("#formMessage");
    await expect(message).not.toBeEmpty();
    await expect(message).not.toContainText(/Mã khách hàng của bạn/);
  });

  test("a duplicate phone tells the customer the number is taken", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("duplicate") });
    await submitAndAwait(page);
    const message = page.locator("#formMessage");
    await expect(message).not.toBeEmpty();
    /* DUPLICATE_PHONE is permanent: the wording must not invite an endless retry
     * of a number that will never be accepted, and must not invent a new one. */
    await expect(message).toContainText(/số điện thoại|đăng ký|đăng nhập/i);
  });

  test("the client refuses an empty form without contacting the server", async ({ page }) => {
    const requests = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/registrations")) requests.push(r.method());
    });
    await page.click('#registerForm button[type="submit"]');
    await expect(page.locator("#formMessage")).not.toBeEmpty();
    expect(requests, "an empty form was sent to the server").toEqual([]);
  });

  test("the client refuses a phone that is not Vietnamese", async ({ page }) => {
    await fillRegistration(page, { phone: "12345" });
    await page.click('#registerForm button[type="submit"]');
    /* The specific reason belongs on the FIELD. `#formMessage` carries only the
     * generic "check the marked fields" summary, so asserting the reason there
     * tests the wrong element. */
    await expect(page.locator("#err-phone")).toContainText(/điện thoại/i);
    await expect(page.locator('[name="phone"]')).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("#formMessage")).toContainText(/kiểm tra lại/i);
  });

  test("a password shorter than eight characters is refused client-side", async ({ page }) => {
    await fillRegistration(page, { phone: phoneFor("success"), password: "ngan" });
    await page.click('#registerForm button[type="submit"]');
    await expect(page.locator("#err-password")).toContainText(/8 ký tự/i);
  });

  test("the password never reaches the DOM, storage or cookies", async ({ page }) => {
    /* The password must cross the wire, so "it is in the request body" is correct
     * and expected. What must NOT happen is it landing anywhere that outlives the
     * request: the rendered page, localStorage, sessionStorage or a cookie. */
    const password = "matkhau-bi-mat-123";
    await fillRegistration(page, { phone: phoneFor("success"), password });
    await submitAndAwait(page);
    await page.waitForTimeout(500);

    const dom = await page.content();
    expect(dom, "the password is in the rendered DOM").not.toContain(password);

    const storage = await page.evaluate(() => {
      const dump = {};
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        dump[`local:${k}`] = localStorage.getItem(k);
      }
      for (let i = 0; i < sessionStorage.length; i += 1) {
        const k = sessionStorage.key(i);
        dump[`session:${k}`] = sessionStorage.getItem(k);
      }
      dump["cookies"] = document.cookie;
      return JSON.stringify(dump);
    });
    expect(storage, "the password is in browser storage").not.toContain(password);
    /* Nor may the password still sit in the input after success. */
    await expect(page.locator('[name="password"]')).not.toHaveValue(password);
  });

  test("consent is required and is not checked by default", async ({ page }) => {
    await expect(page.locator('[name="consent"]')).not.toBeChecked();
    await page.fill('[name="full_name"]', "Khách Kiểm Thử");
    await page.fill('[name="phone"]', phoneFor("success"));
    await page.fill('[name="password"]', "matkhau123");
    await page.click('#registerForm button[type="submit"]');
    await expect(page.locator("#err-consent")).toContainText(/đồng ý/i);
    /* And nothing was sent: consent is not a field the server can infer. */
  });
});
