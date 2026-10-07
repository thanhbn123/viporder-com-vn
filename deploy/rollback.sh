#!/usr/bin/env bash
# =============================================================================
# ROLLBACK — VIPORDER.COM.VN
#   ./deploy/rollback.sh staging|production [--yes]
# =============================================================================
# > ROLLBACK NÀY QUAY LẠI MÃ, KHÔNG QUAY LẠI LƯỢC ĐỒ DATABASE. Cố ý.
# > `alembic downgrade` chỉ an toàn khi chính migration đó được VIẾT để đảo
# > ngược được, và không suy ra được điều đó từ việc hàm downgrade() tồn tại.
# > Hạ cấp sai thì mất dữ liệu — không rollback nào vá lại được.
# > Luật: migration tương thích ngược. Xem docs/DIRECT-DEPLOY.md §6.
# =============================================================================
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_conf
select_env "${1:-}"
require_host
AUTO="${2:-}"

[ "$ENV_NAME" = staging ] && PROBE_PORT="$STAGING_PORT" || PROBE_PORT="$PRODUCTION_PROXY_PORT"

step "ROLLBACK $ENV_NAME"
CUR="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/current" 2>/dev/null | xargs -r basename || true
REMOTE
)"
TARGET="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/previous" 2>/dev/null | xargs -r basename || true
REMOTE
)"
[ -n "$TARGET" ] || die "không có bản trước để quay về ($ENV_ROOT/previous trống).
     Không bịa ra bản nào khác: chọn sai bản để rollback còn tệ hơn không rollback."
[ "$TARGET" != "$CUR" ] || die "previous trùng current ($CUR) — không có gì để quay về"
printf '  đang chạy : %s\n  quay về   : %s\n' "${CUR:-(không rõ)}" "$TARGET"

MIG_C="$(remote_capture <<REMOTE
ls -1 "$ENV_ROOT/releases/$CUR/backend/alembic/versions" 2>/dev/null | wc -l
REMOTE
)"
MIG_T="$(remote_capture <<REMOTE
ls -1 "$ENV_ROOT/releases/$TARGET/backend/alembic/versions" 2>/dev/null | wc -l
REMOTE
)"
if [ "${MIG_C:-0}" -gt "${MIG_T:-0}" ] 2>/dev/null; then
  warn "bản đang chạy có ${MIG_C} migration, bản đích có ${MIG_T}"
  warn "LƯỢC ĐỒ KHÔNG bị hạ cấp — mã cũ phải chạy được trên lược đồ mới."
  warn "Nếu không chạy được thì health check dưới đây sẽ nói ra, không im lặng."
fi

if [ "$AUTO" != "--yes" ] && [ "$ENV_NAME" = production ]; then
  printf '\n  Gõ đúng chữ %sROLLBACK%s để tiếp tục: ' "$C_WARN" "$C_0"
  read -r answer
  [ "$answer" = "ROLLBACK" ] || die "đã huỷ — không đổi gì"
fi

step "DỰNG LẠI BẢN $TARGET"
remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$TARGET/deploy"
$(compose_prefix "$TARGET") up -d --build
REMOTE
done_or_dry "compose up cho $TARGET"

step "ĐỔI current VỀ $TARGET"
remote_sh <<REMOTE
ROOT="$ENV_ROOT"
OLD="$CUR"
ln -sfn "\$ROOT/releases/$TARGET" "\$ROOT/current.tmp" && mv -f "\$ROOT/current.tmp" "\$ROOT/current"
# current ↔ previous đổi chỗ, để rollback hai lần quay lại chỗ cũ chứ không đi
# tiếp xuống một bản thứ ba mà không ai chọn.
[ -n "\$OLD" ] && { ln -sfn "\$ROOT/releases/\$OLD" "\$ROOT/previous.tmp" && mv -f "\$ROOT/previous.tmp" "\$ROOT/previous"; }
printf '%s\t%s\tROLLBACK→%s\n' "\$(date -Is)" "$ENV_NAME" "$TARGET" >> "\$ROOT/history.log"
REMOTE
done_or_dry "current → $TARGET"

step "HEALTH SAU ROLLBACK"
if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
  warn "chạy khô: không gọi health"
else
  got=""
  for i in $(seq 1 30); do
    got="$(app_probe "$HEALTH_PATH" "$PROBE_PORT")"
    [ "$got" = "200" ] && break
    sleep 2
  done
  [ "$got" = "200" ] || die "ROLLBACK KHÔNG KHOẺ: health = $got sau 60 giây.
     $ENV_NAME đang ở trạng thái xấu và cần người xử ngay.
     KHÔNG tự thử sang một bản thứ ba."
  ok "health 200 sau rollback"
fi

step "XÁC NHẬN BẢN ĐANG CHẠY"
NOW="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/current" 2>/dev/null | xargs -r basename || true
REMOTE
)"
printf '  current: %s\n' "${NOW:-(không đo được)}"
if [ "${DEPLOY_DRY_RUN:-0}" != "1" ] && [ "$NOW" != "$TARGET" ]; then
  die "current đang là '${NOW:-rỗng}' chứ không phải '$TARGET' — rollback CHƯA xong"
fi
ok "rollback $ENV_NAME về $TARGET xong"
printf '\n  Nhớ: lược đồ database KHÔNG bị hạ cấp. Vá tiến lên, không vá lùi.\n'
