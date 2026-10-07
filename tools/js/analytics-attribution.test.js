/* Tests for static/js/analytics-attribution.js.
 *
 * This is the BOUNDARY: the attribution module decides what the page remembers
 * and what it sends. Nothing executed it before, so "the analytics layer does not
 * leak" was a habit rather than a property.
 *
 * Run: node --test "tools/js/*.test.js"
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const attribution = require("../../static/js/analytics-attribution.js");

const { MAX_FIELD, MAX_URL, UTM_KEYS } = attribution;

/* --------------------------------------------------------------------------
 * The boundary: exactly these fields, and no others
 * ------------------------------------------------------------------------ */

test("the API payload has EXACTLY the seven expected fields", () => {
  /* Stated as set equality rather than as a spot check. If someone adds a field
   * here without thinking about what it carries, this fails - which is the whole
   * reason the boundary lives in a function instead of in a habit. */
  const payload = attribution.toApiAttributionFrom(attribution.emptyTouch());
  assert.deepEqual(
    Object.keys(payload).sort(),
    [
      "landing_page",
      "referrer",
      "utm_campaign",
      "utm_content",
      "utm_medium",
      "utm_source",
      "utm_term",
    ],
  );
});

test("a stored touch cannot smuggle unknown keys into the payload", () => {
  /* The reason `normaliseTouch` REBUILDS rather than merges: anything that ever
   * writes to storage - including other scripts on the page - would otherwise be
   * able to add a field and have it forwarded. */
  const hostile = {
    captured_at: 1,
    landing_page: "/ok",
    referrer: "/ref",
    utm: { utm_source: "fb", utm_medium: "cpc" },
    password: "hunter2",
    email: "khach@example.com",
    phone: "+84901234567",
    tracking_token: "tok_live_abc",
    __proto__: { injected: "x" },
  };

  const payload = attribution.toApiAttributionFrom(hostile);
  const sent = JSON.stringify(payload);

  for (const secret of ["hunter2", "khach@example.com", "+84901234567", "tok_live_abc", "injected"]) {
    assert.equal(sent.includes(secret), false, `the payload carried ${secret}: ${sent}`);
  }
  assert.deepEqual(Object.keys(payload).sort(), [
    "landing_page",
    "referrer",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
  ]);
});

test("unknown keys are dropped from the normalised touch itself, not only the payload", () => {
  const normalised = attribution.normaliseTouch({
    captured_at: 0,
    landing_page: "/",
    referrer: "",
    utm: { utm_source: "x" },
    extra: "should not survive",
  });
  assert.deepEqual(Object.keys(normalised).sort(), ["captured_at", "landing_page", "referrer", "utm"]);
  assert.deepEqual(Object.keys(normalised.utm).sort(), [...UTM_KEYS].sort());
});

/* --------------------------------------------------------------------------
 * Nothing unbounded leaves
 * ------------------------------------------------------------------------ */

test("a very long UTM value is clipped, not forwarded whole", () => {
  const huge = "x".repeat(10000);
  const touch = attribution.normaliseTouch({
    utm: { utm_source: huge },
    landing_page: `/${huge}`,
  });
  assert.equal(touch.utm.utm_source.length, MAX_FIELD);
  assert.equal(touch.landing_page.length, MAX_URL);
});

test("readUtms clips every parameter it reads", () => {
  const search = UTM_KEYS.map((k) => `${k}=${"y".repeat(500)}`).join("&");
  const utms = attribution.readUtms(search);
  for (const key of UTM_KEYS) {
    assert.equal(utms[key].length, MAX_FIELD, `${key} was not clipped`);
  }
});

test("clip handles null, undefined and numbers without throwing", () => {
  assert.equal(attribution.clip(null, 10), "");
  assert.equal(attribution.clip(undefined, 10), "");
  assert.equal(attribution.clip(42, 10), "42");
  assert.equal(attribution.clip("abcdef", 3), "abc");
  // A max of 0 clips everything away, which is the honest reading of "0".
  assert.equal(attribution.clip("abc", 0), "");
  assert.equal(attribution.clip("", 5), "");
});

/* --------------------------------------------------------------------------
 * Shape stability: an absent value is never `undefined`
 * ------------------------------------------------------------------------ */

test("readUtms always returns EVERY key, defaulting to empty string", () => {
  /* So no caller has to guard, and no `undefined` can reach a request body. */
  const utms = attribution.readUtms("utm_source=fb");
  assert.deepEqual(Object.keys(utms).sort(), [...UTM_KEYS].sort());
  for (const key of UTM_KEYS) {
    assert.equal(typeof utms[key], "string", `${key} is not a string`);
  }
  assert.equal(utms.utm_source, "fb");
  assert.equal(utms.utm_medium, "");
});

test("readUtms survives a missing or empty search string", () => {
  for (const input of [undefined, null, "", "???"]) {
    const utms = attribution.readUtms(input);
    assert.equal(utms.utm_source, "");
  }
});

test("emptyTouch has the same shape as a normalised touch", () => {
  /* Two shapes for "no attribution" is how `undefined` gets into a payload. */
  const empty = attribution.emptyTouch();
  assert.deepEqual(Object.keys(empty).sort(), ["captured_at", "landing_page", "referrer", "utm"]);
  assert.deepEqual(Object.keys(empty.utm).sort(), [...UTM_KEYS].sort());
});

test("garbage input yields the empty shape rather than throwing or returning null", () => {
  for (const input of [null, undefined, 0, "", "a string", 42, [], true]) {
    const touch = attribution.normaliseTouch(input);
    assert.equal(typeof touch, "object", `normaliseTouch(${JSON.stringify(input)})`);
    assert.deepEqual(
      Object.keys(touch).sort(),
      ["captured_at", "landing_page", "referrer", "utm"],
      `normaliseTouch(${JSON.stringify(input)}) returned the wrong shape`,
    );
    assert.equal(touch.captured_at, 0);
  }
});

test("a touch whose utm is not an object still yields all five UTM keys", () => {
  const touch = attribution.normaliseTouch({ utm: "not-an-object" });
  assert.deepEqual(Object.keys(touch.utm).sort(), [...UTM_KEYS].sort());
});

/* --------------------------------------------------------------------------
 * hasAnyUtm
 * ------------------------------------------------------------------------ */

test("hasAnyUtm is true only when some real value is present", () => {
  assert.equal(attribution.hasAnyUtm(attribution.emptyTouch().utm), false);
  assert.equal(attribution.hasAnyUtm(attribution.readUtms("utm_source=fb")), true);
  assert.equal(attribution.hasAnyUtm(attribution.readUtms("utm_term=  ")), true);
  assert.equal(attribution.hasAnyUtm(attribution.readUtms("other=1")), false);
  /* Defensive: callers pass whatever they have. */
  for (const input of [null, undefined, "string", 42]) {
    assert.equal(attribution.hasAnyUtm(input), false, `hasAnyUtm(${JSON.stringify(input)})`);
  }
});

/* --------------------------------------------------------------------------
 * Failures are reported, never swallowed
 * ------------------------------------------------------------------------ */

test("a broken URLSearchParams is REPORTED rather than silently swallowed", () => {
  /* A silently failed read looks exactly like "nobody arrived with UTMs", which
   * is a real answer that would be wrong. */
  const real = globalThis.URLSearchParams;
  globalThis.URLSearchParams = function Broken() {
    throw new Error("boom");
  };
  try {
    const seen = [];
    const utms = attribution.readUtms("utm_source=fb", (err) => seen.push(err));
    assert.equal(seen.length, 1, "the failure was not reported");
    assert.equal(utms.utm_source, "", "the shape must still be complete");
    assert.deepEqual(Object.keys(utms).sort(), [...UTM_KEYS].sort());
  } finally {
    globalThis.URLSearchParams = real;
  }
});

test("readUtms works with no onError callback at all", () => {
  const real = globalThis.URLSearchParams;
  globalThis.URLSearchParams = function Broken() {
    throw new Error("boom");
  };
  try {
    assert.doesNotThrow(() => attribution.readUtms("utm_source=fb"));
  } finally {
    globalThis.URLSearchParams = real;
  }
});

/* --------------------------------------------------------------------------
 * The module contract
 * ------------------------------------------------------------------------ */

test("it loads and attaches itself with no CommonJS present", () => {
  const { execFileSync } = require("node:child_process");
  const path = require("node:path");
  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      path.join(__dirname, "..", "..", "static", "js", "analytics-attribution.js"),
    )}, "utf8");
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderAttribution;
    if (!api) { throw new Error("did not attach VipOrderAttribution"); }
    // The boundary must survive the browser path, not merely load.
    const keys = Object.keys(api.toApiAttributionFrom(api.emptyTouch())).sort().join(",");
    if (keys !== "landing_page,referrer,utm_campaign,utm_content,utm_medium,utm_source,utm_term") {
      throw new Error("browser copy has a different payload shape: " + keys);
    }
    process.stdout.write("ok");
  `;
  assert.equal(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }), "ok");
});
