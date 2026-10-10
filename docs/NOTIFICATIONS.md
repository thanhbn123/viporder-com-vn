# Thông báo Zalo khi có khách đăng ký

Mỗi khi có khách đăng ký mới trên viporder.com.vn, backend gửi **một tin Zalo**
tới nhân viên. Tin gồm tên, số điện thoại, email, tỉnh/thành, dịch vụ quan tâm,
kết quả, mã khách hàng (nếu có) và giờ đăng ký.

| Kết quả | Tin nhắn ghi |
|---|---|
| Tạo tài khoản thành công (201) | ✅ Đã tạo tài khoản |
| Chưa tạo được mã (202, PENDING) | ⚠️ **CẦN GỌI LẠI**: gọi khách theo số trong tin |
| Số đã có tài khoản / bị từ chối (409/422) | ❌ Không tạo được |

**Không bao giờ có mật khẩu** trong tin. Gửi lại cùng một yêu cầu (replay) hoặc
đăng ký trùng bị chặn từ đầu đều **không** sinh tin thứ hai. Nếu Zalo lỗi, khách
vẫn đăng ký bình thường: tin được gửi *sau* khi khách đã nhận kết quả, và mọi lỗi
gửi tin đều bị nuốt.

Cách gửi: Zalo Bot API, `POST https://bot-api.zapps.me/bot<TOKEN>/sendMessage`
`{"chat_id", "text"}`. Code ở `backend/app/notifications.py`.

## Bật thông báo (làm một lần)

1. **Tạo bot.** Trên điện thoại, mở Zalo, tìm **"Zalo Bot Creator"**, hoặc vào
   https://bot.zapps.me bằng tài khoản Zalo của anh. Tạo bot mới, đặt tên ví dụ
   *VIPORDER Thông báo*, rồi **copy token** (dạng `12345678:abc...`).
   Token là mật khẩu của bot: không gửi qua chat, không đưa vào git.
2. **Nhắn bot một tin bất kỳ** từ tài khoản Zalo sẽ nhận thông báo (ví dụ "hi").
3. **Lấy chat id**, chạy trên VPS:
   ```bash
   cd ~/viporder-production
   read -rs ZALO_BOT_TOKEN && export ZALO_BOT_TOKEN   # dán token, Enter (không hiện ra)
   python3 tools/zalo_chat_id.py                      # in ra ZALO_NOTIFY_CHAT_ID=...
   ```
4. **Gửi thử:**
   ```bash
   export ZALO_NOTIFY_CHAT_ID=<id vừa in ra>
   python3 tools/zalo_chat_id.py --test               # Zalo nhận "✅ VIPORDER: ..."
   ```
5. **Ghi vào `deploy/.env` của production** (sao lưu trước) hai dòng
   `ZALO_BOT_TOKEN=...` và `ZALO_NOTIFY_CHAT_ID=...`, rồi
   `docker compose -p viporder-prod -f docker-compose.yml -f docker-compose.production-vps.yml up -d --no-deps app`.
6. **Kiểm tra:** `curl -s https://viporder.com.vn/api/v1/health` phải có
   `"notifications":{"zalo":"on"}`.

Tắt: xoá hoặc để trống một trong hai biến rồi `up -d --no-deps app`.
