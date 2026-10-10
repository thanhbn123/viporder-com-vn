# Phương án sao lưu — production viporder.com.vn

**Tình trạng hiện tại (10/10/2026):** chỉ có bản sao lưu **thủ công** mỗi lần
deploy (`~/log-truoc-trien-khai/viporder-prod-db-*.sql`), nằm **trên chính VPS**.
Nếu VPS hỏng ổ đĩa, bị xoá hoặc bị khoá tài khoản thì mất cả trang lẫn bản sao.

## Cần bảo vệ gì

| Thứ | Ở đâu | Mất thì sao |
|---|---|---|
| **CSDL khách đăng ký** (leads: tên, SĐT, email, trạng thái, mã KH) | volume `viporder-prod_db-data` | **Mất danh sách khách cần gọi lại.** Đây là thứ duy nhất không tạo lại được |
| `deploy/.env` (mật khẩu DB, token admin, token Zalo) | `~/viporder-production/deploy/.env` | Tạo lại được nhưng mất thời gian; **không** đưa lên mây |
| Chứng chỉ TLS nội bộ `deploy/certs/` | trên VPS | Tạo lại trong 1 phút |
| Code, cấu hình nginx, Caddy snippet | GitHub | Đã có sẵn |

Tài khoản khách thật nằm ở **KHAIBAO9610** (`khachhang.viporder.com.vn`), không
nằm ở đây. CSDL của trang này chỉ là danh sách đăng ký và theo dõi gọi lại.

## Phương án đề xuất: 3 lớp

### Lớp 1 — hằng ngày, trên VPS (bắt buộc, làm ngay)
- Script `deploy/backup-vps.sh` (đã có trong repo) chạy bằng cron lúc **02:17
  mỗi đêm**:
  - `pg_dump -Fc` CSDL production;
  - **kiểm bản dump đọc được** (`pg_restore --list`) rồi ghi sha256;
  - giữ **14 bản hằng ngày + 8 bản hằng tuần** (bản Chủ nhật), tự xoá bản cũ hơn.
- Dung lượng: CSDL hiện rất nhỏ (vài chục dòng), mỗi bản < 1 MB.
- Lệnh cài (user `deploy`, không cần root):
  ```bash
  mkdir -p ~/viporder-backups
  ~/viporder-production/deploy/backup-vps.sh        # chạy thử một lần, phải in "OK ..."
  ( crontab -l 2>/dev/null; echo '17 2 * * * ~/viporder-production/deploy/backup-vps.sh >> ~/viporder-backups/backup.log 2>&1' ) | crontab -
  ```

### Lớp 2 — chép ra ngoài VPS (nên làm trong tuần)
Chọn **một**:

| Cách | Chi phí | Ưu | Nhược |
|---|---|---|---|
| **A. Google Drive của anh qua `rclone`** (đề xuất) | Miễn phí (15 GB) | Anh tự xem được file trên Drive; không phụ thuộc nhà cung cấp VPS | Phải đăng nhập Google một lần trên VPS (`rclone config`) |
| B. Một VPS/máy khác kéo về bằng `rsync` | Theo máy có sẵn | Không dùng dịch vụ mây | Cần máy thứ hai luôn bật |
| C. Snapshot VPS của nhà cung cấp | Thường tính phí | Phục hồi cả máy | Vẫn cùng nhà cung cấp; snapshot chụp cả các dự án khác |

Với cách A: cài `rclone`, tạo remote `gdrive`, rồi thêm
`BACKUP_RCLONE_REMOTE=gdrive:viporder-backups` vào dòng cron. Script sẽ đẩy bản
mới lên sau mỗi lần sao lưu. File dump có thông tin cá nhân của khách, nên
thư mục Drive này **không chia sẻ** cho ai.

### Lớp 3 — kiểm tra phục hồi (mỗi tháng một lần, 5 phút)
Bản sao lưu chưa từng phục hồi thử thì chưa chắc dùng được. Mỗi tháng phục hồi
bản mới nhất vào một container tạm, đếm số dòng, rồi xoá container:
```bash
F=$(ls -t ~/viporder-backups/daily/*.dump | head -1)
docker run -d --name vip-restore-test -e POSTGRES_PASSWORD=t postgres:16-alpine
sleep 5
docker exec -i vip-restore-test pg_restore -U postgres -d postgres --no-owner < "$F"
docker exec vip-restore-test psql -U postgres -tc "select count(*) from leads"
docker rm -f vip-restore-test
```
Số dòng phải khớp production (`/api/v1/admin/registrations/follow-up` hoặc
`select count(*)` trên production).

## Khi cần phục hồi thật
1. Sao lưu tình trạng hiện tại trước (kể cả khi nó hỏng).
2. Dừng app: `$C stop app`.
3. Phục hồi: `$C exec -T db pg_restore -U viporder -d viporder --clean --if-exists < <file.dump>`.
4. `$C start app`, kiểm `/api/v1/health`.

(`$C` = `docker compose -p viporder-prod -f docker-compose.yml -f docker-compose.production-vps.yml`,
chạy trong `~/viporder-production/deploy`.)

## Anh cần quyết
- [ ] Đồng ý bật **Lớp 1** (phiên Mac cài cron, không cần root).
- [ ] Chọn cách cho **Lớp 2**: A (Google Drive), B hay C.
- [ ] Ai làm **Lớp 3** hằng tháng (có thể giao phiên Claude Code trên Mac).
