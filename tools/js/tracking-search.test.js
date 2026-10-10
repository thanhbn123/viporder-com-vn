/* Tests for static/js/tracking-search.js — the pure half of the G04B lookup.
 *
 * WHAT IS ACTUALLY AT STAKE HERE
 * ------------------------------
 * The DOM controller (static/js/tracking.js) cannot be executed by `node --test`,
 * so the three decisions that matter were moved into the module under test:
 *
 *   1. what the customer's typing is allowed to become (checkKeyword),
 *   2. what URL it becomes (keywordPath), and
 *   3. what HTTP status plus body MEANS to the customer (interpret).
 *
 * (1) and (2) are the security boundary of a public, unauthenticated endpoint:
 * the keyword is the only thing a stranger controls. (3) is the honesty boundary:
 * a broken lookup must never be reported as "không tìm thấy".
 *
 * Run: node --test "tools/js/*.test.js"
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const t = require("../../static/js/tracking-search.js");

const ROOT = path.join(__dirname, "..", "..");
const INDEX_HTML = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
/* Comments are stripped before the markup assertions below. A real parser ignores
 * them (which is what tools/check_site.py uses); a regex does not, and this file's
 * own explanatory comment contains the words "<script> body" — the first version
 * of this test failed on its own documentation. */
const INDEX_MARKUP = INDEX_HTML.replace(/<!--[\s\S]*?-->/g, "");

/* ==========================================================================
 * checkKeyword — the security boundary
 * ======================================================================== */

test("a keyword is trimmed and accepted", () => {
  assert.deepEqual(t.checkKeyword("  VIP12345  "), { ok: true, keyword: "VIP12345" });
});

test("empty and whitespace-only keywords are refused without a reason message going missing", () => {
  for (const input of ["", "   ", "\t\n ", null, undefined]) {
    const result = t.checkKeyword(input);
    assert.equal(result.ok, false, JSON.stringify(input));
    assert.equal(result.reason, "EMPTY");
    assert.ok(result.message, "a refusal must carry something the customer can read");
  }
});

test("a keyword longer than the cap is refused, not silently truncated", () => {
  /* Truncating would ask the provider about a DIFFERENT code and then present that
   * answer as the customer's parcel. */
  const long = "A".repeat(t.MAX_KEYWORD_LENGTH + 1);
  const result = t.checkKeyword(long);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "TOO_LONG");
  assert.match(result.message, /64/);

  const atCap = "A".repeat(t.MAX_KEYWORD_LENGTH);
  assert.equal(t.checkKeyword(atCap).ok, true, "exactly 64 characters must be accepted");
});

test("only letters, digits and - _ | . are allowed through", () => {
  for (const keyword of ["ABC123", "abc-123", "abc_123", "abc.123", "A|B|C", "09.12-3_4|x"]) {
    assert.equal(t.checkKeyword(keyword).ok, true, `${keyword} must be accepted`);
    assert.equal(t.checkKeyword(keyword).reason, undefined);
  }
});

test("every character that could change the MEANING of a URL is refused locally", () => {
  /* These are the ones that would otherwise be interpreted by the server or by
   * something in front of it rather than being data in a path. None of them may
   * reach a request. */
  const hostile = [
    "ABC 123",       // space
    "ABC/123",       // path separator
    "../admin",      // traversal
    "..%2fadmin",    // encoded traversal
    "ABC?x=1",       // query
    "ABC#frag",      // fragment
    "ABC&y=2",       // parameter separator
    "ABC=1",         // assignment
    "ABC%00",        // null byte
    "ABC%7C",        // pre-encoded separator
    "ABC\"123",      // quote
    "ABC'123",       // apostrophe
    "ABC<123>",      // markup
    "ABC\\123",      // backslash
    "ABC\n123",      // CR/LF: header and log splitting
    "ABC+123",       // plus (decodes to a space in form encoding)
    "tiếng Việt",    // non-ASCII
    "包裝",           // CJK
    "ABC;123",       // parameter separator
    "ABC@123",       // userinfo
    "ABC:123",       // port
  ];
  for (const keyword of hostile) {
    const result = t.checkKeyword(keyword);
    assert.equal(result.ok, false, `${JSON.stringify(keyword)} must be refused`);
    assert.equal(result.reason, "CHARSET", JSON.stringify(keyword));
    assert.ok(result.message);
  }
});

test("checkKeyword never throws, and always answers with a shaped result", () => {
  /* A caller can hand this anything — the DOM gives a string, but a future caller
   * (or a paste handler) may not. What is asserted here is the CONTRACT (no
   * exception, always `{ok}` and either a keyword or a message), not a claim about
   * which odd JavaScript values are objectionable: `checkKeyword(0)` accepts "0",
   * which is a perfectly valid sequence of allowed characters. */
  for (const input of [null, undefined, 0, 42, true, false, {}, [], NaN, Symbol("x")]) {
    let result;
    assert.doesNotThrow(() => { result = t.checkKeyword(input); }, String(input));
    assert.equal(typeof result.ok, "boolean", String(input));
    if (result.ok) {
      assert.equal(typeof result.keyword, "string", String(input));
      assert.equal(result.keyword, result.keyword.trim(), String(input));
    } else {
      assert.ok(result.message, String(input));
      assert.ok(result.reason, String(input));
    }
  }
});

/* ==========================================================================
 * keywordPath — the only place a URL is built
 * ======================================================================== */

test("the two documented paths are used, one per mode", () => {
  assert.equal(
    t.keywordPath(t.MODES.WAREHOUSE, "ABC123"),
    "/api/tracking/warehouse-imports/ABC123",
  );
  assert.equal(
    t.keywordPath(t.MODES.PACKAGE, "ABC123"),
    "/api/tracking/package-sealings/ABC123",
  );
});

test("an unknown mode falls back to the warehouse path rather than inventing one", () => {
  for (const mode of [null, undefined, "", "warehouse", "../admin", 42]) {
    assert.equal(t.keywordPath(mode, "ABC"), "/api/tracking/warehouse-imports/ABC", String(mode));
  }
});

test("the keyword is always encodeURIComponent-encoded", () => {
  /* `|` is the character in the allowed set that actually gets rewritten. This is
   * the second lock: it still holds if the allowed set is ever widened. */
  assert.equal(t.keywordPath("warehouse_import", "A|B"), "/api/tracking/warehouse-imports/A%7CB");
  assert.equal(t.keywordPath("warehouse_import", "A B"), "/api/tracking/warehouse-imports/A%20B");
  assert.equal(t.keywordPath("warehouse_import", "../x"), "/api/tracking/warehouse-imports/..%2Fx");
  assert.equal(
    t.keywordPath("warehouse_import", "a/b?c#d&e=f"),
    "/api/tracking/warehouse-imports/a%2Fb%3Fc%23d%26e%3Df",
  );
});

test("no keyword can add a path segment or a query to the request", () => {
  /* The property, stated as a property: the built URL begins with the documented
   * path and, after it, contains no `/`, `?` or `#` that came from the input. */
  const attempts = ["../secret", "A/B", "A?x=1", "A#f", "A|B", "A B", "包", "%2F"];
  for (const attempt of attempts) {
    const url = t.keywordPath("warehouse_import", attempt);
    const rest = url.slice("/api/tracking/warehouse-imports/".length);
    assert.doesNotMatch(rest, /[/?#]/, `${attempt} -> ${rest}`);
  }
});

/* ==========================================================================
 * interpret — status + body -> exactly one state
 * ======================================================================== */

const FOUND_BODY = {
  result: "found",
  search_type: "warehouse_import",
  data: { customer_code: "TT001" },
};

test("a 200 that matches the contract is FOUND and carries the data", () => {
  const outcome = t.interpret("warehouse_import", 200, FOUND_BODY);
  assert.equal(outcome.state, t.STATE.FOUND);
  assert.deepEqual(outcome.data, { customer_code: "TT001" });
  assert.equal(outcome.code, null);
});

test("the response's search_type decides the mode when it names one", () => {
  const outcome = t.interpret("warehouse_import", 200, {
    result: "found",
    search_type: "package_sealing",
    data: {},
  });
  assert.equal(outcome.mode, "package_sealing");
});

test("an unknown or missing search_type falls back to the requested mode", () => {
  for (const reported of [undefined, null, "", "gibberish", 42]) {
    const outcome = t.interpret("package_sealing", 200, {
      result: "found",
      search_type: reported,
      data: {},
    });
    assert.equal(outcome.mode, "package_sealing", String(reported));
  }
});

test("a 200 that is NOT the documented shape is a provider error, never a not-found", () => {
  /* The honesty rule. A broken reply is not evidence about the parcel, and telling
   * the customer "không tìm thấy" sends them to support with the wrong question. */
  const bodies = [
    null,
    {},
    { result: "found" },
    { result: "found", data: null },
    { result: "found", data: "not an object" },
    { result: "ok", data: {} },
    { "result": "found", "data": [] },
    "<html>gateway</html>",
  ];
  for (const body of bodies) {
    const outcome = t.interpret("warehouse_import", 200, body);
    assert.equal(outcome.state, t.STATE.PROVIDER_ERROR, JSON.stringify(body));
    assert.equal(outcome.code, "MALFORMED_RESPONSE", JSON.stringify(body));
  }
});

test("a contract 404 means NOT_FOUND", () => {
  const outcome = t.interpret("warehouse_import", 404, {
    error: { code: "NOT_FOUND", message: "Không tìm thấy mã vận đơn này." },
  });
  assert.equal(outcome.state, t.STATE.NOT_FOUND);
  assert.equal(outcome.code, "NOT_FOUND");
});

test("a 404 that is NOT the contract envelope is a provider error", () => {
  const bodies = [null, {}, { detail: "Not Found" }, { error: {} }, { error: { code: 42 } }];
  for (const body of bodies) {
    const outcome = t.interpret("warehouse_import", 404, body);
    assert.equal(outcome.state, t.STATE.PROVIDER_ERROR, JSON.stringify(body));
  }
});


test("a contract 400 means INVALID", () => {
  const outcome = t.interpret("warehouse_import", 400, {
    error: { code: "INVALID_KEYWORD", message: "Mã không hợp lệ." },
  });
  assert.equal(outcome.state, t.STATE.INVALID);
  assert.equal(outcome.code, "INVALID_KEYWORD");
});

test("400 without the contract envelope is a provider error", () => {
  for (const body of [null, {}, { detail: "bad request" }]) {
    assert.equal(
      t.interpret("warehouse_import", 400, body).state,
      t.STATE.PROVIDER_ERROR,
      JSON.stringify(body),
    );
  }
});

test("both provider failures, and anything else unexpected, are PROVIDER_ERROR", () => {
  const cases = [
    [502, { error: { code: "PROVIDER_ERROR", message: "..." } }],
    [503, { error: { code: "PROVIDER_UNAVAILABLE", message: "..." } }],
    [500, { error: { code: "INTERNAL", message: "..." } }],
    [429, null],
    [0, null],          // network failure / abort
    [200, null],        // replaced by the malformed case above; kept for the status
  ];
  for (const [status, body] of cases) {
    assert.equal(
      t.interpret("warehouse_import", status, body).state,
      t.STATE.PROVIDER_ERROR,
      `${status} ${JSON.stringify(body)}`,
    );
  }
});

test("a network failure is named, not swallowed", () => {
  const outcome = t.interpret("warehouse_import", 0, null);
  assert.equal(outcome.code, "NETWORK_ERROR");
});

test("the server's own message is never carried out of interpret", () => {
  /* The whole point of "never show a raw provider message": the client cannot tell
   * a curated Vietnamese sentence from a relayed provider string, so it renders its
   * own copy chosen by CODE. Nothing in the returned object may hold server prose.
   */
  const hostile = {
    error: { code: "PROVIDER_ERROR", message: "<img src=x onerror=alert(1)>" },
  };
  const outcome = t.interpret("warehouse_import", 502, hostile);
  assert.equal(outcome.state, t.STATE.PROVIDER_ERROR);
  assert.equal(JSON.stringify(outcome).includes("onerror"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(outcome, "message"), false);
});

test("interpret never throws, whatever it is handed", () => {
  const inputs = [null, undefined, 0, "", "x", [], {}, true, NaN];
  for (const status of [200, 400, 404, 502, 503, 0, "200", null]) {
    for (const body of inputs) {
      assert.doesNotThrow(() => t.interpret("warehouse_import", status, body));
    }
  }
});

/* ==========================================================================
 * resultForState — the analytics dimension
 * ======================================================================== */

test("each state maps to one of the three documented results", () => {
  assert.equal(t.resultForState(t.STATE.FOUND), "found");
  assert.equal(t.resultForState(t.STATE.NOT_FOUND), "not_found");
  assert.equal(t.resultForState(t.STATE.INVALID), "error");
  assert.equal(t.resultForState(t.STATE.PROVIDER_ERROR), "error");
});

test("IDLE and LOADING produce no result, so no event is fired for them", () => {
  assert.equal(t.resultForState(t.STATE.IDLE), null);
  assert.equal(t.resultForState(t.STATE.LOADING), null);
  assert.equal(t.resultForState("NONSENSE"), null);
});

/* ==========================================================================
 * viewFor — what the customer is shown
 * ======================================================================== */

test("absent fields are skipped entirely — no row, and no 'null'", () => {
  const view = t.viewFor("warehouse_import", {
    customer_code: "TT001",
    weight: null,
    date: undefined,
    china_tracking_code: "",
    package_sealing_code: "B999",
  });
  const labels = view.fields.map((row) => row.label);
  assert.deepEqual(labels, ["Mã khách", "Mã bao"]);
  assert.equal(JSON.stringify(view).includes("null"), false);
  assert.equal(JSON.stringify(view).includes("undefined"), false);
});

test("zero and false are VALUES and keep their row", () => {
  const view = t.viewFor("warehouse_import", { product_quantity: 0, package_quantity: 0 });
  assert.deepEqual(view.fields.map((row) => row.value), ["0", "0"]);
});

test("warehouse fields are rendered in the agreed order", () => {
  const view = t.viewFor("warehouse_import", {
    customer_code: "TT001",
    delivery_customer: "TT2840 - Khách nhận",
    delivery_code: "GH1",
    date: "2026-09-30",
    weight: "12.5",
    china_tracking_code: "CN1",
    vietnam_tracking_code: "VN1",
    vnpost_tracking_code: "VP1",
    package_sealing_code: "B1",
    product_name_vi: "Áo thun",
    product_quantity: "3",
    package_quantity: "1",
    status: "Đóng bao 打包",
  });
  assert.deepEqual(view.fields.map((row) => row.label), [
    "Mã khách",
    "Khách giao hàng",
    "Mã giao hàng",
    "Ngày nhập",
    "Cân nặng",
    "Mã vận đơn Trung Quốc",
    "Mã bao",
    "Tên hàng",
    "Số lượng",
    "Số kiện",
    "Trạng thái hiện tại",
  ]);
  /* The status row is part of the ordered layout, not appended: its position
   * differs between the two modes. */
  assert.equal(view.fields[view.fields.length - 1].value, "Đóng bao 打包");
  assert.equal(view.status, "Đóng bao 打包");
  assert.equal(view.statusLabel, "Trạng thái hiện tại");
});

test("the ORDER survives missing fields, because it is a layout and not an index", () => {
  /* A `splice` into a filtered array puts "Tên hàng" in the wrong place the moment
   * an earlier field is absent — which is the normal case, not the edge case. */
  const view = t.viewFor("warehouse_import", {
    package_sealing_code: "B1",
    product_name_vi: "Áo thun",
    product_quantity: "3",
  });
  assert.deepEqual(view.fields.map((row) => row.label), ["Mã bao", "Tên hàng", "Số lượng"]);
});

test("the Vietnamese product name leads and the Chinese one is kept alongside", () => {
  assert.equal(
    t.viewFor("warehouse_import", { product_name_vi: "Áo thun", product_name_cn: "T恤" })
      .fields[0].value,
    "Áo thun (T恤)",
  );
  assert.equal(
    t.viewFor("warehouse_import", { product_name_vi: "Áo thun" }).fields[0].value,
    "Áo thun",
  );
  assert.equal(t.viewFor("warehouse_import", { product_name_cn: "T恤" }).fields[0].value, "T恤");
});

test("Kích thước appears only when all three dimensions are present", () => {
  const complete = t.viewFor("package_sealing", { length: 10, width: 20, height: 30 });
  const dims = complete.fields.find((row) => /Kích thước/.test(row.label));
  assert.ok(dims, "a complete set of dimensions must be rendered");
  assert.equal(dims.value, "10 × 20 × 30");

  for (const partial of [
    { length: 10, width: 20 },
    { length: 10, height: 30 },
    { width: 20, height: 30 },
    { length: 10 },
    { length: 10, width: null, height: 30 },
  ]) {
    const view = t.viewFor("package_sealing", partial);
    assert.equal(
      view.fields.some((row) => /Kích thước/.test(row.label)),
      false,
      `a partial dimension must be dropped: ${JSON.stringify(partial)}`,
    );
  }
});

test("package mode renders its own fields with its own labels", () => {
  const view = t.viewFor("package_sealing", {
    code: "B1",
    customer_code: "TT001",
    actual_weight: "8.2",
    volume: "0.5",
    status: "Đóng bao 打包",
    created_at: "2026-09-30",
    warehouse_imports_count: 3,
  });
  assert.deepEqual(view.fields.map((row) => row.label), [
    "Mã bao",
    "Mã khách",
    "Cân nặng thực",
    "Thể tích",
    "Trạng thái",
    "Ngày tạo",
    "Số kiện bên trong",
  ]);
  /* Trạng thái comes BEFORE the date here, and last in warehouse mode. */
  assert.equal(view.fields[4].label, "Trạng thái");
  assert.equal(view.fields[4].value, "Đóng bao 打包");
  assert.equal(view.fields[6].value, "3");
  assert.equal(view.statusLabel, "Trạng thái");
  assert.equal(view.status, "Đóng bao 打包");
});

test("the whitelist is a whitelist: unknown keys are never rendered", () => {
  /* The backend is documented to strip these already. The point of the test is
   * that the client does not depend on that being true. */
  const view = t.viewFor("warehouse_import", {
    customer_code: "TT001",
    created_by: "nhanvien@viporder.com.vn",
    id: 987654,
    area_id: 42,
    internal_note: "do not show this",
    status_background_color: "#ff0000",
    status_text_color: "#ffffff",
    package_number: "PN-INTERNAL-1",
    is_pending: true,
    is_completed: false,
    kind: "warehouse_import",
  });
  const rendered = JSON.stringify(view);
  for (const leaked of [
    "nhanvien",
    "987654",
    "42",
    "do not show this",
    "#ff0000",
    "#ffffff",
    "PN-INTERNAL-1",
  ]) {
    assert.equal(rendered.includes(leaked), false, `${leaked} reached the render model`);
  }
  assert.deepEqual(view.fields.map((row) => row.label), ["Mã khách"]);
});

test("status logs keep the provider's bilingual status verbatim", () => {
  const view = t.viewFor("warehouse_import", {
    status_logs: [
      { status: "Đóng bao 打包", note: "Kho Quảng Châu", created_at: "2026-09-29 10:00" },
      { status: "Đang vận chuyển 运输中", note: null, created_at: "2026-09-30 08:00" },
    ],
  });
  assert.equal(view.logs.length, 2);
  assert.equal(view.logs[0].status, "Đóng bao 打包");
  assert.equal(view.logs[0].note, "Kho Quảng Châu");
  assert.equal(view.logs[1].status, "Đang vận chuyển 运输中");
  assert.equal(view.logs[1].note, "", "an absent note becomes an empty string, not 'null'");
  assert.equal(view.logsLabel, "Lịch sử trạng thái");
});

test("empty or malformed log entries are dropped rather than rendered as blank bullets", () => {
  const view = t.viewFor("warehouse_import", {
    status_logs: [null, {}, "text", { status: "" }, { status: "OK" }],
  });
  assert.equal(view.logs.length, 1);
  assert.equal(view.logs[0].status, "OK");
});

test("a package's inner imports become a sub-list of whitelisted rows", () => {
  const view = t.viewFor("package_sealing", {
    code: "B1",
    warehouse_imports_count: 2,
    warehouse_imports: [
      {
        china_tracking_code: "CN1",
        vietnam_tracking_code: "VN1",
        package_sealing_code: "B1",
        weight: "3.1",
        created_at: "2026-09-29",
        created_by: "secret",
      },
      { china_tracking_code: "CN2" },
      null,
      {},
    ],
  });
  assert.equal(view.children.length, 2, "an entry with no renderable field is not a card");
  assert.equal(view.children[0].fields.length, 4, "Mã vận đơn Việt Nam is no longer rendered");
  assert.equal(view.children[1].fields.length, 1);
  assert.equal(JSON.stringify(view).includes("secret"), false);
  assert.equal(view.childrenLabel, "Kiện hàng bên trong");
});

test("viewFor never throws and always answers with a renderable shape", () => {
  for (const mode of ["warehouse_import", "package_sealing", null, "junk"]) {
    for (const data of [null, undefined, 0, "", "text", [], {}, true]) {
      const view = t.viewFor(mode, data);
      assert.ok(Array.isArray(view.fields), String(data));
      assert.ok(Array.isArray(view.logs), String(data));
      assert.ok(Array.isArray(view.children), String(data));
    }
  }
});

/* ==========================================================================
 * present — the rule that decides what counts as "the provider sent nothing"
 * ======================================================================== */

test("present rejects only genuine absences", () => {
  for (const value of [null, undefined, "", "   ", "\n"]) {
    assert.equal(t.present(value), false, JSON.stringify(value));
  }
  for (const value of [0, 1, false, true, "0", "x"]) {
    assert.equal(t.present(value), true, JSON.stringify(value));
  }
  /* An EMPTY container is an absence too: `String([])` is `""`, so it is dropped
   * rather than rendered as a row with nothing in it. A non-empty one stringifies
   * to its contents and keeps its row. */
  assert.equal(t.present([]), false);
  assert.equal(t.present([1]), true);
  assert.equal(t.present({}), true, "String({}) is '[object Object]', which is not empty");
});

/* ==========================================================================
 * The page and the module must agree — cross-file, because neither can see the other
 * ======================================================================== */

test("the idle sentence in index.html is the one the module renders", () => {
  /* The section has to read correctly with scripts disabled, so the sentence is in
   * both places. This is what stops the duplicate from drifting: without it the
   * no-JS page and the JS page would eventually say different things. */
  assert.ok(
    INDEX_HTML.includes(t.MESSAGES.IDLE),
    `index.html does not contain the idle message: ${t.MESSAGES.IDLE}`,
  );
});

test("index.html carries the agreed placeholder and button label", () => {
  assert.ok(
    INDEX_HTML.includes('placeholder="Nhập mã vận đơn / mã kiện"'),
    "the placeholder is part of the agreed design",
  );
  assert.ok(INDEX_HTML.includes(">Tra cứu<") || INDEX_HTML.includes("Tra cứu\n"), "the button label");
  /* The two modes, spelled as the contract spells them. */
  assert.ok(INDEX_HTML.includes('data-mode="warehouse_import"'));
  assert.ok(INDEX_HTML.includes('data-mode="package_sealing"'));
});

test("index.html loads the rules module BEFORE the controller", () => {
  /* `defer` preserves document order, so this is the whole load-order guarantee:
   * the controller reads window.VipOrderTrackingSearch at start-up and would
   * otherwise render the section inert with no error anywhere. */
  const rules = INDEX_HTML.indexOf("/static/js/tracking-search.js");
  const controller = INDEX_HTML.indexOf("/static/js/tracking.js");
  assert.ok(rules > 0 && controller > 0, "both scripts must be referenced");
  assert.ok(rules < controller, "tracking-search.js must be loaded before tracking.js");
});

test("index.html contains no inline executable script and no inline handler", () => {
  /* check_site.py enforces this for the whole site; this is the narrow version
   * that fails next to the code it is about. The section is bound entirely from
   * static/js/tracking.js, which is what a strict CSP requires.
   *
   * A <script> with a `type` is a DATA block (application/ld+json) and is not
   * executable; the pattern below therefore looks only for a script that has
   * neither `src` nor `type` and still has a body — the one shape that would run
   * inline. */
  assert.doesNotMatch(INDEX_MARKUP, /\son[a-z]+\s*=/i, "an inline event handler is present");
  assert.doesNotMatch(
    INDEX_MARKUP,
    /<script(?![^>]*\ssrc=)(?![^>]*\stype=)[^>]*>\s*[^<\s]/i,
    "an inline executable <script> body is present",
  );
});

/* ==========================================================================
 * The module contract
 * ======================================================================== */

test("it attaches itself with no CommonJS present", () => {
  const { execFileSync } = require("node:child_process");
  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      path.join(ROOT, "static", "js", "tracking-search.js"),
    )}, "utf8");
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderTrackingSearch;
    if (!api) { throw new Error("did not attach VipOrderTrackingSearch"); }
    // The boundary must survive the browser path, not merely load.
    if (api.checkKeyword("A/../B").ok) { throw new Error("browser copy let a traversal through"); }
    if (api.keywordPath("warehouse_import", "A|B") !== "/api/tracking/warehouse-imports/A%7CB") {
      throw new Error("browser copy did not encode the keyword");
    }
    process.stdout.write("ok");
  `;
  assert.equal(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }), "ok");
});

test("no status row is invented when the provider sent no status", () => {
  for (const mode of ["warehouse_import", "package_sealing"]) {
    const view = t.viewFor(mode, { customer_code: "TT001", status: null });
    assert.equal(view.fields.some((row) => /Trạng thái/.test(row.label)), false, mode);
    assert.equal(view.status, "");
  }
});

test("the package's inner count is a row, and 0 is a value", () => {
  const view = t.viewFor("package_sealing", { warehouse_imports_count: 0 });
  assert.deepEqual(view.fields, [{ label: "Số kiện bên trong", value: "0" }]);
});

test("the browser copy rejects an array as `data` too, not only the CommonJS one", () => {
  const { execFileSync } = require("node:child_process");
  const script = `
    const fs = require("node:fs");
    const vm = require("node:vm");
    const source = fs.readFileSync(${JSON.stringify(
      require("node:path").join(ROOT, "static", "js", "tracking-search.js"),
    )}, "utf8");
    const sandbox = {};
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const api = sandbox.VipOrderTrackingSearch;
    if (!api) { throw new Error("did not attach VipOrderTrackingSearch"); }
    if (api.interpret("warehouse_import", 200, { result: "found", data: [] }).state !== "PROVIDER_ERROR") {
      throw new Error("browser copy accepted an array as data");
    }
    process.stdout.write("ok");
  `;
  assert.equal(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }), "ok");
});
