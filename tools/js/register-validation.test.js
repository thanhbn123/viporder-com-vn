/* Tests for static/js/register-validation.js.
 *
 * The two decisions in this file are the most consequential in the registration
 * page: where a customer is sent after registering, and whether they are allowed
 * to submit at all. Neither needs a DOM, which is why they are here rather than
 * behind a test-library dependency.
 *
 * Run: node --test "tools/js/*.test.js"
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const v = require("../../static/js/register-validation.js");

const MARKETING_ORIGIN = "https://viporder.com.vn";
const PORTAL_ORIGIN = v.PORTAL_URL;

/* ==========================================================================
 * safePortalUrl - the open-redirect guard
 * ======================================================================== */

test("the approved portal host over https is accepted", () => {
  assert.equal(
    v.safePortalUrl("https://khachhang.viporder.com.vn/login", MARKETING_ORIGIN),
    "https://khachhang.viporder.com.vn/login",
  );
  assert.equal(
    v.safePortalUrl("https://khachhang.viporder.com.vn", MARKETING_ORIGIN),
    "https://khachhang.viporder.com.vn/",
  );
});

test("http is refused even on the right host", () => {
  /* An http portal is a downgrade an attacker on the path can read - and the
   * customer has just typed a password into this page. */
  assert.equal(v.safePortalUrl("http://khachhang.viporder.com.vn/login", MARKETING_ORIGIN), v.PORTAL_URL);
});

test("another host is refused", () => {
  for (const candidate of [
    "https://evil.com/login",
    "https://viporder.com.vn/login",
    "https://www.viporder.com.vn/login",
    "https://khachhang.viporder.com.vn.evil.com/login",
  ]) {
    assert.equal(v.safePortalUrl(candidate, MARKETING_ORIGIN), v.PORTAL_URL, candidate);
  }
});

test("userinfo cannot impersonate the portal host", () => {
  /* `https://khachhang.viporder.com.vn@evil.com/` PARSES with host evil.com. A
   * check written against the raw string - a prefix or `includes` test - accepts
   * it and sends the customer to evil.com. */
  for (const candidate of [
    "https://khachhang.viporder.com.vn@evil.com/",
    "https://khachhang.viporder.com.vn:pass@evil.com/",
  ]) {
    assert.equal(v.safePortalUrl(candidate, MARKETING_ORIGIN), v.PORTAL_URL, candidate);
  }
});

test("a non-https scheme is refused", () => {
  for (const candidate of [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ftp://khachhang.viporder.com.vn/",
    "file:///etc/passwd",
  ]) {
    assert.equal(v.safePortalUrl(candidate, MARKETING_ORIGIN), v.PORTAL_URL, candidate);
  }
});

test("a protocol-relative URL resolves against the ORIGIN, not the portal", () => {
  /* `//evil.com/x` from the marketing site is evil.com over https, and must be
   * refused. This is the case a naive `startsWith("https://")` test gets wrong
   * in the other direction. */
  assert.equal(v.safePortalUrl("//evil.com/x", MARKETING_ORIGIN), v.PORTAL_URL);
});

test("a relative path from the marketing site is refused", () => {
  /* `/login` on viporder.com.vn is NOT the portal. Resolving relatives against
   * the current origin is correct; accepting them as "ours" is not. */
  assert.equal(v.safePortalUrl("/login", MARKETING_ORIGIN), v.PORTAL_URL);
});

test("a relative path from the portal origin is allowed", () => {
  assert.equal(
    v.safePortalUrl("/login", PORTAL_ORIGIN),
    "https://khachhang.viporder.com.vn/login",
  );
});

test("missing, empty or unparseable candidates fall back to the portal", () => {
  /* Never to nothing: the customer has already registered, and no link is a
   * worse outcome than the right link. */
  for (const candidate of [null, undefined, "", 0, false, "not a url", "ht!tp://["]) {
    assert.equal(v.safePortalUrl(candidate, MARKETING_ORIGIN), v.PORTAL_URL, String(candidate));
  }
});

test("it never throws, whatever it is handed", () => {
  const inputs = [null, undefined, 0, 42, true, {}, [], "https://", "://", "%%%"];
  for (const candidate of inputs) {
    for (const origin of inputs) {
      assert.doesNotThrow(() => v.safePortalUrl(candidate, origin));
    }
  }
});

test("the fallback is always the module's own portal, never the candidate", () => {
  /* The property that makes the guard a guard: every refusal returns the SAME
   * known-good value, so no input can influence where a refused customer goes. */
  const refusals = [
    "https://evil.com/",
    "http://khachhang.viporder.com.vn/",
    "//evil.com/",
    "javascript:alert(1)",
    "",
    null,
  ];
  const results = new Set(refusals.map((c) => v.safePortalUrl(c, MARKETING_ORIGIN)));
  assert.deepEqual([...results], [v.PORTAL_URL]);
});

/* ==========================================================================
 * validate
 * ======================================================================== */

const GOOD = {
  full_name: "Nguyễn Văn A",
  phone: "0912345678",
  password: "matkhau123",
  consent: true,
};

test("a complete set of values produces no errors", () => {
  assert.deepEqual(v.validate(GOOD), {});
});

test("each missing field is reported, and only that field", () => {
  const cases = [
    ["full_name", { full_name: "" }],
    ["phone", { phone: "" }],
    ["password", { password: "" }],
    ["consent", { consent: false }],
  ];
  for (const [field, patch] of cases) {
    const errors = v.validate({ ...GOOD, ...patch });
    assert.deepEqual(Object.keys(errors), [field], `${field}: ${JSON.stringify(errors)}`);
  }
});

test("a one-character name is too short", () => {
  assert.ok(v.validate({ ...GOOD, full_name: "A" }).full_name);
  assert.equal(v.validate({ ...GOOD, full_name: "An" }).full_name, undefined);
});

test("a password under eight characters is refused, eight is not", () => {
  assert.ok(v.validate({ ...GOOD, password: "1234567" }).password);
  assert.equal(v.validate({ ...GOOD, password: "12345678" }).password, undefined);
});

test("separators in a phone number do not make it invalid", () => {
  /* People type spaces and dots. The server normalises; so must the check that
   * decides whether to let them submit. */
  for (const phone of [
    "0912345678",
    "0912 345 678",
    "0912.345.678",
    "0912-345-678",
    "0912 345-678",
    "(0912) 345 678",
    "+84912345678",
    "84912345678",
  ]) {
    assert.equal(v.validate({ ...GOOD, phone }).phone, undefined, `${phone} must be accepted`);
  }
});

test("phones that are not Vietnamese mobile/landline forms are refused", () => {
  for (const phone of [
    "12345",
    "091234567",      // one digit short
    "09123456789",    // landline length on a mobile prefix
    "0412345678",     // 4 is not a mobile prefix
    "8491234567",     // short with the country code
    "abc",
    "0912 345 67a",
  ]) {
    assert.ok(v.validate({ ...GOOD, phone }).phone, `${phone} must be refused`);
  }
});

test("validate never throws and always answers with an object", () => {
  for (const input of [null, undefined, 0, "", "string", [], true]) {
    const errors = v.validate(input);
    assert.equal(typeof errors, "object", String(input));
    assert.ok(errors !== null, String(input));
  }
});

test("an empty form reports every required field", () => {
  const errors = v.validate({});
  assert.deepEqual(Object.keys(errors).sort(), ["consent", "full_name", "password", "phone"]);
});

test("email is deliberately NOT validated here", () => {
  /* The server owns that rule. A second, different copy on the client would
   * block customers the server would have accepted. */
  const errors = v.validate({ ...GOOD, email: "not-an-email" });
  assert.deepEqual(errors, {});
});

/* ==========================================================================
 * Small helpers
 * ======================================================================== */

test("normalisePhone strips only separators", () => {
  assert.equal(v.normalisePhone(" 0912 345 678 "), "0912345678");
  assert.equal(v.normalisePhone("(0912).345-678"), "0912345678");
  assert.equal(v.normalisePhone("+84 912 345 678"), "+84912345678");
  assert.equal(v.normalisePhone(null), "");
  assert.equal(v.normalisePhone(undefined), "");
  assert.equal(v.normalisePhone(42), "42");
});

test("own sees only own properties", () => {
  assert.equal(v.own({ a: 1 }, "a"), true);
  assert.equal(v.own({ a: 1 }, "b"), false);
  assert.equal(v.own({}, "toString"), false, "inherited keys must not count");
});

test("hasKeys ignores inherited and empty objects", () => {
  assert.equal(v.hasKeys({}), false);
  assert.equal(v.hasKeys({ a: 1 }), true);
  assert.equal(v.hasKeys(Object.create({ inherited: 1 })), false);
  assert.equal(v.hasKeys({ a: undefined }), true, "a present key still counts");
});

/* ==========================================================================
 * The module contract
 * ======================================================================== */

test("it attaches itself with no CommonJS present", () => {
  const { execFileSync } = require("node:child_process");
  const path = require("node:path");
  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      path.join(__dirname, "..", "..", "static", "js", "register-validation.js"),
    )}, "utf8");
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderRegisterValidation;
    if (!api) { throw new Error("did not attach VipOrderRegisterValidation"); }
    // The guard must survive the browser path, not merely load.
    if (api.safePortalUrl("https://evil.com/", "https://viporder.com.vn") !== api.PORTAL_URL) {
      throw new Error("browser copy let a foreign host through");
    }
    process.stdout.write("ok");
  `;
  assert.equal(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }), "ok");
});
