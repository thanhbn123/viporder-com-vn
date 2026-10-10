# Trợ lý DOMY trên viporder.com.vn

Khung chat nổi trên trang chủ cho khách hỏi Trợ lý DOMY (AI Customer 360) và đặt
giao hàng. Trình duyệt **không** nói chuyện với DOMY: nó gọi `/api/v1/chat/*` của
chính backend này, backend ký từng tin rồi chuyển sang DOMY.

```
trình duyệt ──► viporder.com.vn/api/v1/chat/*  (giới hạn lượt, ký HMAC)
                      │
                      ▼
          https://vanhanh.viporder.vn/domy-web/{webhook/webchat, api/webchat/poll}
          QUA TAILSCALE: máy viporder-web (100.83.38.75) → viporder-vps (100.108.103.24)
          (cửa hẹp trên Caddy VPS chính: chỉ hai đường này, chỉ từ 100.83.38.75,
           DOMY tự kiểm chữ ký)
                      │
                      ▼
               DOMY 127.0.0.1:8770  →  tri thức · tra đơn · link đặt giao hàng · chuyển nhân viên
```

## Bật / tắt

| Biến | Ý nghĩa |
|---|---|
| `DOMY_CHAT_UPSTREAM_URL` | `https://vanhanh.viporder.vn/domy-web` |
| `DOMY_CHAT_SECRET` | **bằng đúng** `WEBCHAT_WEBHOOK_SECRET` của DOMY. Là bí mật: không in, không commit |
| `DOMY_CHAT_MAX_CHARS` | độ dài tối đa một tin (mặc định 1000) |
| `DOMY_CHAT_MESSAGES_PER_MINUTE` | tin/phút mỗi IP khách (mặc định 6) |
| `DOMY_CHAT_MESSAGES_PER_VISITOR_DAY` | tin/ngày mỗi trình duyệt (mặc định 40) |
| `DOMY_CHAT_MESSAGES_PER_IP_DAY` | tin/ngày mỗi IP (mặc định 120) |
| `DOMY_CHAT_UPSTREAM_PER_MINUTE` | tổng lượt gọi DOMY/phút của cả trang (mặc định 90) |

Thiếu URL **hoặc** khoá = tắt: `/api/v1/chat/status` trả `enabled: false`, khung chat
và nút "Đặt giao hàng" tự ẩn. `/api/v1/health` báo `"chat":{"domy":"on|off"}`.

Đổi `.env` xong: `docker compose ... up -d --no-deps app`.

## Vì sao có `DOMY_CHAT_UPSTREAM_PER_MINUTE`

DOMY giới hạn 120 lượt/phút **theo IP nguồn**. Mọi khách web tới DOMY đều mang IP
của máy chủ viporder.com.vn, nên với DOMY họ là một người. Ngân sách chung 90/phút
giữ cả trang dưới ngưỡng đó; poll quá nhịp của cùng một hội thoại (dưới 2,5 giây)
được trả lô rỗng thay vì tốn ngân sách.

## Đường mạng: vì sao qua Tailscale

Hai VPS (`160.22.170.20` và `160.22.171.228`) cùng khai `/23` nhưng nằm sau hai cổng
khác nhau, nên gói tin đi thẳng bị rơi (ARP `FAILED`, "No route to host" — đo
10/10/2026). VPS viporder vì vậy chạy Tailscale (máy `viporder-web`, `--shields-up`,
`--accept-dns=false`), và compose ghim `vanhanh.viporder.vn` về IP Tailscale của VPS
chính (`extra_hosts` trong `deploy/docker-compose.production-vps.yml`). Tailscale trên
VPS viporder tắt hoặc đăng xuất ⇒ chat báo "tạm thời không phản hồi".

## Đặt giao hàng

Không có form mới. Khách nhắn "giao hàng", "xuất kho", "gọi xe"… hoặc bấm nút
**Đặt giao hàng** ở phần Liên hệ: DOMY trả **link riêng** (hạn 72 giờ, tối đa 5 lượt)
tới form `vanhanh.viporder.vn/d/<token>`; khung chat vẽ nó thành nút
"📦 Mở form đặt giao hàng". Chỉ link `https` về miền VIP Order mới thành nút
(`static/js/domy-chat-text.js`, có bộ thử); link lạ trong câu trả lời giữ nguyên là chữ.

## Nhân viên

Hội thoại hiện trong Cổng vận hành như kênh webchat, tên khách
**"Khách web viporder.com.vn"**, mã khách bắt đầu `web-`. Nhân viên trả lời ở đó,
khách thấy trong khung chat (poll thưa 20 giây khi khung mở hoặc đã chuyển nhân viên).

## Chưa làm (cần trước khi bật trên production)

- Rà kho tri thức DOMY: mục nào được nói với khách lạ.
- Hotline: DOMY và form giao hàng đưa `03.6263.1114` (nhân sự trực), trang đưa
  `0968 961 962` (số anh Thành). Chủ dự án chốt 11/10/2026 giữ như vậy.
