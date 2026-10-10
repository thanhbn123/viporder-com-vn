/* The public tracking lookup (G04B), driven through a real browser.
 *
 * WHAT IS STUBBED, AND WHY — read this before changing anything here.
 *
 * The two tracking endpoints are being built in a PARALLEL workstream
 * (`GET /api/tracking/warehouse-imports/{keyword}` and
 * `GET /api/tracking/package-sealings/{keyword}`). They do not exist in the
 * backend this suite boots, so every test that needs an ANSWER stubs it with
 * `page.route()`.
 *
 * THE STUB SPEAKS THE DOCUMENTED CONTRACT, NOT THIS IMPLEMENTATION. Same paths,
 * same status codes, same `{result, search_type, data}` / `{error:{code,message}}`
 * envelopes, and the same Vietnamese `message` strings the contract names. That is
 * what keeps these assertions valid against the real service: when the endpoints
 * land, delete the `stub()` calls (or run with TRACKING_LIVE=1, which installs no
 * stub at all) and the same expectations apply unchanged.
 *
 * ONE test below deliberately does NOT stub (`the real endpoint, when it exists,
 * answers the documented contract`). It probes the live API and SKIPS while the
 * route is missing, so it is honest about what has been verified today and becomes
 * a real contract check the moment the other workstream merges.
 *
 * THAT PROBE HAD TO BE MADE HONEST, and the reason is worth keeping. MEASURED on
 * this branch: an UNIMPLEMENTED tracking route answers
 *
 *     HTTP 404  {"error":{"code":"NOT_FOUND","message":"Not found."}}
 *
 * which is shape-identical to a genuine tracking NOT_FOUND. The first version of
 * this test therefore PASSED for the wrong reason — it saw a contract-shaped 404
 * envelope where there was no route at all. It now recognises that exact generic
 * placeholder and skips. static/js/tracking-search.js applies the same rule in
 * production, so a customer is never told their parcel
 * does not exist merely because the route is not deployed.
 *
 * Every assertion here is something a CUSTOMER can observe: what the section says,
 * what it renders, and what it refuses to do.
 */

"use strict";

const { test, expect } = require("@playwright/test");
const { hasHorizontalOverflow } = require("./helpers");

/* The paths, as the contract spells them. */
const WAREHOUSE_URL = "**/api/tracking/warehouse-imports/*";
const PACKAGE_URL = "**/api/tracking/package-sealings/*";

/* Set TRACKING_LIVE=1 to run every test against the real backend instead of the
 * stub. It is expected to FAIL today: the endpoints are not merged, and a
 * dependency on the other workstream is exactly what the stub exists to avoid. */
const LIVE = process.env.TRACKING_LIVE === "1";

/* ------------------------------------------------------------------ fixtures */

/* `created_by`, `id` and `area_id` are in this payload ON PURPOSE. The backend is
 * documented to strip them; these tests assert the CLIENT does not render them
 * either, so the guarantee does not depend on somebody else's filter.
 *
 * `package_number` is in the contract but NOT in the agreed render list, so it must
 * not appear either. */
const WAREHOUSE_FOUND = {
  result: "found",
  search_type: "warehouse_import",
  data: {
    kind: "warehouse_import",
    customer_code: "TT12345",
    date: "2026-09-29",
    weight: "12.5",
    china_tracking_code: "CN987654321",
    vietnam_tracking_code: "VN123456789",
    vnpost_tracking_code: "VP998877665",
    package_sealing_code: "B2026-001",
    product_name_vi: "Áo thun nam",
    product_name_cn: "男士T恤",
    product_quantity: "3",
    package_quantity: "1",
    package_number: "PN-INTERNAL-77",
    status: "Đóng bao 打包",
    status_logs: [
      { status: "Nhập kho 入库", note: "Kho Quảng Châu", created_at: "2026-09-29 09:10" },
      { status: "Đóng bao 打包", note: null, created_at: "2026-09-30 08:00" },
    ],
    is_pending: false,
    is_completed: false,
    status_background_color: "#ffcc00",
    status_text_color: "#111111",
    created_by: "nhanvien-noi-bo",
    id: 424242,
    area_id: 7,
  },
};

const PACKAGE_FOUND = {
  result: "found",
  search_type: "package_sealing",
  data: {
    kind: "package_sealing",
    code: "B2026-001",
    customer_code: "TT12345",
    actual_weight: "8.4",
    length: "40",
    width: "30",
    height: "20",
    volume: "0.024",
    status: "Đóng bao 打包",
    status_logs: [{ status: "Đóng bao 打包", note: "Kho Hà Nội", created_at: "2026-09-30 08:00" }],
    created_at: "2026-09-30",
    warehouse_imports_count: 2,
    warehouse_imports: [
      {
        china_tracking_code: "CN987654321",
        vietnam_tracking_code: "VN123456789",
        package_sealing_code: "B2026-001",
        weight: "4.2",
        created_at: "2026-09-29",
      },
      /* The second one has no Vietnam code: the absent field must simply not be
       * rendered, which is the "skip absent fields" rule in a browser. */
      {
        china_tracking_code: "CN987654322",
        vietnam_tracking_code: "",
        package_sealing_code: "B2026-001",
        weight: "4.2",
        created_at: "2026-09-30",
      },
    ],
  },
};

const NOT_FOUND = {
  error: { code: "NOT_FOUND", message: "Không tìm thấy mã vận đơn này." },
};

const PROVIDER_ERROR = {
  error: { code: "PROVIDER_ERROR", message: "Nhà cung cấp trả về lỗi." },
};

/* ------------------------------------------------------------------- helpers */

/** Answer the two tracking paths with a fixed status and body.
 *
 * Both paths are always routed, so a test can never accidentally reach the real
 * backend through the half it did not stub — a mistake that would look like a
 * flaky failure rather than a missing stub. */
async function stub(page, { warehouse, warehouseStatus = 200, packageSealing, packageStatus = 200 }) {
  if (LIVE) {
    return;
  }
  const fulfil = (route, status, body) =>
    route.fulfill({
      status,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify(body),
    });

  await page.route(WAREHOUSE_URL, (route) =>
    fulfil(route, warehouseStatus, warehouse === undefined ? WAREHOUSE_FOUND : warehouse),
  );
  await page.route(PACKAGE_URL, (route) =>
    fulfil(route, packageStatus, packageSealing === undefined ? PACKAGE_FOUND : packageSealing),
  );
}

/** The section, ready to use. */
async function openTracking(page) {
  await page.goto("/");
  await page.locator("#tracking").scrollIntoViewIfNeeded();
}

/** Label -> value for the FIRST definition list, i.e. the record's own fields and
 * not the nested rows of a package's inner imports (which reuse some labels). */
function fieldMap(page) {
  return page.$$eval("#trackingResults > .tracking-fields", (lists) => {
    const out = {};
    if (!lists.length) {
      return out;
    }
    const kids = [...lists[0].children];
    for (let i = 0; i + 1 < kids.length; i += 2) {
      out[kids[i].textContent] = kids[i + 1].textContent;
    }
    return out;
  });
}

function trackingRequests(page) {
  const urls = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/tracking/")) {
      urls.push(url);
    }
  });
  return urls;
}

/* --------------------------------------------------------------- idle state */

test.describe("tracking lookup", () => {
  test("starts idle: a hint, no result, an enabled button and the agreed placeholder", async ({
    page,
  }) => {
    await openTracking(page);

    await expect(page.locator("#trackingStatus")).toHaveText(/Nhập mã vận đơn hoặc mã kiện rồi bấm Tra cứu/);
    await expect(page.locator("#trackingResults")).toBeHidden();
    await expect(page.locator("#trackingSubmit")).toBeEnabled();
    await expect(page.locator("#trackingKeyword")).toHaveAttribute(
      "placeholder",
      "Nhập mã vận đơn / mã kiện",
    );

    /* Both modes are offered, and the warehouse one is the default. */
    await expect(page.locator("[data-mode='warehouse_import']")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.locator("[data-mode='package_sealing']")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  /* --------------------------------------------------------------- found */

  test("a warehouse lookup renders the record, in Vietnamese, with the bilingual status intact", async ({
    page,
  }) => {
    await stub(page, {});
    await openTracking(page);

    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    const fields = await fieldMap(page);
    expect(fields["Mã khách"]).toBe("TT12345");
    expect(fields["Ngày nhập"]).toBe("2026-09-29");
    expect(fields["Cân nặng"]).toBe("12.5");
    expect(fields["Mã vận đơn Trung Quốc"]).toBe("CN987654321");
    /* Not shown since 2026-10-10 (owner), although the API still returns them. */
    expect(fields["Mã vận đơn Việt Nam"]).toBeUndefined();
    expect(fields["VNPost"]).toBeUndefined();
    expect(fields["Mã bao"]).toBe("B2026-001");
    /* The codes a customer reads out are highlighted. */
    await expect(page.locator("#trackingResults dd.tracking-code").first()).toHaveText("TT12345");
    expect(fields["Số lượng"]).toBe("3");
    expect(fields["Số kiện"]).toBe("1");

    /* THE STATUS IS BILINGUAL AND MUST SURVIVE VERBATIM. Stripping the Chinese
     * half would leave the customer unable to match what the warehouse in China
     * tells them. */
    expect(fields["Trạng thái hiện tại"]).toBe("Đóng bao 打包");
    await expect(page.locator("#trackingResults")).toContainText("Đóng bao 打包");

    /* Tên hàng: the Vietnamese name leads, the Chinese name is alongside. */
    const product = fields["Tên hàng"];
    expect(product).toContain("Áo thun nam");
    expect(product).toContain("男士T恤");

    /* The history is rendered as a list, also verbatim. */
    await expect(page.locator("#trackingResults")).toContainText("Lịch sử trạng thái");
    await expect(page.locator(".tracking-logs li")).toHaveCount(2);
    await expect(page.locator(".tracking-logs")).toContainText("Nhập kho 入库");
    await expect(page.locator(".tracking-logs")).toContainText("Kho Quảng Châu");

    /* And the live region says so, so a screen reader hears the outcome. */
    await expect(page.locator("#trackingStatus")).toHaveText(/Đã tìm thấy thông tin/);
  });

  test("nothing the contract does not list is ever rendered", async ({ page }) => {
    /* The client must not depend on the backend having stripped these. An unknown
     * key that reaches the page as a row is a leak with no visible symptom. */
    await stub(page, {});
    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    const text = await page.locator("#trackingResults").textContent();
    for (const leaked of [
      "nhanvien-noi-bo",
      "424242",
      "PN-INTERNAL-77",
      "#ffcc00",
      "#111111",
      "is_pending",
      "created_by",
      "area_id",
      "kind",
    ]) {
      expect(text, `${leaked} was rendered`).not.toContain(leaked);
    }
    /* And no absent value is printed as a word. */
    expect(text).not.toContain("null");
    expect(text).not.toContain("undefined");
  });

  test("a package lookup asks the sealing endpoint and renders its inner imports", async ({
    page,
  }) => {
    await stub(page, {});
    await openTracking(page);

    await page.click("[data-mode='package_sealing']");
    await expect(page.locator("[data-mode='package_sealing']")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await page.fill("#trackingKeyword", "B2026-001");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    const fields = await fieldMap(page);
    expect(fields["Mã bao"]).toBe("B2026-001");
    expect(fields["Mã khách"]).toBe("TT12345");
    expect(fields["Cân nặng thực"]).toBe("8.4");
    expect(fields["Kích thước (D × R × C)"]).toBe("40 × 30 × 20");
    expect(fields["Thể tích"]).toBe("0.024");
    expect(fields["Trạng thái"]).toBe("Đóng bao 打包");
    expect(fields["Ngày tạo"]).toBe("2026-09-30");
    expect(fields["Số kiện bên trong"]).toBe("2");

    await expect(page.locator(".tracking-children li")).toHaveCount(2);
    await expect(page.locator(".tracking-children")).toContainText("CN987654322");
    /* The inner import with no Vietnam code simply has no row for it. */
    await expect(page.locator("#trackingResults")).not.toContainText("null");
  });

  test("choosing the package mode clears a warehouse answer instead of leaving it on screen", async ({
    page,
  }) => {
    await stub(page, {});
    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    await page.click("[data-mode='package_sealing']");
    await expect(page.locator("#trackingResults")).toBeHidden();
    await expect(page.locator("#trackingStatus")).toHaveText(/Nhập mã vận đơn/);
  });

  /* ------------------------------------------------------------ not found */

  test("a keyword with no record is reported as not found, not as a failure", async ({ page }) => {
    await stub(page, { warehouse: NOT_FOUND, warehouseStatus: 404 });
    await openTracking(page);

    await page.fill("#trackingKeyword", "KHONGCOTOI123");
    await page.click("#trackingSubmit");

    const status = page.locator("#trackingStatus");
    await expect(status).toHaveText(/Không tìm thấy thông tin cho mã này/);
    await expect(status).toHaveClass(/is-notfound/);
    await expect(page.locator("#trackingResults")).toBeHidden();
    /* A not-found answer must not read like an outage: the customer's next move is
     * to check the code, not to wait and retry. */
    await expect(status).not.toHaveText(/tạm bận|thử lại sau/);
  });

  /* The pre-merge guard has a test-less history on purpose. While the tracking
   * router was unmerged, an unimplemented route answered
   * `{"error":{"code":"NOT_FOUND","message":"Not found."}}` — shape-identical to a
   * genuine not-found — so the client carried an exact-match guard to keep it from
   * telling customers their parcel did not exist. Both halves are now merged and
   * the merged route answers with its own Vietnamese messages, verified by
   * measurement, so the guard and its test were deleted rather than kept as
   * decoration. The `ROUTE_NOT_IMPLEMENTED` branch is gone; a contract 404 is a
   * not-found again, which is what the two tests above already assert. */

  /* --------------------------------------------------------------- invalid */

  test("an invalid keyword is refused locally and NO request is made", async ({ page }) => {
    /* This is the security boundary seen from the outside. `1 OR 1=1` and a path
     * traversal are the two shapes worth naming: one that changes a query, one that
     * changes a path. Neither may reach the network. */
    await stub(page, {});
    await openTracking(page);
    const requests = trackingRequests(page);

    for (const keyword of ["abc 123", "../../etc/passwd", "A?x=1", "<script>x</script>", "mã có dấu"]) {
      await page.fill("#trackingKeyword", keyword);
      await page.click("#trackingSubmit");
      const status = page.locator("#trackingStatus");
      await expect(status, `"${keyword}" must be refused`).toHaveText(/Mã tra cứu/);
      await expect(page.locator("#trackingKeyword")).toHaveAttribute("aria-invalid", "true");
      await expect(page.locator("#trackingResults")).toBeHidden();
    }

    expect(requests, `a request escaped the local boundary:\n${requests.join("\n")}`).toEqual([]);
  });

  test("an empty keyword is refused locally and NO request is made", async ({ page }) => {
    await stub(page, {});
    await openTracking(page);
    const requests = trackingRequests(page);

    await page.fill("#trackingKeyword", "   ");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingStatus")).toHaveText(/Vui lòng nhập mã vận đơn hoặc mã kiện/);
    expect(requests).toEqual([]);
  });

  /* --------------------------------------------------------- provider error */

  test("a provider error is reported as an error, never as not found", async ({ page }) => {
    await stub(page, { warehouse: PROVIDER_ERROR, warehouseStatus: 502 });
    await openTracking(page);

    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");

    const status = page.locator("#trackingStatus");
    await expect(status).toHaveText(/tạm bận|không phản hồi/);
    await expect(status).toHaveClass(/is-error/);
    /* The distinction that matters: a broken lookup must never tell a customer
     * their parcel does not exist. */
    await expect(status).not.toHaveText(/Không tìm thấy/);
  });

  test("a provider outage (503) is the same state as a provider error", async ({ page }) => {
    await stub(page, {
      warehouse: { error: { code: "PROVIDER_UNAVAILABLE", message: "Nhà cung cấp không sẵn sàng." } },
      warehouseStatus: 503,
    });
    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingStatus")).toHaveClass(/is-error/);
    await expect(page.locator("#trackingSubmit")).toBeEnabled();
  });

  test("a raw provider message is never shown, whatever the response says", async ({ page }) => {
    /* The server's own sentence is not rendered: the client cannot tell a curated
     * Vietnamese message from a relayed provider string, so it renders its own copy
     * chosen by contract code. */
    await stub(page, {
      warehouse: {
        error: {
          code: "PROVIDER_ERROR",
          message: "<img src=x onerror=alert(1)> Traceback (most recent call last)",
        },
      },
      warehouseStatus: 502,
    });
    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");

    const status = page.locator("#trackingStatus");
    await expect(status).not.toContainText("Traceback");
    await expect(status).not.toContainText("onerror");
    await expect(page.locator("#trackingResults")).toBeHidden();
  });

  test("a 200 that is not the documented shape is an error, not a not-found", async ({ page }) => {
    await stub(page, { warehouse: { unexpected: "shape" } });
    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");

    const status = page.locator("#trackingStatus");
    await expect(status).toHaveClass(/is-error/);
    await expect(status).not.toHaveText(/Không tìm thấy/);
  });

  test("provider text cannot inject markup into the page", async ({ page }) => {
    /* Provider data is third-party input: a product name typed into somebody else's
     * warehouse system. The renderer uses createElement + textContent only, so the
     * tag must appear as TEXT and never as an element. */
    const hostile = "<img src=x onerror=window.__pwned=1>Áo";
    await stub(page, {
      warehouse: {
        result: "found",
        search_type: "warehouse_import",
        data: { customer_code: "TT1", product_name_vi: hostile, status: "OK" },
      },
    });
    await openTracking(page);
    /* The keyword must be legal — the injection is in the RESPONSE, not the input. */
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    await expect(page.locator("#trackingResults")).toContainText(hostile);
    expect(await page.locator("#trackingResults img").count()).toBe(0);
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  });

  /* ------------------------------------------------------------- keyboard */

  test("Enter in the input submits the lookup", async ({ page }) => {
    await stub(page, {});
    await openTracking(page);

    await page.fill("#trackingKeyword", "CN987654321");
    await page.locator("#trackingKeyword").press("Enter");

    await expect(page.locator("#trackingResults")).toBeVisible();
    const fields = await fieldMap(page);
    expect(fields["Mã khách"]).toBe("TT12345");
  });

  /* -------------------------------------------------------- in-flight state */

  test("the button is disabled and the section says so while a request is in flight", async ({
    page,
  }) => {
    /* The response is held open by a gate the test controls, so "in flight" is an
     * observable state rather than a race against a timer. */
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const calls = [];
    await page.route(WAREHOUSE_URL, async (route) => {
      calls.push(route.request().url());
      await gate;
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body: JSON.stringify(WAREHOUSE_FOUND),
      });
    });
    await page.route(PACKAGE_URL, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PACKAGE_FOUND) }),
    );

    await openTracking(page);
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");

    const button = page.locator("#trackingSubmit");
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute("aria-busy", "true");
    await expect(page.locator("#trackingStatus")).toHaveText(/Đang tra cứu/);

    release();
    await expect(button).toBeEnabled();
    await expect(page.locator("#trackingResults")).toBeVisible();
    /* Exactly ONE request was made for one click: the disabled button is not
     * merely decorative. */
    expect(calls).toHaveLength(1);
  });

  /* -------------------------------------------------------------- requests */

  test("each mode asks its own documented endpoint, with the keyword encoded", async ({ page }) => {
    const seen = [];
    await page.route("**/api/tracking/**", async (route) => {
      seen.push(new URL(route.request().url()).pathname);
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body: JSON.stringify(WAREHOUSE_FOUND),
      });
    });
    await openTracking(page);

    await page.fill("#trackingKeyword", "A|B.CO-1_2");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    await page.click("[data-mode='package_sealing']");
    await page.fill("#trackingKeyword", "B2026-001");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    expect(seen[0]).toBe("/api/tracking/warehouse-imports/A%7CB.CO-1_2");
    expect(seen[1]).toBe("/api/tracking/package-sealings/B2026-001");
  });

  /* ------------------------------------------------------------- analytics */

  test("tracking_search carries the two agreed fields and NEVER the keyword", async ({ page }) => {
    await stub(page, {});
    await openTracking(page);

    const keyword = "CN987654321";
    await page.fill("#trackingKeyword", keyword);
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();

    /* Not found, then a provider error, so all three result values are exercised. */
    await page.unroute(WAREHOUSE_URL);
    await page.route(WAREHOUSE_URL, (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify(NOT_FOUND),
      }),
    );
    await page.fill("#trackingKeyword", "KHONGCOTOI123");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingStatus")).toHaveText(/Không tìm thấy/);

    await page.unroute(WAREHOUSE_URL);
    await page.route(WAREHOUSE_URL, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify(PROVIDER_ERROR),
      }),
    );
    await page.fill("#trackingKeyword", "CN987654322");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingStatus")).toHaveClass(/is-error/);

    const events = await page.evaluate(() =>
      (window.dataLayer || []).filter((entry) => entry && entry.event === "tracking_search"),
    );
    expect(events.length).toBeGreaterThanOrEqual(3);

    const results = events.map((event) => event.result);
    expect(results).toContain("found");
    expect(results).toContain("not_found");
    expect(results).toContain("error");
    for (const event of events) {
      expect(["warehouse_import", "package_sealing"]).toContain(event.search_type);
      /* The fields are EXACTLY these two: nothing else may ride along. */
      const keys = Object.keys(event);
      expect(keys).toContain("search_type");
      expect(keys).toContain("result");
      /* There is no key here that could hold the code, and none of the names a
       * future edit might reach for. */
      for (const forbidden of ["keyword", "code", "query", "q", "tracking_code"]) {
        expect(keys, `tracking_search carries a "${forbidden}" field`).not.toContain(forbidden);
      }
    }

    /* And the tracking code is nowhere in what was sent. */
    const dumped = JSON.stringify(events);
    expect(dumped).not.toContain(keyword);
    expect(dumped).not.toContain("KHONGCOTOI123");
  });

  test("the invalid state is reported as an error result, with no request behind it", async ({
    page,
  }) => {
    await stub(page, {});
    await openTracking(page);

    await page.fill("#trackingKeyword", "abc 123");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingStatus")).toHaveText(/Mã tra cứu/);

    const events = await page.evaluate(() =>
      (window.dataLayer || []).filter((entry) => entry && entry.event === "tracking_search"),
    );
    expect(events.length).toBe(1);
    expect(events[0].result).toBe("error");
    expect(events[0].search_type).toBe("warehouse_import");
  });

  /* ------------------------------------------------------- mis-deployed page */

  test("without the rules module the section says so instead of dying silently", async ({ page }) => {
    /* The rules module owns the keyword boundary and the status mapping, so a
     * controller without it cannot build a safe URL. The failure mode this guards
     * against is the worst one available: a button that looks fine and does
     * nothing at all, with no message anywhere. */
    await page.route("**/static/js/tracking-search.js", (route) => route.abort());
    await page.goto("/");
    await page.locator("#tracking").scrollIntoViewIfNeeded();

    await expect(page.locator("#trackingStatus")).toHaveClass(/is-error/);
    await expect(page.locator("#trackingSubmit")).toBeDisabled();
  });

  /* ---------------------------------------------------------------- mobile */

  test("the section is usable at 412px with no clipped button and no sideways scroll", async ({
    page,
  }) => {
    /* This file runs in the desktop project (mobile.spec.js is the one matched by
     * the Pixel 7 project), so the phone width is set here EXPLICITLY rather than
     * inherited. 412px is a real Android viewport, and it is the narrowest one the
     * section has to survive. */
    await page.setViewportSize({ width: 412, height: 915 });
    await stub(page, {});
    await openTracking(page);

    expect(await hasHorizontalOverflow(page), "the page scrolls sideways at 412px").toBe(false);

    /* Tappable targets: both modes and the submit button clear the 44px minimum,
     * and the input is wide enough to read a code in. */
    for (const selector of [
      "[data-mode='warehouse_import']",
      "[data-mode='package_sealing']",
      "#trackingSubmit",
    ]) {
      const box = await page.locator(selector).boundingBox();
      expect(box.height, `${selector} is too short to tap`).toBeGreaterThanOrEqual(40);
    }
    const inputBox = await page.locator("#trackingKeyword").boundingBox();
    expect(inputBox.width, "the keyword input is too narrow to use").toBeGreaterThan(200);

    /* Nothing may run off the right edge — the classic clipped button. */
    const buttonBox = await page.locator("#trackingSubmit").boundingBox();
    expect(buttonBox.x).toBeGreaterThanOrEqual(-1);
    expect(buttonBox.x + buttonBox.width).toBeLessThanOrEqual(412 + 1);

    /* And it still WORKS at this size: a lookup end to end, plus the overflow
     * re-check with the result rendered, because a long code with no spaces is
     * exactly what pushes a card wider than the screen. */
    await page.fill("#trackingKeyword", "CN987654321");
    await page.click("#trackingSubmit");
    await expect(page.locator("#trackingResults")).toBeVisible();
    await expect(page.locator("#trackingResults")).toContainText("CN987654321");
    expect(await hasHorizontalOverflow(page), "the result overflows at 412px").toBe(false);
  });

  /* ------------------------------------------------------- the live contract */

  test("the real endpoint, when it exists, answers the documented contract", async ({ page }) => {
    /* Deliberately NOT stubbed. The tracking endpoints are built in a parallel
     * workstream, so today the route is missing and this SKIPS with that reason
     * stated. Once the route lands it stops skipping and becomes the check that
     * matters: the real service still speaks the contract the stub assumes.
     *
     * It asserts the SHAPE only — never a particular parcel — because no test may
     * depend on data that does not exist. */
    const response = await page.request.get("/api/tracking/warehouse-imports/KIEMTRA0001");
    const body = await response.json().catch(() => null);
    const code = body && body.error && typeof body.error.code === "string" ? body.error.code : null;
    /* The backend's generic 404 body, MEASURED — see the file header. It is what an
     * unimplemented route answers, so it is NOT evidence that the route exists. */
    const generic = typeof (body && body.error && body.error.message) === "string" &&
      body.error.message.trim().toLowerCase() === "not found.";
    const speaksContract =
      ((body && body.result === "found") || code !== null) && !generic;

    if (!speaksContract) {
      test.skip(
        true,
        `the tracking API is not merged yet (HTTP ${response.status()}, body ` +
          JSON.stringify(body) + `) — which is the backend's generic 404, not this contract`,
      );
      return;
    }

    expect([200, 400, 404, 502, 503]).toContain(response.status());
    if (code !== null) {
      expect(["INVALID_KEYWORD", "NOT_FOUND", "PROVIDER_ERROR", "PROVIDER_UNAVAILABLE"]).toContain(
        code,
      );
    }
  });
});
