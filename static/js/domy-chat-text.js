/*!
 * VIPORDER — phần THUẦN của khung chat DOMY: tách câu trả lời thành chữ + link.
 *
 * Đây là ranh giới an toàn của khung chat: câu trả lời đến từ một mô hình AI, nên
 * mọi đường link trong đó đều coi là dữ liệu không tin cậy. Chỉ link https trỏ về
 * miền của VIP Order (TRUSTED_HOSTS) mới thành nút bấm; mọi thứ khác giữ nguyên là
 * chữ thường. Không có HTML nào được tạo ở đây — khung chat vẽ bằng textContent.
 *
 * Kiểm bằng: node --test "tools/js/*.test.js"
 */
(function (root) {
  "use strict";

  var TRUSTED_HOSTS = [
    "vanhanh.viporder.vn",
    "viporder.com.vn",
    "www.viporder.com.vn",
    "khachhang.viporder.com.vn",
    "zalo.me"
  ];
  var DELIVERY_PATH = /^\/(d|dat-giao-hang)\//;
  var URL_RE = /https?:\/\/[^\s<>"']+/g;
  var TRAILING = /[).,;:!?]+$/;

  function linkPart(raw) {
    var u;
    try {
      u = new URL(raw);
    } catch (e) {
      return { type: "text", text: raw };
    }
    if (u.protocol !== "https:" || u.username || u.password || u.port ||
        TRUSTED_HOSTS.indexOf(u.hostname) === -1) {
      return { type: "text", text: raw };
    }
    var delivery = u.hostname === "vanhanh.viporder.vn" && DELIVERY_PATH.test(u.pathname);
    return {
      type: "link",
      href: u.href,
      delivery: delivery,
      text: delivery ? "📦 Mở form đặt giao hàng" : u.hostname + (u.pathname.length > 1 ? u.pathname : "")
    };
  }

  function splitMessage(text) {
    var parts = [];
    if (typeof text !== "string" || !text) { return parts; }
    var last = 0;
    var m;
    URL_RE.lastIndex = 0;
    while ((m = URL_RE.exec(text)) !== null) {
      var url = m[0].replace(TRAILING, "");
      if (m.index > last) { parts.push({ type: "text", text: text.slice(last, m.index) }); }
      parts.push(linkPart(url));
      last = m.index + url.length;
      URL_RE.lastIndex = last;
    }
    if (last < text.length) { parts.push({ type: "text", text: text.slice(last) }); }
    return parts;
  }

  var api = { splitMessage: splitMessage, TRUSTED_HOSTS: TRUSTED_HOSTS.slice() };
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.DomyChatText = api;
  }
})(typeof window !== "undefined" ? window : this);
