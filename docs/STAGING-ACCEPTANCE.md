# Phiếu nghiệm thu staging — viporder.com.vn

**Mục đích.** Đây là điểm chặn số 2 trước khi chuyển tên miền (`docs/DNS-CUTOVER.md` §6.0).
Kỹ thuật đã được đo bằng máy (§A). Phần §B là việc **chỉ chủ dự án làm được**:
nhìn trang bằng mắt trên thiết bị thật và xác nhận nội dung kinh doanh đúng.

**Cách dùng.** Mở file này trên GitHub → bấm ✏️ → đánh `x` vào ô đã kiểm
(`- [x]`) → ghi chú vào cột/ dòng "Ghi chú" nếu có điểm chưa đạt → commit vào
nhánh mới và mở PR vào `develop`. PR đó chính là biên bản nghiệm thu, có ngày giờ
và người ký.

| | |
|---|---|
| Địa chỉ nghiệm thu | **https://web.viporder.vn** (tên miền tạm, `noindex`) |
| Bản đang chạy (SHA) | `______________________` ← điền từ `/api/v1/health` hoặc báo cáo deploy |
| Người nghiệm thu | `______________________` |
| Ngày | `______________________` |
| Thiết bị | ☐ Điện thoại: `__________` ☐ Máy tính: `__________` |

> **Lưu ý khi thử form đăng ký:** staging đang **khoá ghi**
> (`registration_writes = disabled`) nên **không tạo tài khoản thật** bên
> KHAIBAO9610. Kết quả đúng là thông báo **màu vàng** "…VIPORDER sẽ liên hệ…".
> Dùng số điện thoại thử, mỗi lần một số khác nhau.

---

## A. Đã đo bằng máy — chỉ cần đối chiếu

Người deploy điền kết quả của lần deploy gần nhất. Không đánh dấu nếu chưa có số đo.

- [ ] `/api/v1/health` → `status: ok`, `tracking_reads: live`, `registration_writes: disabled`
- [ ] HTTPS hợp lệ, có `x-robots-tag: noindex, nofollow` và đủ 6 header bảo mật
- [ ] `/.git/HEAD`, `/.env` không trả 200; mã nguồn backend, `docs/` trả 404
- [ ] Tra cứu thật: mã nhập kho `KY4001103376087-2-4-|s` → `found`; mã đóng bao `A1918106` → `found`
- [ ] Bộ test: backend, JS, 75 test trình duyệt, Lighthouse ≥ 90 ở cả 4 mục — **xanh trên đúng SHA đang chạy**
- [ ] `/api/v1/admin/registrations/follow-up`: không khoá → 404; đúng khoá → 200

## B. Chủ dự án kiểm tra bằng mắt

### B1. Trang chủ — máy tính
- [ ] Logo, menu (Tra cứu vận đơn · Dịch vụ · Quy trình · Vì sao chọn VIPORDER · Liên hệ · Đăng nhập) hiển thị đúng, bấm từng mục cuộn tới đúng phần
- [ ] Ảnh, màu, chữ không vỡ, không chồng lên nhau
- [ ] Nội dung các phần **Dịch vụ**, **Quy trình**, **Vì sao chọn VIPORDER** đúng với dịch vụ thật của công ty
- [ ] Không có câu nào hứa điều công ty không làm (giá, thời gian giao, cam kết)

### B2. Trang chủ — điện thoại
- [ ] Nút menu (☰) mở/đóng được, các mục bấm được bằng ngón tay
- [ ] Không phải kéo ngang; chữ đọc được không cần phóng to
- [ ] Hai nút chính **Đăng ký mã khách hàng** và **Đăng nhập** thấy ngay

### B3. Đăng nhập
- [ ] Nút **Đăng nhập** (menu, thẻ, form) mở đúng `https://khachhang.viporder.com.vn`

### B4. Tra cứu vận đơn
- [ ] Nút **Tra mã vận đơn**, nhập `KY4001103376087-2-4-|s` → hiện kết quả
- [ ] Nút **Tra mã bao**, nhập `A1918106` → hiện kết quả
- [ ] Nhập mã không tồn tại → báo không tìm thấy, không lỗi trang

### B5. Form đăng ký (staging khoá ghi)
- [ ] Để trống / nhập sai SĐT, email, mật khẩu không khớp → báo lỗi rõ từng ô, bằng tiếng Việt
- [ ] Không tick "Chấp nhận điều khoản" → không gửi được
- [ ] Nhập đúng, bấm **Đăng ký mã khách hàng** → thông báo màu vàng:
      *"Chúng tôi đã ghi nhận đăng ký của bạn nhưng chưa tạo được mã khách hàng ngay. VIPORDER sẽ liên hệ qua số điện thoại bạn đã cung cấp để hoàn tất. Bạn không cần đăng ký lại."*
- [ ] Nhân viên lấy được số vừa đăng ký qua danh sách cần gọi lại (`follow-up`)

### B6. Liên hệ và chân trang
- [ ] Phần **Liên hệ** hiện **chưa có** hotline, Zalo, địa chỉ — cố ý, vì công ty chưa
      công bố và trang không được tự bịa (`index.html`, `TODO(g07-content)`). Anh quyết:
      ☐ ra mắt như vậy  ☐ bổ sung trước khi ra mắt — gửi: hotline `________`,
      Zalo `________`, địa chỉ `________`
- [ ] Chân trang: tên công ty / mã số thuế — ☐ không cần  ☐ cần, gửi: `________`

### B7. Trang lỗi
- [ ] Mở `https://web.viporder.vn/khong-ton-tai` → trang 404 có logo, có nút về trang chủ

---

## C. Kết luận

- [ ] **ĐẠT** — staging được chấp nhận làm bản phát hành
- [ ] **CHƯA ĐẠT** — các điểm cần sửa:

```
1.
2.
3.
```

Chữ ký (tên GitHub / tên): `______________________`

---

**Không thuộc phiếu này** (đã/ sẽ quyết ở nơi khác): đăng ký thật với KHAIBAO9610
khi bật ghi (đã đo một lần 2026-10-09, `docs/KHAIBAO9610-INTEGRATION.md` §10.8);
mã đo lường GA4/GTM/Meta Pixel; thông báo khi có khách đăng ký; tách mail trước khi
đổi DNS (`docs/DNS-CUTOVER.md` §6.0).
