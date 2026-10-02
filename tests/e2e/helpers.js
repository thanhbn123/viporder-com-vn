/* Shared helpers for the browser gate.
 *
 * The phone number is the CONTROL CHANNEL for these tests. The mock provider
 * picks its behaviour from the last four digits (backend/app/providers/mock.py),
 * so a scenario is selected by choosing a number rather than by restarting the
 * server or waiting on a timer. That is what makes the failure paths
 * deterministic instead of flaky.
 */

"use strict";

const { expect } = require("@playwright/test");

/* Suffixes the mock provider treats specially. Anything else succeeds. */
const SUFFIX = {
  duplicate: "0000",
  invalid: "0001",
  unavailable: "0002",
  timeout: "0003",
  error: "0004",
};

/** A Vietnamese mobile number that passes VN_PHONE and is unique per call. */
function phoneFor(scenario) {
  const suffix = SUFFIX[scenario];
  if (!suffix) {
    /* A successful registration must NOT collide with a special suffix, and must
     * not repeat within a run or the second one is a duplicate. */
    const n = String(Math.floor(Math.random() * 10000)).padStart(4, "0");
    const safe = n.endsWith("0000") || n.endsWith("0001") ? "7777" : n;
    return `+849${String(Math.floor(Math.random() * 100000000)).padStart(8, "0").slice(0, 4)}${safe}`;
  }
  /* `+84` + a 9-digit subscriber number: `9` + 4 random digits + the 4-digit
   * suffix. Getting this wrong makes VN_PHONE reject the number and the API
   * answers 422 — so the provider under test is never reached and the test
   * silently measures validation instead of the scenario it names. */
  return `+849${String(Math.floor(Math.random() * 10000)).padStart(4, "0")}${suffix}`;
}

/** Fill every field the form requires, and only those.
 *
 * The email does NOT have to be unique: the API deduplicates customers on the
 * PHONE (that is the identity the idempotency key is bound to), so one stable
 * address keeps this helper free of a second source of randomness.
 *
 * The confirmation is filled with the same value as the password on purpose —
 * they must match, and a caller that wants the mismatch case sets them apart
 * explicitly. */
const TEST_EMAIL = "khach.kiem.thu@viporder.com.vn";

async function fillRegistration(
  page,
  { phone, password = "matkhau123", name = "Khách Kiểm Thử", email = TEST_EMAIL },
) {
  await page.fill('[name="full_name"]', name);
  await page.fill('[name="phone"]', phone);
  await page.fill('[name="email"]', email);
  await page.fill('[name="password"]', password);
  await page.fill('[name="confirmPassword"]', password);
  await page.check('[name="acceptTerms"]');
}

/** Submit and wait for the form to report back through its live region. */
async function submitAndAwait(page) {
  await page.click('#registerForm button[type="submit"]');
  await expect(page.locator("#formMessage")).not.toBeEmpty({ timeout: 20_000 });
}

/** True when the page is wider than the viewport — horizontal overflow. */
async function hasHorizontalOverflow(page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    return de.scrollWidth > de.clientWidth + 1;
  });
}

module.exports = {
  SUFFIX,
  TEST_EMAIL,
  phoneFor,
  fillRegistration,
  submitAndAwait,
  hasHorizontalOverflow,
};
