/* Attribution: what the page is allowed to REMEMBER and to SEND.
 *
 * WHY THIS IS SEPARATE FROM analytics.js. Two reasons, and the second is the
 * important one.
 *
 * 1. It was the only part of the page with no test. `analytics.js` decides what
 *    leaves the browser, and nothing executed it - the same gap that let a
 *    service-side change silently break the registration form.
 *
 * 2. It is a BOUNDARY, and a boundary should be a function you can call in a test
 *    rather than a habit you hope holds. Everything downstream builds its payload
 *    from `toApiAttributionFrom`, which emits exactly seven named fields, each
 *    clipped, and nothing else. A stored blob carrying extra keys cannot smuggle
 *    them out, and a 200 kB query string cannot become a 200 kB request.
 *
 * Dependency-free and loadable by both the browser and Node, so the rule has ONE
 * copy - not two kept in step by hand.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.VipOrderAttribution = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"];
  var MAX_FIELD = 200;
  var MAX_URL = 1000;

  function clip(value, max) {
    var s = value == null ? "" : String(value);
    return s.length > max ? s.slice(0, max) : s;
  }

  /* Read the UTM parameters out of a query string.
   *
   * Always returns EVERY key, defaulting to "", so a caller never has to guard
   * against `undefined` - a missing key and an absent parameter are the same
   * thing here, and making them different shapes is how `undefined` reaches a
   * request body.
   *
   * `onError` is called rather than the failure being swallowed: a silently
   * broken attribution read looks exactly like a page nobody arrived at with
   * UTMs, which is a real answer that would be wrong.
   */
  function readUtms(search, onError) {
    var out = {};
    var i;
    for (i = 0; i < UTM_KEYS.length; i += 1) {
      out[UTM_KEYS[i]] = "";
    }
    try {
      if (typeof URLSearchParams !== "function") {
        return out;
      }
      var params = new URLSearchParams(search || "");
      for (i = 0; i < UTM_KEYS.length; i += 1) {
        out[UTM_KEYS[i]] = clip(params.get(UTM_KEYS[i]) || "", MAX_FIELD);
      }
    } catch (err) {
      if (typeof onError === "function") {
        onError(err);
      }
    }
    return out;
  }

  function hasAnyUtm(utms) {
    if (!utms || typeof utms !== "object") {
      return false;
    }
    for (var i = 0; i < UTM_KEYS.length; i += 1) {
      if (utms[UTM_KEYS[i]]) {
        return true;
      }
    }
    return false;
  }

  function emptyTouch() {
    return { captured_at: 0, landing_page: "", referrer: "", utm: readUtms("") };
  }

  /* Rebuild a stored touch from scratch, keeping ONLY the known fields.
   *
   * It is a rebuild rather than a merge on purpose. Merge-and-clean would carry
   * every unrecognised key forward into whatever reads it next; this drops them,
   * so the set of things that can reach the API is fixed here rather than
   * wherever the payload happens to be assembled.
   */
  function normaliseTouch(value) {
    if (!value || typeof value !== "object") {
      return emptyTouch();
    }
    var utm = value.utm && typeof value.utm === "object" ? value.utm : {};
    var out = {
      captured_at: value.captured_at || 0,
      landing_page: clip(value.landing_page || "", MAX_URL),
      referrer: clip(value.referrer || "", MAX_URL),
      utm: {}
    };
    for (var i = 0; i < UTM_KEYS.length; i += 1) {
      out.utm[UTM_KEYS[i]] = clip(utm[UTM_KEYS[i]] || "", MAX_FIELD);
    }
    return out;
  }

  /* The exact shape that is sent to the API. Seven fields, all clipped.
   *
   * This is the boundary. Nothing else may be added here without appearing in
   * the test that pins the field set, which is the point. */
  function toApiAttributionFrom(touch) {
    var t = normaliseTouch(touch);
    return {
      utm_source: t.utm.utm_source,
      utm_medium: t.utm.utm_medium,
      utm_campaign: t.utm.utm_campaign,
      utm_content: t.utm.utm_content,
      utm_term: t.utm.utm_term,
      landing_page: t.landing_page,
      referrer: t.referrer
    };
  }

  return {
    UTM_KEYS: UTM_KEYS,
    MAX_FIELD: MAX_FIELD,
    MAX_URL: MAX_URL,
    clip: clip,
    readUtms: readUtms,
    hasAnyUtm: hasAnyUtm,
    emptyTouch: emptyTouch,
    normaliseTouch: normaliseTouch,
    toApiAttributionFrom: toApiAttributionFrom
  };
});
