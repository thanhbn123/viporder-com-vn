/* The failure vocabulary of the registration form, in one place.
 *
 * WHY THIS FILE EXISTS. HTTP status alone does not identify what happened. A 409
 * means one of two OPPOSITE things:
 *
 *   DUPLICATE_PHONE          - the number is registered. Permanently. Minting a
 *                              new idempotency key is right, because the customer
 *                              is about to change something and re-submit.
 *   REGISTRATION_IN_PROGRESS - another attempt is in flight RIGHT NOW. The number
 *                              is NOT taken, and the retry the server just asked
 *                              for must reuse the SAME key, or it opens a second
 *                              registration for one customer.
 *
 * The form used to switch on the status alone, so it treated the second as the
 * first: it told the customer to retry and simultaneously threw away the thing
 * that makes retrying safe. Nothing was wrong in isolation - a change in the
 * service gave an existing status a new meaning while the client was reading the
 * status.
 *
 * So this file takes the CODE, not the status, as the thing that carries the
 * distinction, and it is deliberately dependency-free and loadable by both the
 * browser and Node, so the rule can be TESTED rather than only read. There is no
 * second copy of this logic anywhere: `register.js` calls in here.
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
    root.VipOrderRegisterErrors = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* The ONLY 409 that means the number is taken for good.
   *
   * When the server sends no code we fall back to the old behaviour - treat it as
   * permanent - because that was the only 409 that existed when the form was
   * written, and it is the RECOVERABLE mistake: keeping a stale key after a real
   * duplicate locks the customer out permanently, while resetting it early merely
   * costs one redundant attempt.
   */
  function isPermanentDuplicate(status, code) {
    if (status !== 409) {
      return false;
    }
    return !code || code === "DUPLICATE_PHONE";
  }

  /* The transient 409. Named separately so callers read as what they mean. */
  function isInProgress(status, code) {
    return status === 409 && code === "REGISTRATION_IN_PROGRESS";
  }

  function defaultMessageFor(status) {
    if (status === 422) {
      return "Thông tin đăng ký chưa hợp lệ. Vui lòng kiểm tra lại các ô được đánh dấu.";
    }
    if (status === 409) {
      /* Never promise "already registered" here. Without a code we cannot tell
       * which of the two events this is, and telling a customer whose
       * registration is merely in flight to go and sign in sends them to an
       * account that does not exist yet. Both meanings are stated, and the retry
       * is offered first because it is the one that works in both cases. */
      return (
        "Số điện thoại này đã được đăng ký, hoặc một lượt đăng ký cho số này đang " +
        "được xử lý. Vui lòng thử lại sau vài giây; nếu vẫn không được, hãy đăng nhập " +
        "hoặc dùng số điện thoại khác."
      );
    }
    if (status === 429) {
      return "Bạn đã gửi quá nhiều lần trong thời gian ngắn. Vui lòng thử lại sau ít phút.";
    }
    if (status === 503) {
      /* The lead may well have been stored. Never say nothing happened. */
      return (
        "Hệ thống đăng ký đang tạm bận. Thông tin bạn gửi có thể đã được ghi nhận — " +
        "vui lòng thử lại sau ít phút và không gửi lại nhiều lần."
      );
    }
    if (status >= 500) {
      return (
        "Hệ thống đăng ký đang gặp sự cố. Thông tin bạn gửi có thể đã được ghi nhận — " +
        "vui lòng thử lại sau ít phút và không gửi lại nhiều lần."
      );
    }
    return "Không gửi được thông tin đăng ký. Vui lòng thử lại.";
  }

  /* Everything the form must DECIDE about a failure, as data rather than as
   * branches scattered through a DOM handler - so it can be asserted. */
  function planFor(status, code) {
    var permanent = isPermanentDuplicate(status, code);
    return {
      /* Mint a new idempotency key: only when the phone is taken for good. */
      resetKey: permanent,
      /* Point the error at the phone field: only when the phone is the problem.
       * Blaming the input for "someone else is registering it right now" tells the
       * customer to change a number that is perfectly fine. */
      phoneFieldError: permanent,
      /* Keep the key and let the customer simply retry. */
      retryable: isInProgress(status, code),
      message: defaultMessageFor(status)
    };
  }

  return {
    isPermanentDuplicate: isPermanentDuplicate,
    isInProgress: isInProgress,
    defaultMessageFor: defaultMessageFor,
    planFor: planFor
  };
});
