/*!
 * VIPORDER — page behaviour (gates G07 + G05 wiring).
 * https://viporder.com.vn
 *
 * Responsibilities
 * ----------------
 *   * footer year (unchanged from G01)
 *   * accessible mobile navigation: a real <button> with aria-expanded /
 *     aria-controls, a <nav> landmark, keyboard operable, closes on Escape and
 *     on link activation. Pure CSS + JS, no framework.
 *   * contact tracking hooks (phone_click / zalo_click)
 *   * service_view reporting
 *
 * No inline handlers, no inline scripts: everything is bound from here so the
 * site stays compatible with a strict Content-Security-Policy.
 */
(function (window, document) {
  "use strict";

  var MOBILE_QUERY = "(max-width: 980px)";

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

  function analytics() {
    try {
      return window.VIPOrderAnalytics || null;
    } catch (err) {
      return null;
    }
  }

  /* Every analytics call is optional: if analytics.js failed to load, the page
   * must still work perfectly. */
  function fire(method, params) {
    var api = analytics();
    if (api && typeof api[method] === "function") {
      try {
        return api[method](params);
      } catch (err) {
        debug(method + " failed", err);
      }
    }
    return null;
  }

  /* ------------------------------------------------------------ footer year */

  var yearEl = document.getElementById("year");
  if (yearEl) {
    yearEl.textContent = String(new Date().getFullYear());
  }

  /* ------------------------------------------------------- mobile navigation */

  var header = document.querySelector(".site-header");
  var toggle = document.getElementById("navToggle");
  var nav = document.getElementById("primaryNav");

  function isMobileViewport() {
    try {
      if (typeof window.matchMedia === "function") {
        return window.matchMedia(MOBILE_QUERY).matches;
      }
    } catch (err) {
      debug("matchMedia unavailable", err);
    }
    return window.innerWidth <= 980;
  }

  function isMenuOpen() {
    return !!(header && header.classList.contains("nav-open"));
  }

  function openMenu() {
    if (!header || !toggle || isMenuOpen()) {
      return;
    }
    header.classList.add("nav-open");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Đóng menu");
    /* Move focus into the panel so the menu is reachable by keyboard. */
    if (nav && isMobileViewport()) {
      var firstLink = nav.querySelector("a");
      if (firstLink && typeof firstLink.focus === "function") {
        try {
          firstLink.focus();
        } catch (err) {
          debug("could not focus the first menu item", err);
        }
      }
    }
  }

  function closeMenu(returnFocus) {
    if (!header || !toggle) {
      return;
    }
    if (isMenuOpen()) {
      header.classList.remove("nav-open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Mở menu");
    }
    if (returnFocus && typeof toggle.focus === "function") {
      try {
        toggle.focus();
      } catch (err) {
        debug("could not return focus to the menu button", err);
      }
    }
  }

  function findAnchor(node, stopAt) {
    while (node && node !== stopAt) {
      if (node.tagName === "A") {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }

  if (header && toggle && nav) {
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      if (isMenuOpen()) {
        closeMenu(true);
      } else {
        openMenu();
      }
    });

    /* Close on link activation (the menu covers the page). */
    nav.addEventListener("click", function (event) {
      if (findAnchor(event.target, nav)) {
        closeMenu(false);
      }
    });

    document.addEventListener("keydown", function (event) {
      var key = event.key;
      var code = event.keyCode;
      if ((key === "Escape" || key === "Esc" || code === 27) && isMenuOpen()) {
        closeMenu(true);
      }
    });

    /* Close when the click lands outside the header. Clicks inside the header
     * are handled by the toggle / nav listeners above. */
    document.addEventListener("click", function (event) {
      if (!isMenuOpen()) {
        return;
      }
      var node = event.target;
      while (node) {
        if (node === header) {
          return;
        }
        node = node.parentNode;
      }
      closeMenu(false);
    });

    /* The panel only exists below the breakpoint: reset when it stops applying. */
    try {
      if (typeof window.matchMedia === "function") {
        var query = window.matchMedia(MOBILE_QUERY);
        var onBreakpointChange = function (event) {
          if (!event.matches) {
            closeMenu(false);
          }
        };
        if (typeof query.addEventListener === "function") {
          query.addEventListener("change", onBreakpointChange);
        } else if (typeof query.addListener === "function") {
          query.addListener(onBreakpointChange);
        }
      }
    } catch (err) {
      debug("could not watch the mobile breakpoint", err);
    }
  } else {
    debug("mobile navigation not mounted: markup missing");
  }

  /* ------------------------------------------------- phone_click / zalo_click
   *
   * These hooks are live, but index.html contains no tel: or Zalo link yet,
   * because VIPORDER has not published a support number and inventing one is
   * not acceptable. Nothing fires until a real link exists.
   *
   * TODO(g07-content): when the business supplies the real number, add
   *     <a href="tel:+84XXXXXXXXX" data-viporder-contact="phone">Hotline …</a>
   *     <a href="https://zalo.me/XXXXXXXXX" data-viporder-contact="zalo"
   *        rel="noopener" target="_blank">Chat Zalo</a>
   * and the events below start reporting with no further code change.
   */
  function contactKind(anchor) {
    try {
      var declared = anchor.getAttribute("data-viporder-contact");
      if (declared === "phone" || declared === "zalo") {
        return declared;
      }
      var href = anchor.getAttribute("href") || "";
      if (/^tel:/i.test(href)) {
        return "phone";
      }
      if (/^https?:\/\/([a-z0-9-]+\.)*zalo\.(me|com)\//i.test(href)) {
        return "zalo";
      }
    } catch (err) {
      debug("could not classify a contact link", err);
    }
    return null;
  }

  document.addEventListener("click", function (event) {
    var anchor = findAnchor(event.target, document);
    if (!anchor) {
      return;
    }
    var kind = contactKind(anchor);
    var href = anchor.getAttribute("href") || "";
    if (kind === "phone") {
      fire("phoneClick", { href: href, placement: "site" });
    } else if (kind === "zalo") {
      fire("zaloClick", { href: href, placement: "site" });
    }
  }, true);

  /* -------------------------------------------------------------- service_view
   *
   * `service_view` fires once per service per session. IntersectionObserver
   * gives a genuine "viewed" signal; the click listener covers browsers without
   * it and doubles as a safety net. Both routes go through the same idempotent
   * call, so the event cannot double-count.
   */
  function reportServiceView(card, placement) {
    var service = card.getAttribute("data-service");
    if (!service) {
      return;
    }
    fire("serviceView", { service: service, placement: placement });
  }

  var serviceCards = document.querySelectorAll(".service-card[data-service]");

  if (serviceCards.length) {
    document.addEventListener("click", function (event) {
      var node = event.target;
      while (node && node !== document) {
        if (node.getAttribute && node.getAttribute("data-service") &&
            node.className && String(node.className).indexOf("service-card") !== -1) {
          reportServiceView(node, "services-click");
          return;
        }
        node = node.parentNode;
      }
    }, true);

    try {
      if (typeof window.IntersectionObserver === "function") {
        var observer = new window.IntersectionObserver(function (entries) {
          for (var i = 0; i < entries.length; i += 1) {
            if (entries[i].isIntersecting) {
              reportServiceView(entries[i].target, "services");
              observer.unobserve(entries[i].target);
            }
          }
        }, { threshold: 0.5 });
        for (var j = 0; j < serviceCards.length; j += 1) {
          observer.observe(serviceCards[j]);
        }
      }
    } catch (err) {
      debug("IntersectionObserver unavailable; service_view falls back to clicks", err);
    }
  }
}(window, document));
