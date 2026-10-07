#!/usr/bin/env bash
# =============================================================================
# SAO LƯU — VIPORDER.COM.VN       ./deploy/backup.sh staging|production
# =============================================================================
# CSDL · dữ liệu bền · DANH SÁCH TÊN biến cấu hình (không giá trị) · metadata.
# Không chép secret vào Git, không tải secret về máy trạm.
# =============================================================================
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_conf
select_env "${1:-}"
require_host

STAMP="$(date +%Y%m%d-%H%M%S)"
step "SAO LƯU $ENV_NAME — $STAMP"

CUR="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/current" 2>/dev/null | xargs -r basename || true
REMOTE
)"
if [ -z "$CUR" ] && [ "${DEPLOY_DRY_RUN:-0}" != "1" ]; then
  warn "chưa có bản nào đang chạy ⇒ chưa có CSDL của compose để sao lưu"
  warn "đây là lượt triển khai đầu tiên; bỏ qua sao lưu là ĐÚNG, và nói ra chứ không im"
  exit 0
fi

remote_sh <<REMOTE
ROOT="$ENV_ROOT"
DEST="\$ROOT/backups/$STAMP"
mkdir -p "\$DEST"
CP="$(compose_prefix "${CUR:-KHONG-CO}")"

# 1. CSDL. pg_dump -Fc: với CSDL lớn thì đây là dạng phục hồi song song được.
\$CP exec -T $COMPOSE_DB_SERVICE pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "\$DEST/$PG_DB-$STAMP.dump"
chmod 600 "\$DEST/$PG_DB-$STAMP.dump"
sha256sum "\$DEST/$PG_DB-$STAMP.dump" > "\$DEST/$PG_DB-$STAMP.dump.sha256"

# Kiểm bản dump ĐỌC ĐƯỢC. Một dump hỏng vẫn là một file có kích thước, và sẽ
# chỉ lộ ra đúng lúc cần phục hồi — tức là lúc muộn nhất có thể.
if \$CP exec -T $COMPOSE_DB_SERVICE pg_restore --list /dev/stdin < "\$DEST/$PG_DB-$STAMP.dump" >/dev/null 2>&1; then
  echo "dump đọc được: pg_restore --list chạy sạch"
else
  echo "LỖI: pg_restore --list KHÔNG đọc được bản dump vừa tạo." >&2
  exit 1
fi

# 2. Dữ liệu bền của ứng dụng (var/ nếu có) và volume tải lên.
[ -d "\$ROOT/shared/var" ] && tar -czf "\$DEST/shared-var-$STAMP.tar.gz" -C "\$ROOT/shared" var

# 3. Cấu hình: CHỈ TÊN BIẾN.
[ -f "\$ROOT/shared/.env" ] && grep -oE '^[A-Z_]+' "\$ROOT/shared/.env" | sort > "\$DEST/env-keys.txt"

# 4. Metadata bản đang chạy + ảnh container đang dùng.
[ -f "\$ROOT/current/release.json" ] && cp "\$ROOT/current/release.json" "\$DEST/release-dang-chay.json"
[ -f "\$ROOT/history.log" ] && tail -50 "\$ROOT/history.log" > "\$DEST/history-tail.log"
\$CP images 2>/dev/null > "\$DEST/compose-images.txt" || true

du -sh "\$DEST" | awk '{print "kích thước: " \$1}'
REMOTE
done_or_dry "sao lưu $ENV_NAME xong: $ENV_ROOT/backups/$STAMP"
