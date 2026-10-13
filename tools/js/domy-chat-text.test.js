/* Tests for static/js/domy-chat-text.js — which links in a DOMY reply become buttons.
 *
 * A DOMY reply is written by an AI model, so every URL in it is untrusted. Only an
 * https link to a VIP Order host may become a clickable button; anything else must
 * stay plain text. The delivery form gets its own labelled button.
 *
 * Run: node --test "tools/js/*.test.js"
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { splitMessage } = require("../../static/js/domy-chat-text.js");

const links = (text) => splitMessage(text).filter((p) => p.type === "link");
const plain = (text) => splitMessage(text).map((p) => p.text).join("");

test("the real delivery reply becomes a delivery button", () => {
  const reply =
    "Dạ, anh/chị đặt giao hàng tại đường link này giúp em ạ:\n" +
    "https://vanhanh.viporder.vn/d/AbC123_xyz\n\nCần hỗ trợ, anh/chị gọi 03.6263.1114 ạ.";
  const l = links(reply);
  assert.equal(l.length, 1);
  assert.equal(l[0].delivery, true);
  assert.equal(l[0].href, "https://vanhanh.viporder.vn/d/AbC123_xyz");
  assert.equal(l[0].text, "📦 Mở form đặt giao hàng");
});

test("the old /dat-giao-hang/ path is a delivery button too", () => {
  assert.equal(links("https://vanhanh.viporder.vn/dat-giao-hang/tok")[0].delivery, true);
});

test("a trusted non-delivery link is a plain link labelled with its address", () => {
  const l = links("Xem https://viporder.com.vn/#tracking nhé");
  assert.equal(l.length, 1);
  assert.equal(l[0].delivery, false);
  assert.equal(l[0].text, "viporder.com.vn");
});

test("foreign, look-alike and non-https links stay plain text", () => {
  for (const url of [
    "https://evil.example/d/abc",
    "https://vanhanh.viporder.vn.evil.example/d/abc",
    "https://viporder-com-vn.example/",
    "http://vanhanh.viporder.vn/d/abc",
    "https://user:pass@vanhanh.viporder.vn/d/abc",
    "https://vanhanh.viporder.vn:8443/d/abc",
    "javascript:alert(1)"
  ]) {
    assert.equal(links(`bấm ${url} đi`).length, 0, url);
    assert.ok(plain(`bấm ${url} đi`).includes(url.replace(/^javascript:/, "javascript:")), url);
  }
});

test("a delivery path on another trusted host is not a delivery button", () => {
  const l = links("https://zalo.me/d/abc");
  assert.equal(l.length, 1);
  assert.equal(l[0].delivery, false);
});

test("trailing punctuation is not swallowed into the link", () => {
  const parts = splitMessage("Link: https://vanhanh.viporder.vn/d/tok.");
  assert.equal(parts[parts.length - 1].text, ".");
  assert.equal(links("Link: https://vanhanh.viporder.vn/d/tok.")[0].href, "https://vanhanh.viporder.vn/d/tok");
});

test("text is preserved exactly around links and HTML is never produced", () => {
  const msg = "<b>xin chào</b> https://zalo.me/0968961962 tạm biệt";
  assert.equal(splitMessage(msg)[0].text, "<b>xin chào</b> ");
  assert.equal(splitMessage(msg).at(-1).text, " tạm biệt");
});

test("empty and non-string input give no parts", () => {
  assert.deepEqual(splitMessage(""), []);
  assert.deepEqual(splitMessage(null), []);
});

test("the widget loads the pure module before itself", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "..", "index.html"), "utf8");
  const a = html.indexOf("/static/js/domy-chat-text.js");
  const b = html.indexOf("/static/js/domy-chat.js");
  assert.ok(a > 0 && b > a);
});
