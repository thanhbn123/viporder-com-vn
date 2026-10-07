/* Visual properties that can be MEASURED, because they cannot be seen here.
 *
 * WHY THIS FILE EXISTS, and what it does not do.
 *
 * Workstream (B) asks for a visual review. The honest position: this work was done
 * by an agent with NO IMAGE INPUT, so nobody has looked at a rendered pixel. Rather
 * than write "reviewed" against a screenshot nobody opened, this file measures the
 * things a visual review is actually looking for, where they are objectively
 * checkable:
 *
 *   - CONTRAST: WCAG AA needs 4.5:1 for normal text and 3:1 for large text. This is
 *     computed from the real rendered colours, including inherited backgrounds.
 *   - TYPE SIZE: a body size below 14px is hard to read on a phone in daylight.
 *   - FOCUS: a keyboard user must be able to see where they are.
 *
 * WHAT IT CANNOT SEE, stated so nobody reads more into a green run than exists:
 * alignment, spacing rhythm, whether a layout looks *right*, icon quality, or
 * anything about the brand. A machine can prove the text is legible; it cannot
 * prove the page is pleasant.
 */

"use strict";

const { test, expect } = require("@playwright/test");

/* sRGB relative luminance, per WCAG 2.x. */
function luminance([r, g, b]) {
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fg, bg) {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

/* Walk every element that renders text, work out its effective foreground and
 * background, and return the ones below AA. Computed in the page because that is
 * the only place the cascade and the inherited background are known. */
const AUDIT = () => {
  const parse = (c) => {
    const m = (c || "").match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(",").map((v) => parseFloat(v.trim()));
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const ownText = (el) =>
    Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join(" ")
      .trim();

  const results = [];
  const walk = (el) => {
    if (el.nodeType !== 1) return;
    const tag = el.tagName.toLowerCase();
    if (["script", "style", "noscript", "svg", "path"].includes(tag)) return;
    if (visible(el)) {
      const text = ownText(el);
      if (text) {
        const s = getComputedStyle(el);
        // Effective background: walk up until something is not transparent.
        let node = el;
        let bg = null;
        while (node && !bg) {
          const c = parse(getComputedStyle(node).backgroundColor);
          if (c && c.a > 0.5) bg = c.rgb;
          node = node.parentElement;
        }
        if (!bg) bg = [255, 255, 255];
        const fg = parse(s.color);
        if (fg) {
          results.push({
            tag,
            cls: (el.className || "").toString().slice(0, 40),
            text: text.slice(0, 40),
            fg: fg.rgb,
            bg,
            size: parseFloat(s.fontSize),
            weight: s.fontWeight,
            color: s.color,
            background: bg,
          });
        }
      }
    }
    for (const c of el.children) walk(c);
  };
  walk(document.body);
  return results;
};

test.describe("visual properties", () => {
  test("body text meets WCAG AA contrast", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    const samples = await page.evaluate(AUDIT);

    const failures = [];
    for (const s of samples) {
      const c = contrast(s.fg, s.bg);
      const large = s.size >= 24 || (s.size >= 18.66 && parseInt(s.weight, 10) >= 700);
      const need = large ? 3 : 4.5;
      if (c < need) {
        failures.push(
          `${c.toFixed(2)}:1 (needs ${need}) <${s.tag} class="${s.cls}"> "${s.text}" ` +
            `fg=${s.color} bg=rgb(${s.bg.join(",")}) ${s.size}px`,
        );
      }
    }
    expect(failures, `text below AA:\n${failures.join("\n")}`).toEqual([]);
  });

  test("no rendered body text is smaller than 12px", async ({ page }) => {
    /* 14px is the comfortable floor; 12px is the point at which a machine should
     * object on behalf of a phone in daylight. */
    await page.goto("/", { waitUntil: "networkidle" });
    const samples = await page.evaluate(AUDIT);
    const tiny = samples.filter((s) => s.size < 12).map((s) => `${s.size}px <${s.tag}> "${s.text}"`);
    expect(tiny, `text below 12px:\n${tiny.join("\n")}`).toEqual([]);
  });

  test("a keyboard user can see where they are", async ({ page }) => {
    /* A focus ring removed by a CSS reset is invisible in every screenshot, and it
     * makes the page unusable without a mouse. Measured: focus a control and check
     * that SOMETHING about its rendering changed. */
    await page.goto("/", { waitUntil: "networkidle" });
    await page.locator("#tracking").scrollIntoViewIfNeeded();

    const before = await page.evaluate(() => {
      const el = document.querySelector("#trackingKeyword");
      const s = getComputedStyle(el);
      return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor].join("|");
    });
    await page.locator("#trackingKeyword").focus();
    const after = await page.evaluate(() => {
      const el = document.querySelector("#trackingKeyword");
      const s = getComputedStyle(el);
      return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor].join("|");
    });
    expect(after, "focusing the search input changed nothing visible").not.toBe(before);
  });

  test("every interactive control has an accessible name", async ({ page }) => {
    /* An icon-only button with no label is invisible to a screen reader and is the
     * single most common accessibility defect in a hand-built page. */
    await page.goto("/", { waitUntil: "networkidle" });
    const unnamed = await page.evaluate(() => {
      const out = [];
      for (const el of document.querySelectorAll("button, a[href], input, select")) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        if (r.width === 0 || r.height === 0 || s.visibility === "hidden") continue;
        const name =
          (el.getAttribute("aria-label") || "").trim() ||
          (el.textContent || "").trim() ||
          (el.getAttribute("title") || "").trim() ||
          (el.labels && el.labels.length ? el.labels[0].textContent.trim() : "") ||
          (el.getAttribute("placeholder") || "").trim();
        if (!name) out.push(`${el.tagName.toLowerCase()} name=${el.getAttribute("name")} id=${el.id} class=${el.className}`);
      }
      return out;
    });
    expect(unnamed, `controls with no accessible name:\n${unnamed.join("\n")}`).toEqual([]);
  });
});
