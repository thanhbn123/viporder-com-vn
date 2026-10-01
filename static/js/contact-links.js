/* Which contact links are which.
 *
 * WHY THIS IS SEPARATE FROM app.js. `contactKind` decides how a click is
 * LABELLED in analytics, and it was the last piece of pure logic in the page with
 * nothing executing it. Two things can go wrong quietly here:
 *
 *   - a real Zalo or phone link is missed, so the click is never counted;
 *   - a link that is NOT ours is classified as one, so the numbers are wrong.
 *
 * The second is the one worth a test. The patterns below are anchored, and the
 * tests pin the near-misses that a looser pattern would accept:
 * `notzalo.me`, `zalo.me.evil.com`, `evil.com/zalo.me/`, `zalo.me@evil.com`.
 *
 * Takes plain strings rather than a DOM node, so it can be called in a test.
 * Dependency-free; loads as a browser script and as a CommonJS export.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.VipOrderContactLinks = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* `zalo.(me|com)` preceded by any number of subdomains, and REQUIRED to be the
   * host: the trailing `/` (or end of string) is what stops `zalo.me.evil.com`
   * and `zalo.me@evil.com` from matching. */
  var ZALO = /^https?:\/\/([a-z0-9-]+\.)*zalo\.(me|com)(\/|$)/i;
  var TEL = /^tel:/i;

  /* An explicit `data-viporder-contact` always wins.
   *
   * It is the only way to label a link whose URL does not reveal the kind - a
   * shortened Zalo link, a redirect, an in-page button. Letting the URL override
   * the author's declaration would make the attribute useless. */
  function contactKind(declared, href) {
    if (declared === "phone" || declared === "zalo") {
      return declared;
    }
    var url = href == null ? "" : String(href);
    if (TEL.test(url)) {
      return "phone";
    }
    if (ZALO.test(url)) {
      return "zalo";
    }
    return null;
  }

  return { contactKind: contactKind, ZALO: ZALO, TEL: TEL };
});
