#!/usr/bin/env bash
# =============================================================================
# SAO LƯU HẰNG NGÀY — production trên VPS 160.22.170.20
# =============================================================================
# Dành cho bố cục thật trên VPS: checkout ~/viporder-production, compose project
# viporder-prod. (deploy/backup.sh là cho bố cục releases/ của production.sh.)
#
#   ./deploy/backup-vps.sh                 # chạy tay
#   crontab: 17 2 * * * ~/viporder-production/deploy/backup-vps.sh >> ~/viporder-backups/backup.log 2>&1
#
# Làm gì:
#   1. pg_dump -Fc CSDL production → ~/viporder-backups/daily/viporder-<ngày>.dump (mode 600)
#   2. kiểm bản dump đọc được (pg_restore --list), ghi sha256
#   3. Chủ nhật: chép thêm vào weekly/
#   4. giữ 14 bản daily, 8 bản weekly; xoá bản cũ hơn
#   5. nếu có BACKUP_RCLONE_REMOTE (vd. gdrive:viporder-backups): đẩy bản mới lên
#
# Không đụng: container khác, volume, staging. Không in mật khẩu.
# Thoát khác 0 khi lỗi, để cron/giám sát thấy.
# =============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/viporder-production}"
DEST="${BACKUP_DIR:-$HOME/viporder-backups}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"
REMOTE="${BACKUP_RCLONE_REMOTE:-}"

cd "$APP_DIR/deploy"
C=(docker compose -p viporder-prod -f docker-compose.yml -f docker-compose.production-vps.yml)

# Tên CSDL / user lấy từ chính container, không đọc .env.
PG_DB="$("${C[@]}" exec -T db sh -c 'printf %s "$POSTGRES_DB"')"
PG_USER="$("${C[@]}" exec -T db sh -c 'printf %s "$POSTGRES_USER"')"

umask 077
mkdir -p "$DEST/daily" "$DEST/weekly"
STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="$DEST/daily/viporder-$STAMP.dump"
TMP="$FILE.partial"

"${C[@]}" exec -T db pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$TMP"
# Một dump hỏng vẫn là một file có kích thước; kiểm đọc được ngay bây giờ.
if ! "${C[@]}" exec -T db pg_restore --list < "$TMP" > /dev/null; then
  echo "$(date -Is) LỖI: bản dump không đọc được: $TMP" >&2
  exit 1
fi
mv "$TMP" "$FILE"
sha256sum "$FILE" > "$FILE.sha256"
echo "$(date -Is) OK $(du -h "$FILE" | cut -f1) $FILE"

if [ "$(date +%u)" = "7" ]; then
  cp -p "$FILE" "$FILE.sha256" "$DEST/weekly/"
fi

prune() {  # prune <dir> <keep>
  find "$1" -maxdepth 1 -name 'viporder-*.dump' -printf '%T@ %p\n' \
    | sort -rn | tail -n +"$(( $2 + 1 ))" | cut -d' ' -f2- \
    | while read -r old; do rm -f -- "$old" "$old.sha256"; done
}
prune "$DEST/daily" "$KEEP_DAILY"
prune "$DEST/weekly" "$KEEP_WEEKLY"

if [ -n "$REMOTE" ]; then
  rclone copy --max-age 2d "$DEST" "$REMOTE"
  echo "$(date -Is) đã đẩy lên $REMOTE"
fi
