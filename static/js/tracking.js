/*!
 * VIPORDER — public tracking lookup controller (gate G04B).
 * https://viporder.com.vn
 *
 * Talks to GET /api/tracking/warehouse-imports/{keyword} and
 *          GET /api/tracking/package-sealings/{keyword}
 * (same origin; the dev server proxies /api to the backend, exactly like nginx).
 *
 * Guarantees this file is responsible for
 * ---------------------------------------
 * 1. NO innerHTML, ANYWHERE. Every string that comes from the provider is written
 *    with textContent on a node created here. Third-party tracking data is
 *    untrusted input: a `product_name_vi` is an arbitrary string typed into
 *    somebody else's warehouse system, and it must never reach the HTML parser.
 * 2. THE KEYWORD IS VALIDATED BEFORE IT BECOMES A URL, and encoded when it does.
 *    Both decisions live in static/js/tracking-search.js so they can be executed
 *    by `node --test`; this file only calls them.
 * 3. EVERY STATE IS RENDERED: idle, loading, found, not found, invalid, provider
 *    error. There is no path that leaves the customer looking at a spinner.
 * 4. THE LOOKUP NEVER BLOCKS THE PAGE. Every public entry point is wrapped, and a
 *    failure anywhere becomes the provider-error state rather than an exception
 *    that leaves the section dead.
 *
 * It does NOT carry the keyword into analytics, and it does not log it. See
 * `fireSearch` at the bottom of the analytics section.
 */
(function (window, document) {
  "use strict";

  /* The rules. A plain deferred script loaded before this one, and a CommonJS
   * export, so there is ONE copy of the keyword boundary and of the HTTP-to-state
   * mapping. */
  var rules =
    typeof window !== "undefined" && window.VipOrderTrackingSearch
      ? window.VipOrderTrackingSearch
      : null;

  var LOG_PREFIX = "[viporder]";

  /* How long the customer is left looking at "Đang tra cứu…" before we decide the
   * lookup is not coming back. The backend has its own, shorter provider timeout;
   * this one is the outer bound, and it exists because a hung request would
   * otherwise leave the button disabled forever — a dead section with no message,
   * which is the worst of the six states (it is not one of them).
   *
   * 20s is deliberately longer than any network hiccup and shorter than the point
   * where a person gives up and reloads. If AbortController is unavailable the
   * timeout is simply not armed; the button is released by the response. */
  var TIMEOUT_MS = 20000;

  var BUSY_LABEL = "Đang tra cứu…";

  var form = document.getElementById("trackingForm");
  var input = document.getElementById("trackingKeyword");
  var submitBtn = document.getElementById("trackingSubmit");
  var statusEl = document.getElementById("trackingStatus");
  var resultsEl = document.getElementById("trackingResults");

  /* A page without the section is a normal page (this file is loaded site-wide),
   * so a missing node means there is simply nothing to do. */
  if (!form || !input || !submitBtn || !statusEl || !resultsEl) {
    return;
  }

  /* A section WITHOUT the rules module is a mis-deployed page, and it is the one
   * case where the honest answer is to refuse to work: the module owns the keyword
   * boundary and the status mapping, so a controller without it cannot build a
   * safe URL. `check_site.py` fails the build for a missing asset and
   * tools/js/tracking-search.test.js pins the load order, but if it ever slips
   * through, the customer gets a sentence instead of a button that silently does
   * nothing. */
  if (!rules) {
    setStatus(
      "Hệ thống tra cứu đang tạm bận hoặc không phản hồi. Vui lòng thử lại sau ít phút.",
      "error"
    );
    submitBtn.disabled = true;
    return;
  }

  var modeHost = document.getElementById("trackingModes");
  var modeButtons = modeHost ? modeHost.querySelectorAll("[data-mode]") : null;
  var baseLabel = String(submitBtn.textContent || "").trim();
  var mode = rules.MODES.WAREHOUSE;
  var inFlight = false;
  var timer = null;

  var FOUND_MESSAGE = "Đã tìm thấy thông tin cho mã này.";

  /* ------------------------------------------------------------------ utils */

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

  function analytics() {
    try {
      return window.VIPOrderAnalytics || null;
    } catch (err) {
      return null;
    }
  }

  /* -------------------------------------------------------------- rendering */

  function setStatus(text, kind) {
    statusEl.className = "tracking-status" + (kind ? " is-" + kind : "");
    statusEl.textContent = text == null ? "" : String(text);
  }

  /* Terminal states move focus to the live region the same way the registration
   * form does: role="status" announces a change politely, and a customer who is
   * on a phone with the keyboard open gets the answer in view rather than below
   * the fold. LOADING deliberately does NOT take focus — stealing it mid-typing
   * would close the on-screen keyboard. */
  function focusStatus() {
    try {
      statusEl.focus();
    } catch (err) {
      debug("could not focus the tracking status region", err);
    }
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null && text !== "") {
      /* textContent, never innerHTML — this is the line the security posture of
       * this section rests on. */
      node.textContent = String(text);
    }
    return node;
  }

  function appendRows(host, rows) {
    if (!rows.length) {
      return;
    }
    var dl = el("dl", "tracking-fields");
    for (var i = 0; i < rows.length; i += 1) {
      /* dt/dd are direct children and the stylesheet lays each PAIR out in one
       * grid row. A <div> wrapper would work too but would need
       * `display: contents`, which is worth avoiding inside a <dl>. */
      dl.appendChild(el("dt", null, rows[i].label));
      dl.appendChild(el("dd", null, rows[i].value));
    }
    host.appendChild(dl);
  }

  function renderLogs(view) {
    if (!view.logs.length) {
      return;
    }
    var section = el("div", "tracking-block");
    section.appendChild(el("h4", null, view.logsLabel));
    var list = el("ul", "tracking-logs");
    for (var i = 0; i < view.logs.length; i += 1) {
      var log = view.logs[i];
      var item = el("li");
      /* The status string is written VERBATIM, including its Chinese half. This
       * is provider text and it is not ours to translate. */
      if (log.status) {
        item.appendChild(el("strong", null, log.status));
      }
      if (log.note) {
        item.appendChild(el("span", "tracking-log-note", log.note));
      }
      if (log.created_at) {
        item.appendChild(el("span", "tracking-log-time", log.created_at));
      }
      list.appendChild(item);
    }
    section.appendChild(list);
    resultsEl.appendChild(section);
  }

  function renderChildren(view) {
    if (!view.children.length) {
      return;
    }
    var section = el("div", "tracking-block");
    section.appendChild(el("h4", null, view.childrenLabel));
    var list = el("ol", "tracking-children");
    for (var i = 0; i < view.children.length; i += 1) {
      var item = el("li");
      appendRows(item, view.children[i].fields);
      list.appendChild(item);
    }
    section.appendChild(list);
    resultsEl.appendChild(section);
  }

  function renderFound(resolvedMode, data) {
    var view = rules.viewFor(resolvedMode, data);

    clear(resultsEl);
    resultsEl.appendChild(el("h3", null, "Kết quả tra cứu"));
    /* The status row is already part of view.fields, in the position the agreed
     * render order puts it — which is NOT the same position in both modes. */
    appendRows(resultsEl, view.fields);
    renderLogs(view);
    renderChildren(view);
    /* A found record with nothing renderable in it is still a found record — the
     * heading says so, and an empty card is more honest than "not found". */
    resultsEl.removeAttribute("hidden");
  }

  /* ------------------------------------------------------------------ states */

  function showInvalid(message) {
    input.setAttribute("aria-invalid", "true");
    setStatus(message, "error");
    resultsEl.setAttribute("hidden", "hidden");
    clear(resultsEl);
    focusStatus();
  }

  function showState(state, message, resolvedMode, data) {
    if (state === rules.STATE.FOUND) {
      input.removeAttribute("aria-invalid");
      setStatus(message || FOUND_MESSAGE, "found");
      renderFound(resolvedMode, data);
      focusStatus();
      return;
    }
    input.removeAttribute("aria-invalid");
    resultsEl.setAttribute("hidden", "hidden");
    clear(resultsEl);
    /* not_found is amber-ish rather than red: nothing is broken and nothing was
     * lost, the customer just needs to check the code. Everything else that is not
     * "found" is an error and reads as one. */
    setStatus(message, state === rules.STATE.NOT_FOUND ? "notfound" : "error");
    focusStatus();
  }

  function setBusy(busy) {
    inFlight = !!busy;
    submitBtn.disabled = !!busy;
    if (busy) {
      submitBtn.setAttribute("aria-busy", "true");
      submitBtn.textContent = BUSY_LABEL;
    } else {
      submitBtn.removeAttribute("aria-busy");
      submitBtn.textContent = baseLabel;
    }
  }

  /* One place decides which sentence belongs to which state, so no state can
   * reach the customer with another state's wording. */
  var MESSAGE_FOR_STATE = {};
  MESSAGE_FOR_STATE[rules.STATE.FOUND] = FOUND_MESSAGE;
  MESSAGE_FOR_STATE[rules.STATE.NOT_FOUND] = rules.MESSAGES.NOT_FOUND;
  MESSAGE_FOR_STATE[rules.STATE.INVALID] = rules.MESSAGES.INVALID;
  MESSAGE_FOR_STATE[rules.STATE.PROVIDER_ERROR] = rules.MESSAGES.PROVIDER_ERROR;

  /* --------------------------------------------------------------- analytics */

  /* ONE event per search outcome, carrying two fields and nothing else.
   *
   * THE KEYWORD IS NOT SENT, AND THERE IS NO PARAMETER HERE THAT COULD CARRY IT.
   * A tracking code identifies a shipment and, through it, a person; the
   * first-party layer is under a standing rule that it carries no customer data
   * (tools/js/no-pii-in-analytics.test.js is the tripwire). The same structural
   * argument applies: this function has no argument to leak.
   *
   * The event is also not de-duplicated. `trackOnce` exists for events that mean
   * "this customer has now done X" (a page view, a registration); a lookup is an
   * ACTION, and somebody checking the same parcel five times has searched five
   * times. Collapsing those would delete the only signal that says the section is
   * being used. */
  function fireSearch(searchType, result) {
    if (!result) {
      return;
    }
    var api = analytics();
    if (api && typeof api.trackingSearch === "function") {
      try {
        api.trackingSearch(searchType, result);
      } catch (err) {
        debug("tracking_search failed", err);
      }
    }
  }

  /* ------------------------------------------------------------------ lookup */

  function readJson(text) {
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch (err) {
      /* Not JSON at all. interpret() turns that into the provider-error state,
       * which is the truthful answer: we cannot read the reply. */
      debug("tracking response was not JSON");
      return null;
    }
  }

  function finishOutcome(searchMode, status, body) {
    var outcome = rules.interpret(searchMode, status, body);
    if (outcome.state === rules.STATE.INVALID) {
      /* The server refused a keyword our own boundary accepted. The customer's
       * next move is the same as for a local refusal — check the code — so it is
       * the same UI. The contract code is kept in the console log so a genuine
       * disagreement between the two boundaries can be diagnosed, and it is NOT
       * shown: the server's sentence could be a relayed provider string. */
      debug("tracking keyword refused by the server", outcome.code);
      showInvalid(MESSAGE_FOR_STATE[rules.STATE.INVALID]);
    } else {
      if (outcome.state === rules.STATE.PROVIDER_ERROR) {
        debug("tracking lookup failed", outcome.code);
      }
      showState(outcome.state, MESSAGE_FOR_STATE[outcome.state], outcome.mode, outcome.data);
    }
    fireSearch(outcome.mode, rules.resultForState(outcome.state));
  }

  function onSubmit(event) {
    event.preventDefault();
    if (inFlight) {
      return;
    }

    var checked = rules.checkKeyword(input.value);
    input.removeAttribute("aria-invalid");
    if (!checked.ok) {
      /* REJECTED LOCALLY: no request is made, which is the whole point of the
       * boundary. The message is the one the rule chose, so the customer reads why
       * rather than a generic refusal. */
      showInvalid(checked.message);
      fireSearch(mode, rules.resultForState(rules.STATE.INVALID));
      return;
    }

    var path = rules.keywordPath(mode, checked.keyword);

    if (typeof window.fetch !== "function") {
      finishOutcome(mode, 0, null);
      return;
    }

    setBusy(true);
    setStatus(rules.MESSAGES.LOADING, "loading");
    resultsEl.setAttribute("hidden", "hidden");
    clear(resultsEl);

    var options = {
      method: "GET",
      headers: { "Accept": "application/json" },
      credentials: "same-origin"
    };

    /* The timeout is bound to THIS request's controller by identity, not through a
     * shared variable: the timer outlives the response, and a shared handle let an
     * earlier search's timer abort a later request. */
    var localController = null;
    if (typeof window.AbortController === "function") {
      localController = new window.AbortController();
      options.signal = localController.signal;
      timer = window.setTimeout(function () {
        if (localController && !localController.signal.aborted) {
          debug("tracking lookup timed out after " + TIMEOUT_MS + "ms");
          localController.abort();
        }
      }, TIMEOUT_MS);
    }

    function settle() {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      setBusy(false);
    }

    try {
      window.fetch(path, options).then(function (response) {
        return response.text().then(
          function (text) { return { status: response.status, text: text }; },
          function () { return { status: response.status, text: "" }; }
        );
      }).then(function (result) {
        settle();
        finishOutcome(mode, result.status, readJson(result.text));
      }).catch(function (err) {
        settle();
        /* A network failure, an abort, or anything thrown while reading the body.
         * None of them is evidence about the parcel, so all of them are the
         * provider-error state — never "not found". */
        debug("tracking request failed", err);
        finishOutcome(mode, 0, null);
      });
    } catch (err) {
      settle();
      debug("tracking request could not be started", err);
      finishOutcome(mode, 0, null);
    }
  }

  /* -------------------------------------------------------------------- mode */

  function setMode(next) {
    mode = rules.normaliseMode(next);
    for (var i = 0; i < modeButtons.length; i += 1) {
      var button = modeButtons[i];
      var active = button.getAttribute("data-mode") === mode;
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.className = active ? "tracking-mode is-active" : "tracking-mode";
    }
    /* Switching mode clears the answer: a warehouse record left on screen under
     * the "Tra mã bao" tab is a wrong answer, not a stale one. The keyword stays,
     * because the customer is usually pasting the same code into the other mode. */
    input.removeAttribute("aria-invalid");
    resultsEl.setAttribute("hidden", "hidden");
    clear(resultsEl);
    setStatus(rules.MESSAGES.IDLE, "");
  }

  /* -------------------------------------------------------------------- init */

  /* The browser's own validation UI is switched off from JS, not from the markup,
   * so a browser with JavaScript disabled still gets native `required` as a
   * fallback — the same arrangement as the registration form. */
  form.setAttribute("novalidate", "novalidate");

  /* No keydown handler for Enter: the input sits in a <form> that has a submit
   * button, so implicit submission already works and re-implementing it would add
   * a second path that can drift from the first. */
  form.addEventListener("submit", onSubmit);

  if (modeButtons) {
    for (var i = 0; i < modeButtons.length; i += 1) {
      (function (button) {
        button.addEventListener("click", function () {
          setMode(button.getAttribute("data-mode"));
        });
      }(modeButtons[i]));
    }
  }

  setBusy(false);
  setMode(mode);
}(window, document));
