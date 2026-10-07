#!/usr/bin/env bash
# =============================================================================
# TRIỂN KHAI STAGING — VIPORDER.COM.VN
#   ./deploy/staging.sh                     thật
#   DEPLOY_DRY_RUN=1 ./deploy/staging.sh    in kế hoạch, không chạm máy chủ
# =============================================================================
# Staging ở đây chạy db + app, KHÔNG nginx, KHÔNG TLS — xem đầu phần 2 của
# common.sh để biết vì sao, và xem §9 tài liệu để biết nghiệm thu này KHÔNG phủ
# những gì.
# =============================================================================
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_conf
select_env staging

step "1. KIỂM TRA REPO TẠI MÁY"
require_clean_worktree
SHA="$(git_sha)"; BRANCH="$(git_branch)"; TREE="$(git_tree_sha)"
RELEASE_ID="$(make_release_id "$SHA")"
valid_release_id "$RELEASE_ID" || die "release id sai dạng: $RELEASE_ID"
ok "nhánh $BRANCH · SHA $SHA · release $RELEASE_ID"

step "2. CHẠY KIỂM TRA TẠI MÁY"
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"
elif [ -n "${DEPLOY_PYTHON:-}" ] && [ -x "$DEPLOY_PYTHON" ]; then PY="$DEPLOY_PYTHON"
else die "không thấy $REPO_ROOT/.venv/bin/python. Tạo venv hoặc đặt DEPLOY_PYTHON=...
     Script KHÔNG bỏ qua bước kiểm tra."
fi
ok "python: $PY ($("$PY" -V 2>&1))"
for raw in "${LOCAL_TEST_CMDS[@]}"; do
  cmd="${raw//\$PY/$PY}"
  log "→ $cmd"
  ( cd "$REPO_ROOT" && eval "$cmd" ) >/dev/null || die "KHÔNG đạt: $cmd
     Chạy lại lệnh đó một mình để xem đầu ra đầy đủ."
  ok "$cmd"
done

step "3. ĐÓNG GÓI ARTIFACT"
require_host
build_artifact "$RELEASE_ID" "$DEPLOY_DIR/artifacts"
mkdir -p "$STATE_DIR/artifacts"
cat > "$STATE_DIR/artifacts/$RELEASE_ID.json" <<JSON
{
  "release_id": "$RELEASE_ID",
  "git_sha": "$SHA",
  "artifact_sha256": "$ARTIFACT_SHA256",
  "built_at": "$(_ts)"
}
JSON
ok "đã ghi sổ artifact (production sẽ đòi trùng mã băm này)"

step "4. ĐƯA LÊN STAGING"
require_shared_env
ship_release "$RELEASE_ID"

step "5. SAO LƯU TRƯỚC KHI ĐỔI"
"$DEPLOY_DIR/backup.sh" staging

step "6. MIGRATION (alembic, chạy bằng ảnh của chính bản này)"
migration_guard upgrade
remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$RELEASE_ID/deploy"
$(compose_prefix "$RELEASE_ID") run --rm $COMPOSE_APP_SERVICE alembic upgrade head
REMOTE
done_or_dry "alembic upgrade head"

step "7. DỰNG BẢN MỚI (db + app)"
compose_up_staging "$RELEASE_ID" "$STAGING_PORT"

step "8. HEALTH CHECK"
if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
  warn "chạy khô: không gọi health"
else
  got=""
  for i in $(seq 1 30); do
    got="$(app_probe "$HEALTH_PATH" "$STAGING_PORT")"
    [ "$got" = "200" ] && break
    sleep 2
  done
  if [ "$got" != "200" ]; then
    remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$RELEASE_ID/deploy"
$(compose_prefix "$RELEASE_ID") logs --tail 60 $COMPOSE_APP_SERVICE 2>&1 | sed 's/^/    log| /'
REMOTE
    die "health KHÔNG lên 200 sau 60 giây (lần cuối: $got).
     Bản mới đang chạy nhưng chưa khoẻ. KHÔNG đổi current.
     Sửa ở máy, test, triển khai lại — đừng sửa trực tiếp trên máy chủ."
  fi
  ok "$HEALTH_PATH → 200"
fi

step "9. ĐỔI current"
switch_current "$RELEASE_ID" "$SHA"

step "10. GHI METADATA + DỌN BẢN CŨ"
write_release_json "$RELEASE_ID" "$SHA" "$BRANCH" "$TREE"
remote_prune

step "11. KẾT QUẢ"
printf '  URL staging    : %s\n' "${STAGING_URL:-(chưa có — staging chạy không proxy)}"
printf '  CỔNG APP       : 127.0.0.1:%s trên máy staging\n' "$STAGING_PORT"
printf '  RELEASE_ID     : %s\n' "$RELEASE_ID"
printf '  GIT_SHA        : %s\n' "$SHA"
printf '  BƯỚC TIẾP THEO : ./deploy/verify.sh staging\n'
printf '\n  %sĐã triển khai, CHƯA nghiệm thu.%s Chỉ verify.sh sinh phiếu cho production.\n' "$C_WARN" "$C_0"
