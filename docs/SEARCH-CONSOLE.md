# Đăng ký viporder.com.vn với Google Search Console

Trang đã sẵn sàng cho Google: có `robots.txt` cho phép tất cả,
`sitemap.xml` (`https://viporder.com.vn/sitemap.xml`), thẻ canonical trỏ về
`https://viporder.com.vn/`, và không còn `noindex`. Việc còn lại phải làm
**bằng tài khoản Google của công ty**, nên chỉ anh làm được. Mất khoảng 10 phút.

## Bước 1 — thêm "Miền" (Domain property)
1. Vào https://search.google.com/search-console bằng Gmail của anh.
2. Bấm **Thêm sản phẩm** (Add property) → chọn ô bên trái **Miền** (Domain).
3. Gõ `viporder.com.vn` → **Tiếp tục**.
   Loại "Miền" gộp cả `viporder.com.vn` và `www`, http lẫn https.

## Bước 2 — xác minh bằng bản ghi TXT
1. Google hiện một chuỗi dạng `google-site-verification=abc123...` → bấm **Sao chép**.
2. Vào trang quản lý DNS (zonedns.vn, trang anh đã sửa bản ghi `mail`) → **Tạo record**:
   - Tên record: `@`
   - Loại record: **TXT**
   - Giá trị: dán chuỗi vừa sao chép
   - TTL: 3600
3. Đợi 5–10 phút rồi quay lại Search Console bấm **Xác minh** (Verify). Nếu báo
   chưa thấy thì đợi thêm rồi bấm lại. Bản ghi TXT này **giữ nguyên mãi**: xoá
   đi thì mất quyền.

Bản ghi TXT không ảnh hưởng tới trang web, email hay `khachhang`.

## Bước 3 — gửi sitemap
1. Trong Search Console, menu trái → **Sơ đồ trang web** (Sitemaps).
2. Gõ `sitemap.xml` → **Gửi**. Trạng thái phải thành **Thành công**.

## Bước 4 — yêu cầu lập chỉ mục trang chủ
1. Ô tìm kiếm trên cùng → dán `https://viporder.com.vn/` → Enter.
2. Bấm **Yêu cầu lập chỉ mục** (Request indexing).

Google thường hiện trang trong kết quả tìm kiếm sau vài ngày đến 2 tuần.

## Sau đó
- Search Console gửi email về Gmail của anh khi có lỗi lập chỉ mục.
- Có thể **liên kết với GA4** (Admin GA4 → Product links → Search Console) để
  xem từ khoá khách tìm trong báo cáo GA4.
- Nên làm tương tự với **Bing Webmaster Tools** (https://www.bing.com/webmasters),
  vì công cụ này có nút nhập thẳng từ Google Search Console.
