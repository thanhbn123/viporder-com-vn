/* The registration form's decisions that do not need a page.
 *
 * WHY THIS FILE EXISTS. Round 3 and 4 gave the page three tested modules, but the
 * two decisions below were still only in `register.js`, with nothing executing
 * them, and they are the two most consequential in the file:
 *
 *   safePortalUrl  is an OPEN-REDIRECT GUARD. The server sends a `login_url` and
 *                  the page navigates the customer to it. If the host check is
 *                  wrong, a bad or compromised response can send a customer who
 *                  has just typed their password to somebody else's site. It
 *                  returns the approved portal unless the candidate is BOTH
 *                  https AND exactly the approved host.
 *
 *   validate       decides whether the customer is allowed to submit at all.
 *                  Getting it wrong either blocks a real customer or sends the
 *                  server a request it will reject.
 *
 * Both are pure functions of their arguments, so both can be tested without a DOM
 * and without a test-library dependency - which is the answer to the question
 * round 4 left open: extract what does not need a DOM, and do not add jsdom for
 * the rest.
 *
 * Dependency-free; loads as a browser script and as a CommonJS export.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.VipOrderRegisterValidation = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* The one host the page is ever allowed to navigate a customer to. */
  var PORTAL_URL = "https://khachhang.viporder.com.vn";
  var PORTAL_HOST = "khachhang.viporder.com.vn";

  /* Vietnamese mobile and landline forms: +84 / 84 / 0, then a mobile prefix
   * (3/5/7/8/9) with 8 digits, or a landline 2-prefix with 8-9. */
  var VN_PHONE = /^(?:(?:\+84)|84|0)(?:[35789]\d{8}|2\d{8,9})$/;

  function own(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  function hasKeys(obj) {
    for (var key in obj) {
      if (own(obj, key)) {
        return true;
      }
    }
    return false;
  }

  /* Strip the separators people actually type, so `0912 345 678`,
   * `0912.345.678` and `0912-345-678` all reach the same value. */
  function normalisePhone(value) {
    return String(value == null ? "" : value).replace(/[\s.\-()]/g, "");
  }

  /* The candidate is used ONLY if it is https AND exactly the approved host.
   *
   * Both halves matter:
   *   - https, because an http portal is a downgrade an attacker can read;
   *   - the EXACT host, because `host` is the parsed hostname and not a prefix
   *     test, which is what stops `khachhang.viporder.com.vn.evil.com` and
   *     `khachhang.viporder.com.vn@evil.com` from passing.
   *
   * A candidate that fails - including one that cannot be parsed at all - falls
   * back to the approved portal rather than to nothing: the customer has already
   * registered, and a missing link is a worse outcome than a link to the right
   * place. It never throws.
   */
  function safePortalUrl(candidate, origin) {
    try {
      if (candidate && typeof URL === "function") {
        var url = new URL(candidate, origin);
        if (url.protocol === "https:" && url.host === PORTAL_HOST) {
          return url.href;
        }
      }
    } catch (err) {
      /* Unparseable. Fall through to the approved portal - the caller decides
       * whether to report it. */
    }
    return PORTAL_URL;
  }

  /* Returns an object of field -> message. Empty means valid.
   *
   * Deliberately does NOT check the email address: the server owns that rule, and
   * a second, different copy of it here would block customers the server would
   * have accepted. Client-side validation is for speed, not for authority. */
  function validate(v) {
    var values = v || {};
    var errors = {};
    if (!values.full_name) {
      errors.full_name = "Vui lòng nhập họ và tên.";
    } else if (values.full_name.length < 2) {
      errors.full_name = "Họ và tên quá ngắn.";
    }
    if (!values.phone) {
      errors.phone = "Vui lòng nhập số điện thoại.";
    } else if (!VN_PHONE.test(normalisePhone(values.phone))) {
      errors.phone = "Số điện thoại chưa đúng định dạng Việt Nam (ví dụ: 0912345678).";
    }
    if (!values.password) {
      errors.password = "Vui lòng nhập mật khẩu.";
    } else if (values.password.length < 8) {
      errors.password = "Mật khẩu cần tối thiểu 8 ký tự.";
    }
    if (!values.consent) {
      errors.consent = "Vui lòng đồng ý để VIPORDER tạo tài khoản và hỗ trợ dịch vụ.";
    }
    return errors;
  }

  return {
    PORTAL_URL: PORTAL_URL,
    PORTAL_HOST: PORTAL_HOST,
    VN_PHONE: VN_PHONE,
    own: own,
    hasKeys: hasKeys,
    normalisePhone: normalisePhone,
    safePortalUrl: safePortalUrl,
    validate: validate
  };
});
