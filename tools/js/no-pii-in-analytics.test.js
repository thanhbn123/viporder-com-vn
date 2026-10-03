/* The analytics layer must not be able to carry customer PII.
 *
 * WHY THIS IS A SOURCE SCAN AND NOT A BEHAVIOUR TEST.
 *
 * `docs/PROJECT-STATUS.md` claims "No PII in any payload" for G05, and the claim
 * was true - but it was verified by READING, and the document said "adversarially
 * verified". A privacy claim with no check behind it is the exact pattern that
 * has gone stale four times in this project.
 *
 * The property happens to be structural: `analytics.js` never references a
 * customer field at all, so the events cannot carry one. There is no transform to
 * unit-test - the guarantee IS the absence of a reference. So the check is a
 * tripwire on the source, in the same spirit as `tools/check_nginx_config.py`:
 * it does not prove the property, it makes a specific regression loud.
 *
 * What it does NOT cover, stated so nobody reads more into it than exists:
 *   - it cannot see a value passed in from a caller under a neutral name;
 *   - it does not inspect the two third-party loaders' own code.
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const STATIC = path.join(__dirname, "..", "..", "static", "js");
const ANALYTICS_SOURCES = ["analytics.js", "analytics-attribution.js"];

/* The customer's own data, by every name this codebase uses for it. */
const FORBIDDEN = [
  "full_name",
  "password",
  "confirmPassword",
  "consent_given_at",
  "idempotency_key",
  "request_fingerprint",
  "tracking_token",
  "external_customer_id",
  "external_customer_code",
];

/* `email` and `phone` are checked separately: the analytics layer legitimately
 * mentions `phone_click` and Zalo/phone LINKS, which are the BUSINESS's own
 * contact details rather than a customer's. A bare substring search would fail
 * on those and the test would be deleted by whoever hit it - so the check is on
 * the customer-carrying forms only. */
const CUSTOMER_CONTACT = [/\bemail\b/i, /\bphone\s*:/i, /\bphone\s*=/i];

test("the analytics sources never name a customer data field", () => {
  for (const file of ANALYTICS_SOURCES) {
    const source = fs.readFileSync(path.join(STATIC, file), "utf8");
    for (const needle of FORBIDDEN) {
      assert.equal(
        source.includes(needle),
        false,
        `${file} references "${needle}" - analytics must not reach customer data`,
      );
    }
    for (const pattern of CUSTOMER_CONTACT) {
      assert.equal(
        pattern.test(source),
        false,
        `${file} matches ${pattern} - analytics must not reach customer contact data`,
      );
    }
  }
});

test("the analytics sources do reference the two things they may carry", () => {
  /* A guard that passes because it reads the wrong file is worse than none, so
   * assert positively that the files were actually read and are the real ones. */
  const source = fs.readFileSync(path.join(STATIC, "analytics.js"), "utf8");
  assert.ok(source.includes("phone_click"), "analytics.js did not look like itself");
  assert.ok(source.length > 5000, "analytics.js is suspiciously small");
});
