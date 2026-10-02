/*!
 * VIPORDER — first-party analytics layer (gate G05).
 * https://viporder.com.vn
 *
 * Design constraints (read before editing)
 * ----------------------------------------
 * 1. NO SECRETS, EVER. This file is served to every visitor and the repository
 *    is public. Nothing server-side, no API key, no CAPI access token belongs
 *    here. Server-side conversions are a backend concern.
 * 2. NO THIRD-PARTY TRACKING IS CONNECTED BY DEFAULT. GTM / GA4 / Meta Pixel
 *    are configured but dormant (`enabled: false`). See "HOW TO ENABLE" below.
 * 3. THIS FILE MUST NEVER THROW AND MUST NEVER BLOCK RENDERING. Every public
 *    entry point is wrapped in try/catch; failures go to console.debug only.
 *    Third-party loaders are injected on window `load` + requestIdleCallback,
 *    never in the critical path, and never as a blocking <script> in the HTML.
 * 4. Event identity is stable: every event carries an `event_id` (UUID). When
 *    the same LOGICAL event fires twice, the SAME `event_id` is reused so that
 *    a future server-side Meta CAPI / GA4 Measurement Protocol integration can
 *    de-duplicate. `viporder_lead_success` can never fire twice for one lead.
 *
 * HOW TO ENABLE THE THIRD-PARTY TAGS (they are OFF by default)
 * ------------------------------------------------------------
 *   Step 1. In this file set the master switch:
 *               VIPORDER_TRACKING.enabled = true;
 *   Step 2. Flip ONLY the provider you actually want to load:
 *               VIPORDER_TRACKING.gtm.enabled       = true;  // GTM-KKCBDP5R
 *               VIPORDER_TRACKING.ga4.enabled       = true;  // G-581GP4TP51
 *               VIPORDER_TRACKING.metaPixel.enabled = true;  // 901417776091392
 *   Step 3. Deploy. The loaders run after first paint, so page render is never
 *           blocked by a third party. Verify in DevTools > Network that the
 *           request only appears once you have opted in.
 *   Step 4. To go dark again, set VIPORDER_TRACKING.enabled = false. No HTML
 *           change is needed and no third-party <script> tag remains in the DOM
 *           (loaders are never injected while the flag is false).
 *
 *   Consent: enabling these tags is a legal decision, not a technical one.
 *   Wire the master switch to your consent mechanism before enabling in
 *   production for real visitors.
 *
 * Public API — window.VIPOrderAnalytics
 * -------------------------------------
 *   init()                          capture attribution, fire page_view, arm loaders
 *   track(name, params)             fire an event with a fresh event_id
 *   trackOnce(key, name, params)    fire at most once per session per logical key
 *   pageView()                      page_view
 *   serviceView(service, params)    service_view  (once per service per session)
 *   registerStart(params)           register_start (once per session)
 *   leadSuccess(leadId, params)     viporder_lead_success (once per lead, ever)
 *   leadPending(leadId, params)     viporder_lead_pending (kept lead, not a conversion)
 *   registerFailed(params)          register_failed
 *   phoneClick(params)              phone_click
 *   zaloClick(params)               zalo_click
 *   trackingSearch(type, result)    tracking_search (two fields; never the keyword)
 *   getAttribution()                flat + nested first-touch / last-touch
 *   toApiAttribution()              exactly the 7 keys the registration API wants
 *   getEventId(key)                 the stable event_id already used for a key
 *   newId()                         a UUID (used for the Idempotency-Key)
 *   uuid()                          alias of newId()
 *   config                          the live VIPORDER_TRACKING object
 */
(function (window, document) {
  "use strict";

  var VERSION = "1.0.0";
  var LOG_PREFIX = "[viporder]";

  /* =====================================================================
   * DORMANT third-party configuration.
   * These are the real production IDs. They are inert until enabled.
   * ================================================================== */
  var VIPORDER_TRACKING = {
    // Master switch. While false, NO third-party request is ever made.
    enabled: false,

    gtm: { enabled: false, id: "GTM-KKCBDP5R" },
    ga4: { enabled: false, id: "G-581GP4TP51" },
    metaPixel: { enabled: false, id: "901417776091392" }
  };
  window.VIPORDER_TRACKING = VIPORDER_TRACKING;

  /* First-party event vocabulary (G05). Keep this list stable — dashboards,
   * GTM triggers and any future CAPI mapping are built on these exact names. */
  var EVENTS = {
    PAGE_VIEW: "page_view",
    SERVICE_VIEW: "service_view",
    REGISTER_START: "register_start",
    LEAD_SUCCESS: "viporder_lead_success",
    /* Not part of the required vocabulary. A PENDING lead was accepted and
     * stored by the server but is NOT a completed registration, so it is
     * reported separately and must never be mapped to a conversion. */
    LEAD_PENDING: "viporder_lead_pending",
    REGISTER_FAILED: "register_failed",
    PHONE_CLICK: "phone_click",
    ZALO_CLICK: "zalo_click",
    /* G04B — the public tracking lookup. Only two fields travel with it; see
     * `trackingSearch` below for why the keyword is not one of them. */
    TRACKING_SEARCH: "tracking_search"
  };

  /* The closed vocabularies of tracking_search. An unknown value is reported as
   * "unknown" rather than dropped or passed through: a typo has to be VISIBLE in
   * the data, and letting it through would silently create a new dashboard
   * dimension that looks like a real one. */
  var TRACKING_SEARCH_TYPES = ["warehouse_import", "package_sealing"];
  var TRACKING_RESULTS = ["found", "not_found", "error"];

  /* Only defensible Meta mappings — anything else is sent as a custom event. */
  var META_EVENT_MAP = {
    page_view: "PageView",
    viporder_lead_success: "Lead",
    phone_click: "Contact",
    zalo_click: "Contact"
  };

  var ATTR_FIRST_KEY = "vo_attr_first_v1";
  var ATTR_LAST_KEY = "vo_attr_last_v1";
  var EVENT_IDS_KEY = "vo_event_ids_v1";
  /* Attribution rules live in static/js/analytics-attribution.js.
   *
   * That file is the BOUNDARY: what may be remembered and what may be sent. It
   * was moved out for one reason - this layer had nothing executing it, and a
   * boundary enforced by habit is not enforced. It is a plain deferred script
   * loaded before this one, and a CommonJS export, so there is ONE copy. */
  var attribution =
    typeof window !== "undefined" && window.VipOrderAttribution
      ? window.VipOrderAttribution
      : null;

  /* A missing module means a mis-deployed page, and `check_site.py` fails the
   * build for that. These fallbacks keep the page working and, crucially, keep it
   * sending NOTHING: a page that cannot compute attribution must not invent it. */
  var UTM_KEYS = attribution
    ? attribution.UTM_KEYS
    : ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"];
  var MAX_FIELD = attribution ? attribution.MAX_FIELD : 200;
  var MAX_URL = attribution ? attribution.MAX_URL : 1000;

  /* =====================================================================
   * Safe primitives. Nothing in this section may throw.
   * ================================================================== */

  function debug() {
    try {
      if (window.console && typeof window.console.debug === "function") {
        var args = Array.prototype.slice.call(arguments);
        args.unshift(LOG_PREFIX);
        window.console.debug.apply(window.console, args);
      }
    } catch (ignored) {
      /* Logging must never break the page. Deliberately silent. */
    }
  }

  /* In-process fallback when Web Storage is unavailable (private mode, blocked
   * cookies, quota errors). Keeps de-duplication working for the session. */
  var memoryStore = {};

  function storeGet(area, key) {
    try {
      var s = window[area];
      if (s) {
        return s.getItem(key);
      }
    } catch (err) {
      debug("storage read failed", area, err);
    }
    return Object.prototype.hasOwnProperty.call(memoryStore, area + ":" + key)
      ? memoryStore[area + ":" + key]
      : null;
  }

  function storeSet(area, key, value) {
    memoryStore[area + ":" + key] = value;
    try {
      var s = window[area];
      if (s) {
        s.setItem(key, value);
      }
    } catch (err) {
      debug("storage write failed", area, err);
    }
  }

  function readJson(area, key) {
    var raw = storeGet(area, key);
    if (!raw) {
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch (err) {
      debug("corrupt stored JSON discarded", key, err);
      return null;
    }
  }

  function writeJson(area, key, value) {
    try {
      storeSet(area, key, JSON.stringify(value));
    } catch (err) {
      debug("could not persist JSON", key, err);
    }
  }

  function clip(value, max) {
    if (!attribution) {
      var s = value == null ? "" : String(value);
      return s.length > max ? s.slice(0, max) : s;
    }
    return attribution.clip(value, max);
  }

  function newId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
      if (window.crypto && typeof window.crypto.getRandomValues === "function") {
        var bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40; /* version 4 */
        bytes[8] = (bytes[8] & 0x3f) | 0x80; /* variant 10 */
        var hex = [];
        for (var i = 0; i < 16; i += 1) {
          hex.push((bytes[i] + 0x100).toString(16).slice(1));
        }
        return hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
          hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
          hex.slice(10, 16).join("");
      }
    } catch (err) {
      debug("crypto unavailable, using fallback id", err);
    }
    /* Fallback only. This value is a de-duplication key, never a secret and
     * never a security token, so Math.random is acceptable here. */
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : ((r & 0x3) | 0x8);
      return v.toString(16);
    });
  }

  function dataLayerPush(payload) {
    try {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push(payload);
      return true;
    } catch (err) {
      debug("dataLayer push failed", err);
      return false;
    }
  }

  function currentPage() {
    try {
      return {
        path: clip(window.location.pathname + window.location.search, MAX_URL),
        title: clip(document.title || "", MAX_FIELD),
        url: clip(window.location.href, MAX_URL)
      };
    } catch (err) {
      debug("currentPage failed", err);
      return { path: "", title: "", url: "" };
    }
  }

  /* =====================================================================
   * Attribution — first-touch AND last-touch, neither is discarded.
   *
   * Precedence, documented and deliberate:
   *   FIRST TOUCH   written once, ever (localStorage). Records where this
   *                 visitor first landed and which campaign brought them.
   *   LAST TOUCH    written on the first page view and then overwritten every
   *                 time a page view carries at least one non-empty utm_*.
   *   FLAT FIELDS   what the registration API receives. The freshest signal
   *                 wins per key: live URL → last touch → first touch.
   *                 `landing_page` and `referrer` always describe the FIRST
   *                 touch, because that is what "where did this lead come
   *                 from" means for the lead record.
   * ================================================================== */

  function readUtms(search) {
    if (!attribution) {
      return {};
    }
    /* `debug` is passed in rather than the failure being swallowed: a broken
     * attribution read looks exactly like a page nobody arrived at with UTMs,
     * which is a real answer that would be wrong. */
    return attribution.readUtms(search, function (err) {
      debug("could not read UTM parameters", err);
    });
  }

  function hasAnyUtm(utms) {
    return attribution ? attribution.hasAnyUtm(utms) : false;
  }

  function emptyTouch() {
    return attribution ? attribution.emptyTouch() : { captured_at: 0, landing_page: "", referrer: "", utm: {} };
  }

  function normaliseTouch(value) {
    return attribution ? attribution.normaliseTouch(value) : emptyTouch();
  }

  function captureAttribution() {
    try {
      var snapshot = {
        captured_at: Date.now(),
        landing_page: clip(window.location.href, MAX_URL),
        referrer: clip(document.referrer || "", MAX_URL),
        utm: readUtms(window.location.search)
      };

      if (!readJson("localStorage", ATTR_FIRST_KEY)) {
        writeJson("localStorage", ATTR_FIRST_KEY, snapshot);
      }

      /* Last touch is seeded on the very first view so it is never empty, and
       * is replaced whenever a new campaign-tagged view arrives. */
      if (hasAnyUtm(snapshot.utm) || !readJson("localStorage", ATTR_LAST_KEY)) {
        writeJson("localStorage", ATTR_LAST_KEY, snapshot);
      }
    } catch (err) {
      debug("captureAttribution failed", err);
    }
  }

  function getAttribution() {
    var first = normaliseTouch(readJson("localStorage", ATTR_FIRST_KEY));
    var last = normaliseTouch(readJson("localStorage", ATTR_LAST_KEY) || first);
    var live = readUtms(window.location.search);
    var flat = {};

    for (var i = 0; i < UTM_KEYS.length; i += 1) {
      var key = UTM_KEYS[i];
      flat[key] = live[key] || last.utm[key] || first.utm[key] || "";
    }

    return {
      utm_source: flat.utm_source,
      utm_medium: flat.utm_medium,
      utm_campaign: flat.utm_campaign,
      utm_content: flat.utm_content,
      utm_term: flat.utm_term,
      landing_page: first.landing_page || last.landing_page || "",
      referrer: first.referrer || "",
      first_touch: first,
      last_touch: last
    };
  }

  /* Exactly the shape POST /api/v1/registrations expects. No extra keys. */
  function toApiAttribution() {
    var a = getAttribution();
    if (!attribution) {
      /* Mis-deployed page. Send the seven fields explicitly rather than sending
       * nothing: the registration still has to work, and the server validates. */
      return {
        utm_source: a.utm_source,
        utm_medium: a.utm_medium,
        utm_campaign: a.utm_campaign,
        utm_content: a.utm_content,
        utm_term: a.utm_term,
        landing_page: a.landing_page,
        referrer: a.referrer
      };
    }
    /* Routed through the tested boundary, which clips and emits exactly seven
     * fields. Copying them by hand here would be a SECOND place that decides what
     * may leave the page - and the one without tests. */
    return attribution.toApiAttributionFrom({
      captured_at: 0,
      landing_page: a.landing_page,
      referrer: a.referrer,
      utm: a
    });
  }

  /* =====================================================================
   * Event identity — one event_id per LOGICAL event, reused on repeat.
   * ================================================================== */

  var eventIds = null;

  function loadEventIds() {
    if (eventIds === null) {
      eventIds = readJson("sessionStorage", EVENT_IDS_KEY);
      if (!eventIds || typeof eventIds !== "object") {
        eventIds = {};
      }
    }
    return eventIds;
  }

  function getEventId(key) {
    var ids = loadEventIds();
    return Object.prototype.hasOwnProperty.call(ids, key) ? ids[key] : null;
  }

  function rememberEventId(key, id) {
    var ids = loadEventIds();
    ids[key] = id;
    writeJson("sessionStorage", EVENT_IDS_KEY, ids);
  }

  function metaForward(name, eventId, params) {
    if (!VIPORDER_TRACKING.enabled || !VIPORDER_TRACKING.metaPixel.enabled) {
      return;
    }
    if (typeof window.fbq !== "function") {
      return;
    }
    var standard = Object.prototype.hasOwnProperty.call(META_EVENT_MAP, name)
      ? META_EVENT_MAP[name]
      : null;
    /* `eventID` is what lets a future server-side CAPI call de-duplicate. */
    if (standard) {
      window.fbq("track", standard, params || {}, { eventID: eventId });
    } else {
      window.fbq("trackCustom", name, params || {}, { eventID: eventId });
    }
  }

  function emit(name, eventId, params) {
    var extra = params && typeof params === "object" ? params : {};
    try {
      var page = currentPage();
      var payload = {
        event: name,
        viporder_event_id: eventId,
        viporder_analytics_version: VERSION,
        viporder_attribution: getAttribution(),
        page_path: page.path,
        page_title: page.title
      };
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) {
          payload[key] = extra[key];
        }
      }
      /* Works whether or not GTM has loaded: dataLayer is created on demand and
       * GTM drains whatever is already queued when it does load. */
      dataLayerPush(payload);
      metaForward(name, eventId, extra);
    } catch (err) {
      debug("emit failed", name, err);
    }
    return { name: name, event_id: eventId, params: extra };
  }

  function track(name, params) {
    var result = null;
    try {
      result = emit(name, newId(), params);
    } catch (err) {
      debug("track failed", name, err);
    }
    return result;
  }

  /* Idempotent: the first caller creates and stores the event_id, every later
   * caller for the same logical key gets that same id back and nothing is
   * pushed again. */
  function trackOnce(key, name, params) {
    try {
      var existing = getEventId(key);
      if (existing) {
        debug("duplicate suppressed", name, key);
        return { name: name, event_id: existing, params: params || {}, duplicate: true };
      }
      var id = newId();
      rememberEventId(key, id);
      return emit(name, id, params);
    } catch (err) {
      debug("trackOnce failed", name, err);
      return null;
    }
  }

  /* =====================================================================
   * Named events
   * ================================================================== */

  function pageView() {
    var page = currentPage();
    return trackOnce("page_view:" + page.path, EVENTS.PAGE_VIEW, {
      page_path: page.path,
      page_title: page.title
    });
  }

  function serviceView(service, params) {
    var slug = clip(service || "unknown", MAX_FIELD);
    var extra = params && typeof params === "object" ? params : {};
    extra.service = slug;
    return trackOnce("service_view:" + slug, EVENTS.SERVICE_VIEW, extra);
  }

  function registerStart(params) {
    return trackOnce("register_start", EVENTS.REGISTER_START, params);
  }

  /* Never twice for the same lead: the key includes the lead_id, and if the
   * server did not return one the key falls back to a per-session constant. */
  function leadSuccess(leadId, params) {
    var key = "viporder_lead_success:" + clip(leadId || "session", MAX_FIELD);
    var extra = params && typeof params === "object" ? params : {};
    extra.lead_id = leadId || null;
    return trackOnce(key, EVENTS.LEAD_SUCCESS, extra);
  }

  function leadPending(leadId, params) {
    var key = "viporder_lead_pending:" + clip(leadId || "session", MAX_FIELD);
    var extra = params && typeof params === "object" ? params : {};
    extra.lead_id = leadId || null;
    return trackOnce(key, EVENTS.LEAD_PENDING, extra);
  }

  /* Keyed by attempt + status code, so retrying the same attempt with the same
   * outcome does not double-count but a genuinely different failure does. */
  function registerFailed(params) {
    var extra = params && typeof params === "object" ? params : {};
    var key = "register_failed:" + clip(extra.attempt_id || "session", MAX_FIELD) +
      ":" + clip(extra.status_code || extra.error_code || "unknown", 40);
    return trackOnce(key, EVENTS.REGISTER_FAILED, extra);
  }

  function phoneClick(params) {
    return track(EVENTS.PHONE_CLICK, params);
  }

  function zaloClick(params) {
    return track(EVENTS.ZALO_CLICK, params);
  }

  /* G04B — the public tracking lookup.
   *
   * TWO FIELDS, AND THE KEYWORD IS NOT ONE OF THEM. What a customer types into the
   * lookup is their tracking code, which identifies a shipment and through it a
   * person; this layer carries no customer data at all, and the tripwire for that
   * is tools/js/no-pii-in-analytics.test.js. The guarantee here is STRUCTURAL:
   * there is no parameter on this function that could carry the code, so no caller
   * can pass one by accident. Widening the signature is the thing to refuse.
   *
   * Fired with `track`, not `trackOnce`. Every other named event above means "this
   * visitor has now done X" and is de-duplicated per session; a lookup is an
   * ACTION. Somebody checking the same parcel five times searched five times, and
   * collapsing that would erase the only signal that the section is being used. */
  function oneOf(allowed, value) {
    for (var i = 0; i < allowed.length; i += 1) {
      if (allowed[i] === value) {
        return allowed[i];
      }
    }
    return "unknown";
  }

  function trackingSearch(searchType, result) {
    return track(EVENTS.TRACKING_SEARCH, {
      search_type: oneOf(TRACKING_SEARCH_TYPES, searchType),
      result: oneOf(TRACKING_RESULTS, result)
    });
  }

  /* =====================================================================
   * Dormant third-party loaders. Injected only when explicitly enabled, only
   * after window load, only on idle, and each one isolated in try/catch.
   * ================================================================== */

  function injectScriptOnce(elementId, url) {
    try {
      if (document.getElementById(elementId)) {
        return null;
      }
      var el = document.createElement("script");
      el.id = elementId;
      el.async = true;
      el.src = url;
      (document.head || document.documentElement).appendChild(el);
      debug("injected", elementId);
      return el;
    } catch (err) {
      debug("script injection failed", url, err);
      return null;
    }
  }

  function loadGtm() {
    if (!VIPORDER_TRACKING.gtm.enabled || !VIPORDER_TRACKING.gtm.id) {
      return;
    }
    dataLayerPush({ "gtm.start": new Date().getTime(), event: "gtm.js" });
    injectScriptOnce(
      "viporder-gtm",
      "https://www.googletagmanager.com/gtm.js?id=" +
        encodeURIComponent(VIPORDER_TRACKING.gtm.id)
    );
  }

  function loadGa4() {
    if (!VIPORDER_TRACKING.ga4.enabled || !VIPORDER_TRACKING.ga4.id) {
      return;
    }
    /* gtag.js drains these commands from dataLayer once it loads, so no
     * generated inline <script> is needed (keeps the page CSP-friendly). */
    dataLayerPush(["js", new Date()]);
    dataLayerPush(["config", VIPORDER_TRACKING.ga4.id, {
      anonymize_ip: true,
      send_page_view: false
    }]);
    injectScriptOnce(
      "viporder-ga4",
      "https://www.googletagmanager.com/gtag/js?id=" +
        encodeURIComponent(VIPORDER_TRACKING.ga4.id)
    );
  }

  function loadMetaPixel() {
    if (!VIPORDER_TRACKING.metaPixel.enabled || !VIPORDER_TRACKING.metaPixel.id) {
      return;
    }
    if (typeof window.fbq === "function") {
      window.fbq("init", VIPORDER_TRACKING.metaPixel.id);
      window.fbq("track", "PageView");
      return;
    }
    var fbq = function () {
      if (fbq.callMethod) {
        fbq.callMethod.apply(fbq, arguments);
      } else {
        fbq.queue.push(arguments);
      }
    };
    window.fbq = fbq;
    if (!window._fbq) {
      window._fbq = fbq;
    }
    fbq.push = fbq;
    fbq.loaded = true;
    fbq.version = "2.0";
    fbq.queue = [];
    fbq("init", VIPORDER_TRACKING.metaPixel.id);
    fbq("track", "PageView");
    injectScriptOnce("viporder-meta-pixel", "https://connect.facebook.net/en_US/fbevents.js");
  }

  function loadDormantTracking() {
    if (!VIPORDER_TRACKING.enabled) {
      debug("third-party tracking is dormant (VIPORDER_TRACKING.enabled = false)");
      return;
    }
    try { loadGtm(); } catch (err) { debug("GTM loader failed", err); }
    try { loadGa4(); } catch (err) { debug("GA4 loader failed", err); }
    try { loadMetaPixel(); } catch (err) { debug("Meta Pixel loader failed", err); }
  }

  /* Run after first paint. requestIdleCallback where available, otherwise the
   * earliest possible macrotask after `load`. */
  function whenIdle(fn) {
    try {
      var run = function () {
        try {
          fn();
        } catch (err) {
          debug("deferred task failed", err);
        }
      };
      var schedule = function () {
        if (typeof window.requestIdleCallback === "function") {
          window.requestIdleCallback(run, { timeout: 3000 });
        } else {
          window.setTimeout(run, 1);
        }
      };
      if (document.readyState === "complete") {
        schedule();
      } else {
        window.addEventListener("load", schedule, { once: true });
      }
    } catch (err) {
      debug("whenIdle failed", err);
    }
  }

  /* =====================================================================
   * Boot
   * ================================================================== */

  function init() {
    try {
      captureAttribution();
      pageView();
      whenIdle(loadDormantTracking);
    } catch (err) {
      debug("init failed", err);
    }
    return api;
  }

  var api = {
    version: VERSION,
    eventNames: EVENTS,
    config: VIPORDER_TRACKING,
    init: init,
    track: track,
    trackOnce: trackOnce,
    pageView: pageView,
    serviceView: serviceView,
    registerStart: registerStart,
    leadSuccess: leadSuccess,
    leadPending: leadPending,
    registerFailed: registerFailed,
    phoneClick: phoneClick,
    zaloClick: zaloClick,
    trackingSearch: trackingSearch,
    getEventId: getEventId,
    getAttribution: getAttribution,
    toApiAttribution: toApiAttribution,
    captureAttribution: captureAttribution,
    newId: newId,
    uuid: newId,
    whenIdle: whenIdle
  };

  window.VIPOrderAnalytics = api;

  try {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  } catch (err) {
    debug("bootstrap failed", err);
  }
}(window, document));
