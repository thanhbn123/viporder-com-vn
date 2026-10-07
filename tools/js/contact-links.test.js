/* Tests for static/js/contact-links.js.
 *
 * The classification decides how a click is LABELLED in analytics. A missed link
 * means a click is never counted; a false match means the numbers are wrong. The
 * false matches are the interesting half, so the near-misses are pinned.
 *
 * Run: node --test "tools/js/*.test.js"
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { contactKind } = require("../../static/js/contact-links.js");

/* --------------------------------------------------------------------------
 * The declared attribute wins
 * ------------------------------------------------------------------------ */

test("an explicit declaration is honoured", () => {
  assert.equal(contactKind("phone", ""), "phone");
  assert.equal(contactKind("zalo", ""), "zalo");
});

test("an explicit declaration beats a conflicting URL", () => {
  /* The only way to label a link whose URL does not reveal the kind - a
   * shortened Zalo link, a redirect, an in-page button. If the URL could
   * override it, the attribute would be useless. */
  assert.equal(contactKind("zalo", "tel:+84901234567"), "zalo");
  assert.equal(contactKind("phone", "https://zalo.me/123"), "phone");
});

test("an unrecognised declaration falls through to the URL", () => {
  for (const junk of ["", "whatsapp", "PHONE", "tel", null, undefined, 0, 42, {}]) {
    assert.equal(
      contactKind(junk, "tel:+84901234567"),
      "phone",
      `declaration ${JSON.stringify(junk)} must not be treated as valid`,
    );
  }
});

/* --------------------------------------------------------------------------
 * Phone
 * ------------------------------------------------------------------------ */

test("tel: links are phone links, whatever the case", () => {
  assert.equal(contactKind(null, "tel:+84901234567"), "phone");
  assert.equal(contactKind(null, "TEL:+84901234567"), "phone");
  assert.equal(contactKind(null, "Tel:0912345678"), "phone");
});

test("a bare phone number is NOT a phone link", () => {
  /* Only the scheme makes it one. A number in text is not a link, and guessing
   * would invent clicks that never happened. */
  for (const value of ["+84901234567", "0912345678", "tel", "telephone:+84"]) {
    assert.equal(contactKind(null, value), null, `${value} must not be phone`);
  }
});

/* --------------------------------------------------------------------------
 * Zalo - and the near-misses
 * ------------------------------------------------------------------------ */

test("real Zalo links are recognised", () => {
  for (const url of [
    "https://zalo.me/123456789",
    "http://zalo.me/123456789",
    "https://zalo.com/123456789",
    "https://oa.zalo.me/home",
    "https://zalo.me",
    "https://ZALO.ME/123",
  ]) {
    assert.equal(contactKind(null, url), "zalo", `${url} must be zalo`);
  }
});

test("links that merely CONTAIN zalo.me are not Zalo links", () => {
  /* The half worth testing: a looser pattern - `includes("zalo.me")`, or an
   * unanchored regex - accepts every one of these, and the analytics would then
   * report traffic from a domain we have nothing to do with. */
  for (const url of [
    "https://notzalo.me/123",
    "https://zalo.me.evil.com/123",
    "https://evil.com/zalo.me/123",
    "https://zalo.me@evil.com/123",
    "https://evil.com/?next=https://zalo.me/123",
    "https://myzalo.me/123",
    "https://zalo.me-x.com/123",
  ]) {
    assert.equal(contactKind(null, url), null, `${url} must NOT be zalo`);
  }
});

test("a zalo link without a scheme is not classified", () => {
  assert.equal(contactKind(null, "zalo.me/123"), null);
  assert.equal(contactKind(null, "//zalo.me/123"), null);
});

/* --------------------------------------------------------------------------
 * Everything else
 * ------------------------------------------------------------------------ */

test("ordinary links and empty input yield null, never a guess", () => {
  for (const value of [
    null,
    undefined,
    "",
    "https://viporder.com.vn/",
    "mailto:khach@example.com",
    "#",
    "/dang-ky",
    "javascript:void(0)",
  ]) {
    assert.equal(contactKind(null, value), null, `${JSON.stringify(value)} must be null`);
  }
});

test("it never throws on hostile input", () => {
  const values = [null, undefined, 0, 42, true, {}, [], "tel:", "https://"];
  for (const declared of values) {
    for (const href of values) {
      assert.doesNotThrow(() => contactKind(declared, href));
    }
  }
});

/* --------------------------------------------------------------------------
 * The module contract
 * ------------------------------------------------------------------------ */

test("it attaches itself with no CommonJS present", () => {
  const { execFileSync } = require("node:child_process");
  const path = require("node:path");
  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      path.join(__dirname, "..", "..", "static", "js", "contact-links.js"),
    )}, "utf8");
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderContactLinks;
    if (!api) { throw new Error("did not attach VipOrderContactLinks"); }
    // The rule must survive the browser path, not merely load.
    if (api.contactKind(null, "https://zalo.me/1") !== "zalo") {
      throw new Error("browser copy failed to classify a Zalo link");
    }
    if (api.contactKind(null, "https://notzalo.me/1") !== null) {
      throw new Error("browser copy accepted a near-miss");
    }
    process.stdout.write("ok");
  `;
  assert.equal(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }), "ok");
});
