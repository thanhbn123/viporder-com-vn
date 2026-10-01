/* Tests for static/js/register-errors.js.
 *
 * WHY THIS FILE EXISTS. The bug it guards was NOT a typo - it was a change in one
 * layer that silently gave an existing HTTP status a new meaning, in a layer that
 * had no test at all. Everything else in this project is covered; the browser code
 * was the only part verified by reading.
 *
 * Run: node --test tools/js/
 * No dependencies, no browser: `node:test` and `node:assert` are built in, so
 * this adds no supply-chain surface to a project that audits its dependencies.
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const errors = require("../../static/js/register-errors.js");

/* --------------------------------------------------------------------------
 * The distinction the whole file exists for
 * ------------------------------------------------------------------------ */

test("DUPLICATE_PHONE is permanent", () => {
  assert.equal(errors.isPermanentDuplicate(409, "DUPLICATE_PHONE"), true);
});

test("REGISTRATION_IN_PROGRESS is NOT permanent", () => {
  /* The bug: every 409 was treated as this being true, which discarded the
   * idempotency key of an attempt that was still running. */
  assert.equal(errors.isPermanentDuplicate(409, "REGISTRATION_IN_PROGRESS"), false);
  assert.equal(errors.isInProgress(409, "REGISTRATION_IN_PROGRESS"), true);
});

test("a 409 with no code keeps the old behaviour", () => {
  /* Documented as deliberate: the recoverable direction. Keeping a stale key
   * after a real duplicate locks the customer out permanently; resetting it
   * early costs one redundant attempt. */
  assert.equal(errors.isPermanentDuplicate(409, undefined), true);
  assert.equal(errors.isPermanentDuplicate(409, ""), true);
});

test("only 409 is ever a duplicate", () => {
  for (const status of [200, 201, 202, 400, 422, 429, 500, 503]) {
    assert.equal(
      errors.isPermanentDuplicate(status, "DUPLICATE_PHONE"),
      false,
      `status ${status} must not be treated as a permanent duplicate`,
    );
  }
});

/* --------------------------------------------------------------------------
 * The plan: what the form does with a failure
 * ------------------------------------------------------------------------ */

test("a permanent duplicate suggests a new key and blames the phone field", () => {
  const plan = errors.planFor(409, "DUPLICATE_PHONE");
  assert.equal(plan.resetKey, true);
  assert.equal(plan.phoneFieldError, true);
  assert.equal(plan.retryable, false);
});

test("an in-flight attempt keeps the key and does NOT blame the phone field", () => {
  const plan = errors.planFor(409, "REGISTRATION_IN_PROGRESS");
  assert.equal(plan.resetKey, false, "resetting here opens a second registration");
  assert.equal(plan.phoneFieldError, false, "the number is fine");
  assert.equal(plan.retryable, true, "the server asked the customer to retry");
});

test("the two 409s produce opposite plans", () => {
  /* The assertion that would have caught the original bug, stated as the
   * property rather than as two separate expectations. */
  const permanent = errors.planFor(409, "DUPLICATE_PHONE");
  const transient = errors.planFor(409, "REGISTRATION_IN_PROGRESS");
  assert.notEqual(permanent.resetKey, transient.resetKey);
  assert.notEqual(permanent.phoneFieldError, transient.phoneFieldError);
});

test("a retryable failure never also resets the key", () => {
  /* The invariant, not an example: these two must never both be true. */
  const codes = ["DUPLICATE_PHONE", "REGISTRATION_IN_PROGRESS", "", undefined, "OTHER"];
  for (const status of [409, 422, 429, 500, 503]) {
    for (const code of codes) {
      const plan = errors.planFor(status, code);
      assert.equal(
        plan.retryable && plan.resetKey,
        false,
        `status ${status} code ${code}: both retryable and resetKey`,
      );
    }
  }
});

/* --------------------------------------------------------------------------
 * Wording - a wrong message is a wrong answer to the customer
 * ------------------------------------------------------------------------ */

test("no message claims a registration exists when it may not", () => {
  /* The 409 fallback used to say "this number is already registered" outright and
   * send the customer to sign in - to an account that may not exist yet. */
  const message = errors.defaultMessageFor(409);
  assert.ok(message.length > 0);
  assert.ok(
    !/^Số điện thoại này đã được đăng ký\./.test(message),
    "the message must not assert a completed registration on its own",
  );
  assert.ok(message.includes("thử lại"), "it must offer the retry that works");
});

test("a 5xx never claims nothing happened", () => {
  /* The lead may well have been stored. Saying otherwise invites the customer to
   * assume they are not registered. */
  for (const status of [500, 502, 503, 504]) {
    const message = errors.defaultMessageFor(status);
    assert.ok(
      message.includes("có thể đã được ghi nhận"),
      `status ${status} must allow that the lead was stored: ${message}`,
    );
  }
});

test("every status gets a non-empty message", () => {
  for (const status of [400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504, 0, 999]) {
    const message = errors.defaultMessageFor(status);
    assert.equal(typeof message, "string", `status ${status}`);
    assert.ok(message.trim().length > 0, `status ${status} produced an empty message`);
  }
});

test("a 422 points at the invalid fields rather than inventing a cause", () => {
  assert.ok(errors.defaultMessageFor(422).includes("chưa hợp lệ"));
});

test("a 429 says to wait rather than blaming the customer's details", () => {
  assert.ok(errors.defaultMessageFor(429).includes("quá nhiều lần"));
});

/* --------------------------------------------------------------------------
 * The module contract itself
 * ------------------------------------------------------------------------ */

test("the module loads with no browser present", () => {
  /* It is a plain deferred script in the page and a CommonJS export here. If it
   * ever starts touching `window` at load time, this fails. */
  assert.equal(typeof errors.planFor, "function");
  assert.equal(typeof errors.isPermanentDuplicate, "function");
  assert.equal(typeof errors.isInProgress, "function");
  assert.equal(typeof errors.defaultMessageFor, "function");
});

test("it attaches itself to the global when loaded as a plain browser script", () => {
  /* The page loads this with a deferred <script src>, where `module` does not
   * exist. The CommonJS tests above pass either way, so without this the browser
   * path - the one that actually runs for customers - had no coverage at all.
   *
   * Run in a child process with `module` shadowed, because the file checks
   * `typeof module === "object"` and this test file always has one. */
  const { execFileSync } = require("node:child_process");
  const path = require("node:path");

  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      path.join(__dirname, "..", "..", "static", "js", "register-errors.js"),
    )}, "utf8");
    // A browser-shaped sandbox: no \`module\`, no \`exports\`, but a global object.
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderRegisterErrors;
    if (!api) { throw new Error("the script did not attach VipOrderRegisterErrors"); }
    if (typeof api.planFor !== "function") { throw new Error("planFor missing"); }
    // The rule itself must survive the browser path, not just load.
    if (api.isPermanentDuplicate(409, "REGISTRATION_IN_PROGRESS") !== false) {
      throw new Error("the browser copy treats an in-flight attempt as permanent");
    }
    process.stdout.write("ok");
  `;

  const out = execFileSync(process.execPath, ["-e", script], { encoding: "utf8" });
  assert.equal(out, "ok");
});
