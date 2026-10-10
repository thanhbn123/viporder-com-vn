/* The public tracking lookup's decisions that do not need a page (gate G04B).
 *
 * WHY THIS FILE EXISTS. `tracking.js` is a DOM controller and cannot be executed
 * by `node --test`, so anything left inside it can only be READ, never RUN — and
 * this project has already paid for that lesson twice (register-validation.js,
 * register-errors.js: both were extracted for exactly this reason).
 *
 * Three of the decisions below are the ones worth executing:
 *
 *   checkKeyword   is a SECURITY BOUNDARY. It decides whether the customer's
 *                  typing is allowed to become part of a request at all, and it
 *                  is the only place that rejects input without asking anybody.
 *   keywordPath    builds the URL. The keyword is ALWAYS passed through
 *                  encodeURIComponent, so no input can add a path segment, a
 *                  query parameter or a traversal to the request.
 *   interpret      turns an HTTP status plus a body into the ONE state the UI
 *                  shows. This is where "200 but the body is not the documented
 *                  shape" and "404 that is really a missing route" are separated
 *                  from a genuine answer — getting those wrong tells a customer
 *                  "no such parcel" when the truth is "the lookup is broken".
 *
 * THE KEYWORD IS NEVER RETURNED TO ANALYTICS. This module does not talk to
 * `analytics.js` and carries no reference to it; the search event is fired by the
 * controller with `search_type` and `result` only (see static/js/analytics.js).
 *
 * Dependency-free; loads as a browser script and as a CommonJS export, so there
 * is exactly ONE copy of each rule.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  /* Node: the test runner loads this file directly. */
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  /* Browser: a plain deferred script, like the rest of static/js. */
  if (root) {
    root.VipOrderTrackingSearch = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* The two modes, spelled EXACTLY as the provider contract spells them. These
   * strings go into the URL and into the analytics `search_type`, so a typo here
   * is a 404 and a wrong dashboard bucket, not a visible error. */
  var MODES = {
    WAREHOUSE: "warehouse_import",
    PACKAGE: "package_sealing"
  };

  /* The documented paths. The keyword is appended by `keywordPath` and by nothing
   * else, so there is one place where a URL is built. */
  var PATHS = {
    warehouse_import: "/api/tracking/warehouse-imports/",
    package_sealing: "/api/tracking/package-sealings/"
  };

  /* Every state the section can be in. The list is closed on purpose: the UI
   * renders one of these and nothing else, which is what makes "all states are
   * rendered in the UI" a property rather than a hope. */
  var STATE = {
    IDLE: "IDLE",
    LOADING: "LOADING",
    FOUND: "FOUND",
    NOT_FOUND: "NOT_FOUND",
    INVALID: "INVALID",
    PROVIDER_ERROR: "PROVIDER_ERROR"
  };

  /* Longest keyword we will put in a path. The provider's own codes are far
   * shorter than this; the cap exists so a paste of a whole paragraph cannot
   * become a request. */
  var MAX_KEYWORD_LENGTH = 64;

  /* ONLY these characters may reach a URL: ASCII letters, digits and - _ | .
   *
   * Deliberately ASCII-only. This is a security boundary — it decides what is
   * allowed to become part of a request path — and carrier tracking codes (UPU
   * S10, express-carrier and warehouse codes) are ASCII. Every character that
   * could change the MEANING of the URL rather than be data in it (space, /, ?,
   * #, &, =, %, ", ', <, >, \) is outside this set, so the reject happens before
   * any request exists. If a real provider is ever found to emit a code outside
   * this set, widen it HERE, in one place, with the evidence.
   *
   * The `|` is in the set because the provider uses it as a separator inside its
   * own codes; it is the one character here that `encodeURIComponent` actually
   * rewrites (%7C). */
  var KEYWORD_ALLOWED = /^[A-Za-z0-9._|-]+$/;

  function own(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  /* ------------------------------------------------------------------ keyword */

  function normaliseMode(mode) {
    return mode === MODES.PACKAGE ? MODES.PACKAGE : MODES.WAREHOUSE;
  }

  /* Trim, then decide. Returns `{ok:true, keyword}` or
   * `{ok:false, reason, message}` — the reason is for tests and logs, the message
   * is what the customer reads. */
  function checkKeyword(raw) {
    /* String() rather than a truthiness test: `0` and `false` are not keywords,
     * but they must not become the string "undefined" either. */
    var keyword = String(raw == null ? "" : raw).trim();
    if (!keyword) {
      return {
        ok: false,
        reason: "EMPTY",
        message: "Vui lòng nhập mã vận đơn hoặc mã kiện."
      };
    }
    if (keyword.length > MAX_KEYWORD_LENGTH) {
      /* Rejected, not truncated. Truncating would send the provider a DIFFERENT
       * code from the one the customer typed and then answer about it with a
       * straight face. The input carries maxlength=64, so this only happens to a
       * paste or a scripted caller. */
      return {
        ok: false,
        reason: "TOO_LONG",
        message: "Mã tra cứu quá dài (tối đa " + MAX_KEYWORD_LENGTH + " ký tự)."
      };
    }
    if (!KEYWORD_ALLOWED.test(keyword)) {
      return {
        ok: false,
        reason: "CHARSET",
        message: "Mã tra cứu chỉ được chứa chữ, số và các ký tự - _ | ."
      };
    }
    return { ok: true, keyword: keyword };
  }

  /* The ONE place a tracking URL is built.
   *
   * encodeURIComponent is not decoration: the allowed set above already excludes
   * the dangerous characters, so this is the second lock on the same door — and
   * it is the one that keeps the guarantee if the set above is ever widened.
   * `|` is the character it actually changes today. */
  function keywordPath(mode, keyword) {
    return PATHS[normaliseMode(mode)] + encodeURIComponent(String(keyword == null ? "" : keyword));
  }

  /* --------------------------------------------------------------- response */

  function errorCodeOf(body) {
    if (!body || typeof body !== "object") {
      return null;
    }
    var error = body.error;
    if (!error || typeof error !== "object") {
      return null;
    }
    return typeof error.code === "string" && error.code ? error.code : null;
  }

  function errorMessageOf(body) {
    if (!body || typeof body !== "object") {
      return null;
    }
    var error = body.error;
    if (!error || typeof error !== "object") {
      return null;
    }
    return typeof error.message === "string" ? error.message : null;
  }

  /* The backend's GENERIC 404 body. MEASURED, not guessed — against the real
   * service on this branch:
   *
   *   $ curl -s .../api/tracking/warehouse-imports/KIEMTRA0001
   *   {"error":{"code":"NOT_FOUND","message":"Not found."}}
   *
   * (backend/app/errors.py maps `404: NOT_FOUND` with the message "Not found.")
   *
   * WHY THIS GUARD EXISTS. That body is SHAPE-IDENTICAL to a genuine tracking
   * NOT_FOUND, and it is also what an UNIMPLEMENTED route answers. So while the
   * tracking router is being built in a parallel workstream, every lookup would be
   * reported as "không tìm thấy mã này" — a confident lie about a real parcel, and
   * the customer would go to support with the wrong question.
   *
   * The contract requires this message to be Vietnamese, so the English generic is
   * a CONTRACT VIOLATION, and the safe reading of a contract violation is "we do
   * not know" rather than "the parcel does not exist". The direction of the mistake
   * is what decides it: a needless retry costs a minute, a false denial costs the
   * customer's trust.
   *
   * TODO(g04b): delete this guard once GET /api/tracking/* is merged and answers
   * with its own Vietnamese sentence. The tracking router never emits this exact
   * string, so the guard is inert afterwards — it is only here for the window
   * between the two branches landing.
   *
   * It is written as an exact match on the ONE string this backend emits, not as a
   * language detector: clever heuristics that fail OPEN are how a filter becomes
   * decoration, whereas this one either matches the measurement or does nothing. */

  /* The response's `search_type` wins when it names one of the two known modes:
   * the server is the authority on what it found, and the two endpoints agree by
   * contract, so a disagreement is a mis-route worth rendering correctly rather
   * than papering over. Anything else falls back to the mode the customer chose. */
  function resolveMode(reported, requested) {
    return reported === MODES.WAREHOUSE || reported === MODES.PACKAGE
      ? reported
      : normaliseMode(requested);
  }

  /* HTTP status + body -> exactly one state.
   *
   * The judgement calls, all of which exist to keep the UI HONEST:
   *
   *   1. A 200 that is not `{"result":"found","data":{…}}` is a PROVIDER_ERROR,
   *      never a "not found". A broken response is not evidence that the parcel
   *      does not exist.
   *   2. A 404 or 400 whose body is NOT this contract's error envelope (an nginx
   *      error page, a proxy that answered instead of the application) is a
   *      PROVIDER_ERROR.
   *   3. A 404 whose code is NOT_FOUND is a not-found. An earlier revision also
   *      treated the backend's generic `"Not found."` body as a provider error,
   *      because while the tracking router was unmerged an unimplemented route
   *      answered exactly that and the client could not tell it from a genuine
   *      NOT_FOUND. Verified by measurement after the merge: the route answers
   *      404/400/503 with its own Vietnamese messages and never that string, so
   *      the guard was deleted rather than left as decoration.
   *
   * The server's own `error.message` is deliberately NOT carried out of here: the
   * client cannot tell a curated Vietnamese sentence from a relayed provider
   * string, so it renders its own copy, chosen by CODE. That is the whole point of
   * "never show a raw provider message". (The one place a message is READ is rule
   * 3, and it is read only to decide that we do not know — never to display it.) */
  function interpret(requestedMode, status, body) {
    var reported = body && typeof body === "object" ? body.search_type : null;
    var mode = resolveMode(reported, requestedMode);
    var code = errorCodeOf(body);

    if (status === 200) {
      /* `Array.isArray` is excluded explicitly: `typeof [] === "object"`, so an
       * array would otherwise be accepted as `data` and then rendered as a record
       * with no fields — a "found" answer to a question that was not asked. */
      if (
        body && body.result === "found" && body.data &&
        typeof body.data === "object" && !Array.isArray(body.data)
      ) {
        return { state: STATE.FOUND, mode: mode, code: null, data: body.data };
      }
      return { state: STATE.PROVIDER_ERROR, mode: mode, code: "MALFORMED_RESPONSE", data: null };
    }

    if (status === 404 && code === "NOT_FOUND") {
      return { state: STATE.NOT_FOUND, mode: mode, code: code, data: null };
    }

    if (status === 400 && code) {
      return { state: STATE.INVALID, mode: mode, code: code, data: null };
    }

    return {
      state: STATE.PROVIDER_ERROR,
      mode: mode,
      /* Named, not swallowed: an unexpected status is worth seeing in a log, and
       * "UNEXPECTED_RESPONSE" is what tells a reader the shape was wrong rather
       * than the provider being down. */
      code: code || (status === 0 ? "NETWORK_ERROR" : "HTTP_" + status),
      data: null
    };
  }

  /* The analytics `result`, or null when the state is not an outcome yet.
   *
   * INVALID is an `error` and not its own bucket: the customer's search did not
   * produce an answer, and the contract names three results, not four. */
  function resultForState(state) {
    if (state === STATE.FOUND) {
      return "found";
    }
    if (state === STATE.NOT_FOUND) {
      return "not_found";
    }
    if (state === STATE.INVALID || state === STATE.PROVIDER_ERROR) {
      return "error";
    }
    return null;
  }

  /* --------------------------------------------------------------- rendering */

  /* The customer-facing copy, one entry per state. The keyword never appears in
   * any of these — an error message is not a place to echo back what somebody
   * typed, and it would end up in a screenshot or a support ticket.
   *
   * MESSAGES.IDLE is duplicated in index.html so the section reads correctly with
   * scripts disabled; tools/js/tracking-search.test.js asserts the two agree, so
   * the duplicate cannot drift. */
  var MESSAGES = {
    IDLE: "Nhập mã vận đơn hoặc mã kiện rồi bấm Tra cứu.",
    LOADING: "Đang tra cứu, vui lòng chờ…",
    NOT_FOUND:
      "Không tìm thấy thông tin cho mã này. Vui lòng kiểm tra lại mã, " +
      "hoặc liên hệ VIPORDER để được hỗ trợ.",
    PROVIDER_ERROR:
      "Hệ thống tra cứu đang tạm bận hoặc không phản hồi. " +
      "Vui lòng thử lại sau ít phút.",
    INVALID: "Mã tra cứu không hợp lệ."
  };

  /* Is this value worth a row?
   *
   * `null` and `undefined` are "the provider does not know", and an empty string
   * is the same answer written differently. `0` and `false` are VALUES and are
   * kept — a package quantity of 0 is information, not an absence. */
  function present(value) {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }

  function valueOf(value) {
    return String(value);
  }

  /* WAREHOUSE mode — a WHITELIST, in the order the customer reads it.
   *
   * Nothing is rendered by iterating the response object. The response also
   * carries `kind`, `is_pending`, `is_completed`, `package_number`,
   * `status_background_color` and `status_text_color`; they are deliberately not
   * rendered, and the reason is not squeamishness:
   *
   *   * `kind` / `is_pending` / `is_completed` are control fields whose meaning
   *     the UI already knows — it chose the mode and it renders the status.
   *   * the two colour fields would have to be applied as inline CSS, which makes
   *     a third-party string a style sink for a decoration the customer did not
   *     ask for. The state colours are ours and live in the stylesheet.
   *
   * `package_number` is in the contract but not in the agreed render list, and
   * inventing a label for it is how a UI ends up printing a warehouse's internal
   * numbering as if it meant something to a customer.
   *
   * `product: true`, `dimensions: true` and `status: true` are the three rows built
   * from something other than one plain field, so that the ORDER stays readable
   * here instead of being encoded as an index into a filtered array. The status row
   * sits INSIDE this layout because its position differs between the two modes
   * (last for a warehouse import, before the date for a package) and a row appended
   * at the end would be in the wrong place in one of them. */
  /* `highlight: true` marks the codes a customer reads out on the phone (owner,
   * 2026-10-10): customer, delivery customer, delivery code and bag code.
   * "Mã vận đơn Việt Nam" and "VNPost" are NOT shown (owner, 2026-10-10): the
   * API still returns them, the page simply does not render them. */
  var WAREHOUSE_LAYOUT = [
    { label: "Mã khách", key: "customer_code", highlight: true },
    { label: "Khách giao hàng", key: "delivery_customer", highlight: true },
    { label: "Mã giao hàng", key: "delivery_code", highlight: true },
    { label: "Ngày nhập", key: "date" },
    { label: "Cân nặng", key: "weight" },
    { label: "Mã vận đơn Trung Quốc", key: "china_tracking_code" },
    { label: "Mã bao", key: "package_sealing_code", highlight: true },
    { label: "Tên hàng", product: true },
    { label: "Số lượng", key: "product_quantity" },
    { label: "Số kiện", key: "package_quantity" },
    { label: "Trạng thái hiện tại", status: true }
  ];

  var PACKAGE_LAYOUT = [
    { label: "Mã bao", key: "code", highlight: true },
    { label: "Mã khách", key: "customer_code", highlight: true },
    { label: "Cân nặng thực", key: "actual_weight" },
    { label: "Kích thước (D × R × C)", dimensions: true },
    { label: "Thể tích", key: "volume" },
    { label: "Trạng thái", status: true },
    { label: "Ngày tạo", key: "created_at" },
    { label: "Số kiện bên trong", key: "warehouse_imports_count" }
  ];

  var CHILD_LAYOUT = [
    { label: "Mã vận đơn Trung Quốc", key: "china_tracking_code" },
    { label: "Mã bao", key: "package_sealing_code", highlight: true },
    { label: "Cân nặng", key: "weight" },
    { label: "Ngày tạo", key: "created_at" }
  ];

  /* Tên hàng: the Vietnamese name is the one a customer reads, so it leads. The
   * Chinese name is shown ALONGSIDE when the provider sent it — that is what the
   * warehouse staff and the seller both quote, and dropping it would cost the
   * customer the only string they can match against their own order. */
  function productName(source) {
    var vi = present(source.product_name_vi) ? valueOf(source.product_name_vi) : "";
    var cn = present(source.product_name_cn) ? valueOf(source.product_name_cn) : "";
    /* The Chinese half is wrapped only when there is a Vietnamese half to read
     * first; on its own it is the whole answer. */
    if (vi && cn) {
      return vi + " (" + cn + ")";
    }
    return vi || cn;
  }

  /* Kích thước is shown ONLY when all three dimensions are present. A partial
   * "12 × × 8" reads as a fact and is not one. */
  function dimensions(source) {
    if (present(source.length) && present(source.width) && present(source.height)) {
      return valueOf(source.length) + " × " + valueOf(source.width) + " × " + valueOf(source.height);
    }
    return "";
  }

  function rowsFrom(source, layout) {
    var rows = [];
    for (var i = 0; i < layout.length; i += 1) {
      var entry = layout[i];
      var value;
      if (entry.product) {
        value = productName(source);
      } else if (entry.dimensions) {
        value = dimensions(source);
      } else if (entry.status) {
        value = present(source.status) ? valueOf(source.status) : "";
      } else if (own(source, entry.key)) {
        value = present(source[entry.key]) ? valueOf(source[entry.key]) : "";
      } else {
        value = "";
      }
      /* Absent fields are SKIPPED ENTIRELY — no row, no "null", no dash. A dash
       * would be a value the provider never sent. */
      if (value !== "") {
        rows.push(entry.highlight
          ? { label: entry.label, value: value, highlight: true }
          : { label: entry.label, value: value });
      }
    }
    return rows;
  }

  /* Status logs. Text only, in the provider's own words: the status string is
   * bilingual ("Đóng bao 打包") and is never translated or stripped — the Chinese
   * half is what the warehouse in China reads, and the customer needs both halves
   * to match what they are told on the phone. */
  function logsOf(data) {
    var source = data && data.status_logs;
    var logs = [];
    if (!source || typeof source.length !== "number") {
      return logs;
    }
    for (var i = 0; i < source.length; i += 1) {
      var entry = source[i];
      if (!entry || typeof entry !== "object") {
        continue;
      }
      var log = {
        status: present(entry.status) ? valueOf(entry.status) : "",
        note: present(entry.note) ? valueOf(entry.note) : "",
        created_at: present(entry.created_at) ? valueOf(entry.created_at) : ""
      };
      /* An entry with nothing in it is not a status change; skip it rather than
       * render an empty bullet. */
      if (log.status || log.note || log.created_at) {
        logs.push(log);
      }
    }
    return logs;
  }

  function childrenOf(data) {
    var source = data && data.warehouse_imports;
    var children = [];
    if (!source || typeof source.length !== "number") {
      return children;
    }
    for (var i = 0; i < source.length; i += 1) {
      var entry = source[i];
      if (!entry || typeof entry !== "object") {
        continue;
      }
      var rows = rowsFrom(entry, CHILD_LAYOUT);
      if (rows.length) {
        children.push({ fields: rows });
      }
    }
    return children;
  }

  /* Everything the renderer needs, as data, with absent fields already gone. The
   * renderer's remaining job is createElement + textContent only, which is the
   * property that keeps third-party text out of the HTML parser. */
  function viewFor(mode, data) {
    var resolved = normaliseMode(mode);
    var source = data && typeof data === "object" ? data : {};
    var pack = resolved === MODES.PACKAGE;

    return {
      mode: resolved,
      fields: rowsFrom(source, pack ? PACKAGE_LAYOUT : WAREHOUSE_LAYOUT),
      statusLabel: pack ? "Trạng thái" : "Trạng thái hiện tại",
      status: present(source.status) ? valueOf(source.status) : "",
      logsLabel: "Lịch sử trạng thái",
      logs: logsOf(source),
      childrenLabel: "Kiện hàng bên trong",
      children: childrenOf(source)
    };
  }

  return {
    MODES: MODES,
    PATHS: PATHS,
    STATE: STATE,
    MESSAGES: MESSAGES,
    MAX_KEYWORD_LENGTH: MAX_KEYWORD_LENGTH,
    KEYWORD_ALLOWED: KEYWORD_ALLOWED,
    own: own,
    normaliseMode: normaliseMode,
    checkKeyword: checkKeyword,
    keywordPath: keywordPath,
    errorCodeOf: errorCodeOf,
    errorMessageOf: errorMessageOf,
    resolveMode: resolveMode,
    interpret: interpret,
    resultForState: resultForState,
    present: present,
    viewFor: viewFor
  };
});
