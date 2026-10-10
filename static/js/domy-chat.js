/*!
 * VIPORDER — Trợ lý DOMY trên trang (khung chat nổi).
 *
 * Nói chuyện DUY NHẤT với backend của chính trang: /api/v1/chat/*.
 * Backend ký tin rồi chuyển sang DOMY; trình duyệt không bao giờ giữ khoá.
 *
 *   GET  /api/v1/chat/status    → {enabled, max_chars}   (tắt thì không vẽ gì)
 *   POST /api/v1/chat/messages  {visitor_id, content} → {conversation_id, token}
 *   GET  /api/v1/chat/poll?conversation_id&token&after → {messages, status, retry_after?}
 *
 * An toàn:
 *   * Mọi nội dung vẽ bằng textContent, không innerHTML.
 *   * Đường link trong câu trả lời CHỈ biến thành nút bấm khi trỏ về miền của
 *     VIP Order (TRUSTED_HOSTS trong domy-chat-text.js); link lạ giữ nguyên là chữ.
 *   * Không handler inline: tương thích CSP chặt của trang.
 */
(function (window, document) {
  "use strict";

  var API = "/api/v1/chat";
  var KHOA_LUU = "viporder-domy-chat-v1";
  var GIU_TOI_DA = 40;
  var LOI_CHAO =
    "Chào anh/chị! Em là DOMY, trợ lý của VIP Order. Anh/chị cần tra hàng, hỏi giá hay đặt giao hàng ạ?";
  var GOI_Y = [
    ["Giờ làm việc?", "Giờ làm việc bên mình thế nào?"],
    ["Tra hàng của tôi", "Tôi muốn tra hàng của mình đến đâu rồi"],
    ["Đặt giao hàng", "Tôi muốn đặt giao hàng"],
    ["Gặp nhân viên", "Cho tôi gặp nhân viên tư vấn"]
  ];

  var maxChars = 1000;
  var st = taiTrangThai();
  var el = {};
  var dangCho = false;
  var hanCho = 0;
  var hengio = null;
  var dangPoll = false;

  // ---------------------------------------------------------------- lưu trữ

  function taiTrangThai() {
    var mac = { visitor: "", conv: "", token: "", after: "", msgs: [], status: "" };
    try {
      var raw = window.localStorage.getItem(KHOA_LUU);
      if (raw) {
        var d = JSON.parse(raw);
        if (d && typeof d === "object") {
          mac.visitor = typeof d.visitor === "string" ? d.visitor : "";
          mac.conv = typeof d.conv === "string" ? d.conv : "";
          mac.token = typeof d.token === "string" ? d.token : "";
          mac.after = typeof d.after === "string" ? d.after : "";
          mac.status = typeof d.status === "string" ? d.status : "";
          mac.msgs = Array.isArray(d.msgs) ? d.msgs.slice(-GIU_TOI_DA) : [];
        }
      }
    } catch (e) {
      /* Trình duyệt chặn lưu trữ: chạy không nhớ, vẫn chat được. */
    }
    if (!/^[A-Za-z0-9-]{16,64}$/.test(mac.visitor)) {
      mac.visitor = taoMa();
    }
    return mac;
  }

  function luu() {
    try {
      st.msgs = st.msgs.slice(-GIU_TOI_DA);
      window.localStorage.setItem(KHOA_LUU, JSON.stringify(st));
    } catch (e) {
      /* không lưu được thì thôi */
    }
  }

  function taoMa() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
      var b = new Uint8Array(16);
      window.crypto.getRandomValues(b);
      return Array.prototype.map
        .call(b, function (x) { return ("0" + x.toString(16)).slice(-2); })
        .join("");
    } catch (e) {
      return "v" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    }
  }

  // ---------------------------------------------------------------- vẽ

  function tao(tag, lop, chu) {
    var n = document.createElement(tag);
    if (lop) { n.className = lop; }
    if (chu) { n.textContent = chu; }
    return n;
  }

  function veKhung() {
    var bao = tao("div", "domy");
    bao.id = "domyChat";

    var nut = tao("button", "domy-nut");
    nut.type = "button";
    nut.setAttribute("aria-expanded", "false");
    nut.setAttribute("aria-controls", "domyCua");
    nut.appendChild(tao("span", "domy-nut-cham"));
    nut.appendChild(tao("span", "", "Hỏi DOMY"));
    var huy = tao("span", "domy-badge");
    huy.hidden = true;
    nut.appendChild(huy);

    var cua = tao("section", "domy-cua");
    cua.id = "domyCua";
    cua.hidden = true;
    cua.setAttribute("role", "dialog");
    cua.setAttribute("aria-label", "Trợ lý DOMY");

    var dau = tao("div", "domy-dau");
    var ten = tao("div", "domy-ten");
    ten.appendChild(tao("strong", "", "Trợ lý DOMY"));
    ten.appendChild(tao("span", "domy-phu", "Trợ lý AI của VIP Order"));
    var moi = tao("button", "domy-dau-nut", "Mới");
    moi.type = "button";
    moi.title = "Bắt đầu cuộc trò chuyện mới";
    var dong = tao("button", "domy-dau-nut", "✕");
    dong.type = "button";
    dong.setAttribute("aria-label", "Đóng khung chat");
    dau.appendChild(ten);
    dau.appendChild(moi);
    dau.appendChild(dong);

    var than = tao("div", "domy-than");
    than.setAttribute("aria-live", "polite");

    var goiY = tao("div", "domy-goi-y");
    GOI_Y.forEach(function (g) {
      var b = tao("button", "", g[0]);
      b.type = "button";
      b.setAttribute("data-cau", g[1]);
      goiY.appendChild(b);
    });

    var form = tao("form", "domy-form");
    var o = tao("textarea", "domy-o");
    o.id = "domyNhap";
    o.rows = 1;
    o.maxLength = maxChars;
    o.placeholder = "Nhắn cho DOMY…";
    o.setAttribute("aria-label", "Nội dung tin nhắn");
    var gui = tao("button", "domy-gui", "Gửi");
    gui.type = "submit";
    form.appendChild(o);
    form.appendChild(gui);

    var chan = tao(
      "p",
      "domy-chan",
      "DOMY là trợ lý AI, có thể trả lời chưa chính xác. Việc gấp, anh/chị gọi hotline."
    );

    cua.appendChild(dau);
    cua.appendChild(than);
    cua.appendChild(goiY);
    cua.appendChild(form);
    cua.appendChild(chan);
    bao.appendChild(cua);
    bao.appendChild(nut);
    document.body.appendChild(bao);

    el = { bao: bao, nut: nut, badge: huy, cua: cua, than: than, goiY: goiY, form: form, o: o, gui: gui };

    nut.addEventListener("click", function () { batTat(true); });
    dong.addEventListener("click", function () { batTat(false); });
    moi.addEventListener("click", lamMoi);
    goiY.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-cau]") : null;
      if (b) { gui_(b.getAttribute("data-cau")); }
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      gui_(o.value);
    });
    o.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        gui_(o.value);
      }
    });
    o.addEventListener("input", coGian);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !cua.hidden) { batTat(false); nut.focus(); }
    });

    veLaiLichSu();
  }

  function coGian() {
    el.o.style.height = "auto";
    el.o.style.height = Math.min(el.o.scrollHeight, 120) + "px";
  }

  function veLaiLichSu() {
    el.than.textContent = "";
    veBong("domy", LOI_CHAO, true);
    st.msgs.forEach(function (m) { veBong(m.who, m.text, true); });
    el.goiY.hidden = st.msgs.length > 0;
  }

  function veBong(who, text, khongLuu) {
    var b = tao("div", "domy-tin domy-tin-" + who);
    if (who === "staff") {
      b.appendChild(tao("span", "domy-ai-noi", "Nhân viên VIP Order"));
    }
    veNoiDung(b, text);
    el.than.appendChild(b);
    el.than.scrollTop = el.than.scrollHeight;
    if (!khongLuu) {
      st.msgs.push({ who: who, text: text });
      luu();
    }
    return b;
  }

  // Tách văn bản thành chữ + link an toàn (domy-chat-text.js). Không bao giờ innerHTML.
  function veNoiDung(cha, text) {
    var tach = window.DomyChatText ? window.DomyChatText.splitMessage(text) : [{ type: "text", text: text }];
    tach.forEach(function (p) {
      if (p.type !== "link") {
        cha.appendChild(document.createTextNode(p.text));
        return;
      }
      var a = tao("a", p.delivery ? "domy-link domy-link-giao" : "domy-link", p.text);
      a.href = p.href;
      a.target = "_blank";
      a.rel = "noopener";
      cha.appendChild(a);
    });
  }

  function dangSoan(bat) {
    if (bat && !el.soan) {
      el.soan = tao("div", "domy-tin domy-tin-domy domy-soan", "DOMY đang soạn…");
      el.than.appendChild(el.soan);
      el.than.scrollTop = el.than.scrollHeight;
    } else if (!bat && el.soan) {
      el.soan.remove();
      el.soan = null;
    }
  }

  function baoHe(text) {
    var b = tao("div", "domy-tin domy-tin-he", text);
    el.than.appendChild(b);
    el.than.scrollTop = el.than.scrollHeight;
  }

  function batTat(mo) {
    el.cua.hidden = !mo;
    el.nut.hidden = mo;
    el.nut.setAttribute("aria-expanded", String(mo));
    if (mo) {
      el.badge.hidden = true;
      el.badge.textContent = "";
      el.o.focus();
      el.than.scrollTop = el.than.scrollHeight;
      if (st.conv) { lenLich(0); }
    }
  }

  function lamMoi() {
    st.conv = "";
    st.token = "";
    st.after = "";
    st.status = "";
    st.msgs = [];
    st.visitor = taoMa();
    dangCho = false;
    dangSoan(false);
    luu();
    veLaiLichSu();
    el.o.focus();
  }

  // ---------------------------------------------------------------- mạng

  function docLoi(r) {
    return r.json().then(
      function (d) {
        return (d && d.error && d.error.message) || "Trợ lý chưa trả lời được, anh/chị thử lại giúp em.";
      },
      function () { return "Trợ lý chưa trả lời được, anh/chị thử lại giúp em."; }
    );
  }

  function gui_(text) {
    text = (text || "").trim();
    if (!text || el.gui.disabled) { return; }
    if (text.length > maxChars) {
      baoHe("Tin nhắn dài quá, anh/chị viết ngắn lại giúp em.");
      return;
    }
    el.o.value = "";
    coGian();
    el.goiY.hidden = true;
    veBong("khach", text);
    el.gui.disabled = true;
    fetch(API + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitor_id: st.visitor, content: text }),
      cache: "no-store"
    })
      .then(function (r) {
        if (!r.ok) { return docLoi(r).then(function (msg) { throw new Error(msg); }); }
        return r.json();
      })
      .then(function (d) {
        st.conv = d.conversation_id;
        st.token = d.token;
        luu();
        dangCho = true;
        hanCho = Date.now() + 90000;
        dangSoan(true);
        lenLich(1500);
      })
      .catch(function (e) {
        baoHe(e && e.message ? e.message : "Không gửi được, anh/chị thử lại giúp em.");
      })
      .then(function () { el.gui.disabled = false; });
  }

  function lenLich(ms) {
    if (hengio) { window.clearTimeout(hengio); }
    hengio = window.setTimeout(poll, ms);
  }

  function poll() {
    hengio = null;
    if (dangPoll || !st.conv || !st.token) { return; }
    dangPoll = true;
    var q =
      "?conversation_id=" + encodeURIComponent(st.conv) +
      "&token=" + encodeURIComponent(st.token) +
      "&after=" + encodeURIComponent(st.after || "");
    fetch(API + "/poll" + q, { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) {
          return docLoi(r).then(function (msg) {
            baoHe(msg);
            st.conv = "";
            st.token = "";
            dangCho = false;
            dangSoan(false);
            luu();
            return null;
          });
        }
        if (!r.ok) { return null; }
        return r.json();
      })
      .then(function (d) {
        if (!d) { return; }
        var moiDen = 0;
        (d.messages || []).forEach(function (m) {
          dangSoan(false);
          veBong(m.from === "staff" ? "staff" : "domy", m.content);
          if (m.created_at) { st.after = m.created_at; }
          moiDen++;
        });
        if (d.status) { st.status = d.status; }
        if (moiDen) {
          dangCho = false;
          luu();
          if (el.cua.hidden) {
            el.badge.textContent = String((+el.badge.textContent || 0) + moiDen);
            el.badge.hidden = false;
          }
        }
        return d;
      })
      .catch(function () { /* mạng chập chờn: lượt sau thử lại */ })
      .then(function (d) {
        dangPoll = false;
        if (!st.conv) { return; }
        var them = d && d.retry_after ? d.retry_after * 1000 : 0;
        if (dangCho && Date.now() < hanCho) {
          lenLich(Math.max(3000, them));
        } else if (dangCho) {
          dangCho = false;
          dangSoan(false);
          baoHe("DOMY trả lời hơi lâu. Anh/chị chờ thêm hoặc gọi hotline để được hỗ trợ ngay.");
          lenLich(20000);
        } else if (st.status === "human" || !el.cua.hidden) {
          // Đã chuyển nhân viên, hoặc khung đang mở: nghe thưa để nhận tin nhân viên.
          lenLich(Math.max(20000, them));
        }
      });
  }

  // Nút ngoài khung: <button data-domy-ask="câu gõ sẵn" hidden>…</button>
  function noiNutNgoai() {
    var ds = document.querySelectorAll("[data-domy-ask]");
    Array.prototype.forEach.call(ds, function (b) {
      b.hidden = false;
      b.addEventListener("click", function (e) {
        e.preventDefault();
        batTat(true);
        gui_(b.getAttribute("data-domy-ask"));
      });
    });
  }

  function khoiDong() {
    fetch(API + "/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || d.enabled !== true) { return; }
        if (typeof d.max_chars === "number" && d.max_chars > 0) { maxChars = d.max_chars; }
        veKhung();
        noiNutNgoai();
        if (st.conv && st.status === "human") { lenLich(2000); }
      })
      .catch(function () { /* backend không trả lời: không vẽ khung */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", khoiDong);
  } else {
    khoiDong();
  }
})(window, document);
