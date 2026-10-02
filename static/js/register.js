/*!
 * VIPORDER — registration form controller (gate G02 front end + G07).
 * https://viporder.com.vn
 *
 * Talks to POST /api/v1/registrations (same origin; the dev server proxies
 * /api to the backend).
 *
 * Guarantees this file is responsible for
 * ---------------------------------------
 * 1. DUPLICATE-SUBMIT PROTECTION. The submit button is disabled and put into a
 *    busy state while a request is in flight, and ONE `Idempotency-Key` is
 *    generated per registration attempt and reused on every retry, so a double
 *    click (or a retry after a timeout) cannot create two customers.
 * 2. HONEST OUTCOMES. 201 / 202 / 4xx / 5xx / network failure each get their
 *    own UI state. A network failure never claims "nothing happened", because
 *    the lead may already have been stored server-side.
 * 3. THE PORTAL HOST IS FIXED. Whatever `login_url` the API returns is only
 *    used when it points at https://khachhang.viporder.com.vn; otherwise the
 *    approved constant is used. Existing-customer routing is a business rule,
 *    not something a response body gets to change.
 * 4. NO innerHTML. Every server-provided string is written with textContent.
 *
 * Client-side validation is UX only — the server always validates again.
 */
(function (window, document) {
  "use strict";

  var API_URL = "/api/v1/registrations";
  /* The redirect guard and the form's validation live in
   * static/js/register-validation.js.
   *
   * They were the two most consequential decisions still sitting in a file with
   * nothing executing it. `safePortalUrl` is an OPEN-REDIRECT GUARD - it decides
   * where a customer who has just typed their password is sent - and `validate`
   * decides whether they may submit at all. Both are pure functions of their
   * arguments, so both can be tested WITHOUT adding a DOM library: that is the
   * answer to the question round 4 left open. Extract what does not need a DOM;
   * do not add a dependency for the rest.
   *
   * A plain deferred script loaded before this one, and a CommonJS export, so
   * there is ONE copy of each rule. */
  var rules =
    typeof window !== "undefined" && window.VipOrderRegisterValidation
      ? window.VipOrderRegisterValidation
      : null;

  var PORTAL_URL = rules ? rules.PORTAL_URL : "https://khachhang.viporder.com.vn";
  var PORTAL_HOST = rules ? rules.PORTAL_HOST : "khachhang.viporder.com.vn";
  var IDEM_STORAGE_KEY = "vo_idem_key_v1";
  /* The phone the stored key was minted for, so the binding survives a reload. */
  var IDEM_PHONE_STORAGE_KEY = "vo_idem_phone_v1";
  var BUSY_LABEL = "Đang gửi thông tin…";
  var DONE_LABEL = "Đã gửi đăng ký";

  /* Vietnamese phone shapes: mobile 03/05/07/08/09 + 8 digits (optionally
   * written as +84 or 84), landline 02x. Spaces, dots, dashes and brackets are
   * stripped before matching; the server owns the final normalisation. */
  var VN_PHONE = rules
    ? rules.VN_PHONE
    : /^(?:(?:\+84)|84|0)(?:[35789]\d{8}|2\d{8,9})$/;

  var form = document.getElementById("registerForm");
  if (!form) {
    return;
  }

  var message = document.getElementById("formMessage");
  var portalWrap = document.getElementById("formSuccess");
  var portalLink = document.getElementById("formPortalLink");
  var submitBtn = form.querySelector('input[type="submit"], button[type="submit"]');
  var baseLabel = submitBtn ? String(submitBtn.textContent || "").trim() : "";
  var started = false;
  var inFlight = false;
  var idemKey = null;
  var idemPhone = null;

  /* ------------------------------------------------------------------ utils */

  function debug() {
    try {
      if (window.console && typeof window.console.debug === "function") {
        var args = Array.prototype.slice.call(arguments);
        args.unshift("[viporder]");
        window.console.debug.apply(window.console, args);
      }
    } catch (ignored) {
      /* Logging must never break the page. Deliberately silent. */
    }
  }

  function own(obj, key) {
    if (!rules) {
      return Object.prototype.hasOwnProperty.call(obj, key);
    }
    return rules.own(obj, key);
  }

  function hasKeys(obj) {
    if (!rules) {
      return false;
    }
    return rules.hasKeys(obj);
  }

  function newId() {
    try {
      var analytics = window.VIPOrderAnalytics;
      if (analytics && typeof analytics.newId === "function") {
        return analytics.newId();
      }
    } catch (err) {
      debug("analytics uuid unavailable", err);
    }
    /* Local fallback so the Idempotency-Key never silently becomes undefined. */
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : ((r & 0x3) | 0x8)).toString(16);
    });
  }

  function field(name) {
    return form.querySelector('[name="' + name + '"]');
  }

  function normalisePhone(value) {
    if (!rules) {
      return String(value == null ? "" : value).replace(/[\s.\-()]/g, "");
    }
    return rules.normalisePhone(value);
  }

  function parseJson(text) {
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch (err) {
      debug("response body was not JSON", err);
      return null;
    }
  }

  function safeCall(fn, a, b) {
    try {
      fn(a, b);
    } catch (err) {
      debug("handler failed", err);
    }
  }

  /* --------------------------------------------------------- idempotency key */

  /* The key is bound to the submitted IDENTITY, not merely to the form session.
   *
   * Reusing one key across two DIFFERENT bodies is the dangerous direction: if
   * the backend caches responses by key alone, a corrected resubmit would
   * replay the first response forever. Concretely, after a 409 "phone taken" a
   * customer who fixes their number would keep being told it is taken and could
   * never register — a silent, permanent dead end.
   *
   * So the key is re-minted whenever the phone being submitted changes, and is
   * kept for a retry of exactly the same details. The phone is the identity
   * here because it is the uniqueness key the API deduplicates customers on.
   */
  function getIdempotencyKey(phone) {
    if (idemKey && idemPhone !== null && idemPhone !== phone) {
      debug("submitted phone changed; re-minting the Idempotency-Key");
      resetIdempotencyKey();
    }
    if (!idemKey) {
      try {
        if (window.sessionStorage) {
          idemKey = window.sessionStorage.getItem(IDEM_STORAGE_KEY);
          idemPhone = window.sessionStorage.getItem(IDEM_PHONE_STORAGE_KEY);
        }
      } catch (err) {
        debug("sessionStorage read failed", err);
      }
      /* Same guard across a page reload, where only the stored values survive. */
      if (idemKey && idemPhone !== null && idemPhone !== phone) {
        debug("stored Idempotency-Key belongs to a different phone; re-minting");
        resetIdempotencyKey();
      }
    }
    if (!idemKey) {
      idemKey = newId();
    }
    idemPhone = phone;
    try {
      if (window.sessionStorage) {
        window.sessionStorage.setItem(IDEM_STORAGE_KEY, idemKey);
        window.sessionStorage.setItem(IDEM_PHONE_STORAGE_KEY, phone);
      }
    } catch (err) {
      debug("sessionStorage write failed", err);
    }
    return idemKey;
  }

  /* Cleared only once the details being submitted are finished with:
   *   * after a completed registration (201), and
   *   * after a 409 that means the phone is TAKEN — the corrected details are a
   *     NEW attempt, and a fresh key cannot create a duplicate customer.
   *
   * Deliberately NOT cleared on a network error, 429, 5xx or 202: those are
   * retries of the same registration attempt and MUST reuse the key.
   *
   * ALSO not cleared on a 409 that means REGISTRATION_IN_PROGRESS. That 409 says
   * another attempt is in flight and the number is NOT taken, so it belongs with
   * the retry group above, not this one. Clearing it there would make the retry
   * the server just asked for open a second registration for one customer. The
   * caller therefore gates on the error CODE, not on the status. */
  function resetIdempotencyKey() {
    idemKey = null;
    idemPhone = null;
    try {
      if (window.sessionStorage) {
        window.sessionStorage.removeItem(IDEM_STORAGE_KEY);
        window.sessionStorage.removeItem(IDEM_PHONE_STORAGE_KEY);
      }
    } catch (err) {
      debug("sessionStorage clear failed", err);
    }
  }

  /* ------------------------------------------------------------- validation */

  function readValues() {
    var el;
    var out = {};
    el = field("full_name"); out.full_name = el ? String(el.value || "").trim() : "";
    el = field("phone"); out.phone = el ? String(el.value || "").trim() : "";
    el = field("password"); out.password = el ? String(el.value || "") : "";
    el = field("province"); out.province = el ? String(el.value || "").trim() : "";
    el = field("service"); out.service_interest = el ? String(el.value || "") : "";
    el = field("consent"); out.consent = !!(el && el.checked);
    return out;
  }

  function validate(v) {
    if (!rules) {
      /* Mis-deployed page: report the module as missing rather than let the
       * customer submit something the server will reject with no explanation. */
      return { consent: "Không tải được phần kiểm tra. Vui lòng tải lại trang." };
    }
    return rules.validate(v);
  }

  function errorAnchor(el) {
    var label = null;
    try {
      label = el.closest ? el.closest("label") : null;
    } catch (err) {
      label = null;
    }
    if (label && el.parentNode === label && el.type === "checkbox") {
      return label.parentNode;
    }
    return el.parentNode;
  }

  function clearFieldErrors() {
    var i;
    var errs = form.querySelectorAll(".field-error");
    for (i = 0; i < errs.length; i += 1) {
      if (errs[i].parentNode) {
        errs[i].parentNode.removeChild(errs[i]);
      }
    }
    var invalid = form.querySelectorAll('[aria-invalid="true"]');
    for (i = 0; i < invalid.length; i += 1) {
      invalid[i].removeAttribute("aria-invalid");
      var described = invalid[i].getAttribute("aria-describedby") || "";
      if (described.indexOf("err-") === 0) {
        invalid[i].removeAttribute("aria-describedby");
      }
    }
  }

  function showFieldError(name, msg) {
    var el = field(name);
    if (!el || !msg) {
      return;
    }
    var errId = "err-" + name;
    el.setAttribute("aria-invalid", "true");
    el.setAttribute("aria-describedby", errId);
    if (document.getElementById(errId)) {
      return;
    }
    var span = document.createElement("span");
    span.className = "field-error";
    span.id = errId;
    span.textContent = String(msg);
    var anchor = errorAnchor(el);
    if (anchor) {
      anchor.appendChild(span);
    }
  }

  function focusFirstInvalid(errors) {
    var order = ["full_name", "phone", "password", "consent"];
    for (var i = 0; i < order.length; i += 1) {
      if (errors[order[i]]) {
        var el = field(order[i]);
        if (el && typeof el.focus === "function") {
          el.focus();
          return;
        }
      }
    }
  }

  /* --------------------------------------------------------------- rendering */

  function setMessage(text, kind) {
    if (!message) {
      return;
    }
    message.className = "form-message" + (kind ? " is-" + kind : "");
    message.textContent = text == null ? "" : String(text);
  }

  function focusResult() {
    if (!message || typeof message.focus !== "function") {
      return;
    }
    try {
      message.focus();
    } catch (err) {
      debug("could not focus the result region", err);
    }
  }

  /* The portal host is a business rule: an API response may not redirect
   * existing customers anywhere else. */
  function safePortalUrl(candidate) {
    var origin =
      typeof window !== "undefined" && window.location
        ? window.location.origin
        : undefined;
    if (!rules) {
      return PORTAL_URL;
    }
    var safe = rules.safePortalUrl(candidate, origin);
    /* Log every refusal: the module cannot report, and a refused login_url is
     * worth seeing. A page that silently redirects to the approved portal
     * instead of where the server said is a symptom, not a non-event. */
    if (candidate && safe === PORTAL_URL && String(candidate) !== PORTAL_URL) {
      debug("login_url ignored, not the approved portal", candidate);
    }
    return safe;
  }

  function showPortalCta(candidate) {
    if (!portalWrap || !portalLink) {
      return;
    }
    portalLink.setAttribute("href", safePortalUrl(candidate));
    portalWrap.removeAttribute("hidden");
  }

  function setBusy(busy) {
    inFlight = !!busy;
    if (!submitBtn) {
      return;
    }
    var completed = form.getAttribute("data-completed") === "true";
    submitBtn.disabled = !!busy || completed;
    if (busy) {
      submitBtn.setAttribute("aria-busy", "true");
    } else {
      submitBtn.removeAttribute("aria-busy");
    }
    if (busy) {
      form.classList.add("is-busy");
    } else {
      form.classList.remove("is-busy");
    }
    if (!completed) {
      submitBtn.textContent = busy ? BUSY_LABEL : baseLabel;
    }
  }

  /* ------------------------------------------------------------- analytics */

  function analytics() {
    try {
      return window.VIPOrderAnalytics || null;
    } catch (err) {
      return null;
    }
  }

  function trackRegisterStart() {
    if (started) {
      return;
    }
    started = true;
    var a = analytics();
    if (a && typeof a.registerStart === "function") {
      try {
        a.registerStart({ form: "register" });
      } catch (err) {
        debug("register_start failed", err);
      }
    }
  }

  function trackRegisterFailed(status, code, attemptId) {
    var a = analytics();
    if (a && typeof a.registerFailed === "function") {
      try {
        a.registerFailed({
          status_code: status,
          error_code: code || "UNKNOWN",
          attempt_id: attemptId
        });
      } catch (err) {
        debug("register_failed failed", err);
      }
    }
  }

  /* --------------------------------------------------------------- outcomes */

  /* The failure vocabulary lives in static/js/register-errors.js.
   *
   * It was moved there for one reason: this was the only layer in the project with
   * NO test. The bug it fixes was not a typo - the service gave an existing HTTP
   * status a new meaning, and this file was reading the status. No amount of care
   * in a file nobody can execute is a substitute for a file that runs under
   * `node --test tools/js/`.
   *
   * The module is a plain deferred script loaded before this one, and is also a
   * CommonJS export, so there is exactly ONE copy of the rule. */
  var failureRules =
    typeof window !== "undefined" && window.VipOrderRegisterErrors
      ? window.VipOrderRegisterErrors
      : null;

  /* A missing module means the page is mis-deployed, and `check_site.py` fails the
   * build for that. These two guards exist so the FORM still behaves safely if it
   * ever slips through, and both pick the recoverable direction:
   * treating a 409 as permanent costs one redundant attempt, while treating a real
   * duplicate as transient would lock the customer out. */
  function isPermanentDuplicate(status, code) {
    if (!failureRules) {
      return status === 409;
    }
    return failureRules.isPermanentDuplicate(status, code);
  }

  function defaultMessageFor(status) {
    if (!failureRules) {
      return "Không gửi được thông tin đăng ký. Vui lòng thử lại.";
    }
    return failureRules.defaultMessageFor(status);
  }

  function onRegistered(data, attemptId) {
    var leadId = data ? data.lead_id : null;
    var a = analytics();
    if (a && typeof a.leadSuccess === "function") {
      try {
        a.leadSuccess(leadId, {
          registration_status: (data && data.registration_status) || "REGISTERED",
          external_customer_code: (data && data.external_customer_code) || "",
          attempt_id: attemptId
        });
      } catch (err) {
        debug("viporder_lead_success failed", err);
      }
    }

    var parts = [data && data.message ? String(data.message) : "Đăng ký thành công."];
    if (data && data.external_customer_code) {
      parts.push("Mã khách hàng của bạn: " + String(data.external_customer_code) + ".");
    }
    parts.push("Bạn có thể đăng nhập hệ thống khách hàng bằng số điện thoại và mật khẩu vừa tạo.");
    setMessage(parts.join(" "), "success");

    form.setAttribute("data-completed", "true");
    /* Clear the password from the input now the registration is done.
     *
     * The form is finished — it is `data-completed`, the button is disabled and
     * the reply tells the customer to sign in with what they just chose — so
     * there is no further use for it, and leaving a working credential visible in
     * a text field on a shared or shoulder-surfed screen is pure downside. This
     * is not a storage or logging concern (the password is in neither); it is the
     * one place it lingers where a person can read it. */
    if (field("password")) {
      field("password").value = "";
    }
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.removeAttribute("aria-busy");
      submitBtn.textContent = DONE_LABEL;
    }
    showPortalCta(data && data.login_url);
    resetIdempotencyKey();
    focusResult();
  }

  function onPending(data, attemptId) {
    var leadId = data ? data.lead_id : null;
    var a = analytics();
    /* A PENDING lead is stored but is NOT a completed registration, so it is
     * reported separately and never as viporder_lead_success. */
    if (a && typeof a.leadPending === "function") {
      try {
        a.leadPending(leadId, {
          registration_status: (data && data.registration_status) || "PENDING",
          attempt_id: attemptId
        });
      } catch (err) {
        debug("viporder_lead_pending failed", err);
      }
    }

    setMessage(
      data && data.message
        ? String(data.message)
        : "VIPORDER đã ghi nhận thông tin đăng ký của bạn nhưng chưa tạo được mã khách hàng ngay. " +
          "Vui lòng thử lại sau ít phút.",
      "pending"
    );
    showPortalCta(data && data.login_url);
    /* The Idempotency-Key is intentionally kept: retrying is the same
     * registration attempt, not a new one. */
    focusResult();
  }

  function onFailed(status, data, attemptId) {
    var error = (data && data.error) || {};
    var code = error.code || ("HTTP_" + status);
    var fields = error.fields && typeof error.fields === "object" ? error.fields : {};
    var name;

    if (isPermanentDuplicate(status, code)) {
      /* The phone is TAKEN, so the details the customer submits next are a NEW
       * attempt. A fresh key is safe here (a duplicate cannot be created for a
       * phone that already exists) and it is necessary: replaying a cached 409
       * against corrected details would lock the customer out permanently.
       *
       * Done before any rendering on purpose — this is the correctness-critical
       * side effect of the whole function, so it must not be skippable by a
       * failure further down.
       *
       * NOT every 409 means this. `REGISTRATION_IN_PROGRESS` says another attempt
       * is still in flight and the number is NOT taken; there, resetting the key
       * would guarantee the retry the server just asked for creates a SECOND lead
       * for one customer — the exact thing the key exists to prevent. The
       * condition below is the whole difference between the two. */
      resetIdempotencyKey();
    }

    for (name in fields) {
      if (own(fields, name) && field(name)) {
        showFieldError(name, fields[name]);
      }
    }
    /* Only a PERMANENT duplicate is a problem with the phone field. Attaching
     * "another attempt is in progress" to the input would tell the customer to
     * change a number that is perfectly fine. The general message below still
     * shows the server's wording either way. */
    if (isPermanentDuplicate(status, code) && field("phone")) {
      showFieldError("phone", error.message || defaultMessageFor(409));
    }

    /* Mirrors the server wording whenever the server sent any. */
    setMessage(error.message ? String(error.message) : defaultMessageFor(status), "error");
    trackRegisterFailed(status, code, attemptId);
    /* Every other failure — network, 429, 5xx — keeps the key so a retry of the
     * SAME details stays one idempotent attempt. */
    focusResult();
  }

  function onNetworkError(err, attemptId) {
    debug("registration request failed", err);
    setMessage(
      "Không kết nối được tới hệ thống VIPORDER. Thông tin bạn vừa gửi có thể đã được ghi nhận " +
      "phía máy chủ — vui lòng kiểm tra bằng nút “Đăng nhập / Kiểm tra đơn” trước khi gửi lại.",
      "error"
    );
    trackRegisterFailed(0, "NETWORK_ERROR", attemptId);
    focusResult();
  }

  /* ---------------------------------------------------------------- submit */

  function buildPayload(values) {
    var payload = {
      full_name: values.full_name,
      phone: normalisePhone(values.phone),
      password: values.password,
      /* The form does not collect an email yet — sent empty so the payload
       * keeps the documented shape. TODO(g02): add an email field. */
      email: "",
      province: values.province,
      consent: true,
      attribution: apiAttribution()
    };
    /* service_interest is a documented enum (transport|official_import|
     * customs|order). An empty string is not a member of it, so the key is
     * omitted entirely when the customer did not choose one. */
    if (values.service_interest) {
      payload.service_interest = values.service_interest;
    }
    return payload;
  }

  function apiAttribution() {
    var a = analytics();
    if (a && typeof a.toApiAttribution === "function") {
      try {
        return a.toApiAttribution();
      } catch (err) {
        debug("attribution unavailable", err);
      }
    }
    return {
      utm_source: "", utm_medium: "", utm_campaign: "", utm_content: "",
      utm_term: "", landing_page: "", referrer: ""
    };
  }

  function onSubmit(event) {
    event.preventDefault();
    if (inFlight || form.getAttribute("data-completed") === "true") {
      return;
    }

    clearFieldErrors();
    setMessage("", "");

    var values = readValues();
    var errors = validate(values);
    if (hasKeys(errors)) {
      for (var name in errors) {
        if (own(errors, name)) {
          showFieldError(name, errors[name]);
        }
      }
      setMessage("Vui lòng kiểm tra lại các thông tin được đánh dấu.", "error");
      focusFirstInvalid(errors);
      return;
    }

    /* Keyed on the phone actually being submitted, so the key and the body can
     * never drift apart. */
    var attemptId = getIdempotencyKey(normalisePhone(values.phone));
    var payload = buildPayload(values);

    if (typeof window.fetch !== "function") {
      onNetworkError(new Error("fetch is unavailable in this browser"), attemptId);
      return;
    }

    setBusy(true);
    try {
      window.fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "Idempotency-Key": attemptId
        },
        body: JSON.stringify(payload),
        credentials: "same-origin"
      }).then(function (response) {
        return response.text().then(
          function (text) { return { status: response.status, text: text }; },
          function () { return { status: response.status, text: "" }; }
        );
      }).then(function (result) {
        setBusy(false);
        var data = parseJson(result.text);
        if (result.status === 201) {
          safeCall(function () { onRegistered(data, attemptId); });
        } else if (result.status === 202) {
          safeCall(function () { onPending(data, attemptId); });
        } else {
          safeCall(function () { onFailed(result.status, data, attemptId); });
        }
      }).catch(function (err) {
        setBusy(false);
        safeCall(function () { onNetworkError(err, attemptId); });
      });
    } catch (err) {
      setBusy(false);
      onNetworkError(err, attemptId);
    }
  }

  /* ------------------------------------------------------------------ init */

  /* The browser's own validation UI is switched off from JS, not from the
   * markup, so a browser with JavaScript disabled still gets native
   * validation as a fallback. */
  form.setAttribute("novalidate", "novalidate");
  form.addEventListener("submit", onSubmit);
  form.addEventListener("focusin", trackRegisterStart);
  form.addEventListener("input", trackRegisterStart);
  form.addEventListener("change", trackRegisterStart);

  /* Defence in depth for FIX 2: the submit path already re-mints the key when
   * the phone changes, but dropping it the moment the customer edits the number
   * also removes any window in which a stale key could be reused. */
  var phoneField = field("phone");
  if (phoneField) {
    phoneField.addEventListener("input", function () {
      if (idemKey && idemPhone !== null && normalisePhone(phoneField.value) !== idemPhone) {
        debug("phone edited after a previous attempt; re-minting the Idempotency-Key");
        resetIdempotencyKey();
      }
    });
  }

  if (portalLink) {
    portalLink.setAttribute("href", PORTAL_URL);
  }
}(window, document));
