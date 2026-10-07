#!/usr/bin/env bash
# =============================================================================
# TRIỂN KHAI PRODUCTION — VIPORDER.COM.VN
#   ./deploy/production.sh                     thật
#   DEPLOY_DRY_RUN=1 ./deploy/production.sh    in kế hoạch
# =============================================================================
# Ba cửa khoá, fail closed: cây sạch · phiếu staging PASS đúng SHA · gói trùng
# mã băm bản đã nghiệm thu. Hỏng thì rollback, KHÔNG sửa trực tiếp production.
# =============================================================================
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_conf
select_env production

step "1. CỬA KHOÁ: CÂY LÀM VIỆC SẠCH"
require_clean_worktree
SHA="$(git_sha)"; BRANCH="$(git_branch)"; TREE="$(git_tree_sha)"
ok "nhánh $BRANCH · SHA $SHA"

step "2. CỬA KHOÁ: PHIẾU STAGING PASS CHO ĐÚNG SHA NÀY"
[ -d "$STATE_DIR/staging-pass" ] || die "chưa có phiếu staging nào. Chạy ./deploy/staging.sh rồi ./deploy/verify.sh staging"
GATE=""
for g in "$STATE_DIR/staging-pass"/*.json; do
  [ -f "$g" ] || continue
  [ "$(json_str "$g" git_sha)" = "$SHA" ] && GATE="$g"
done
[ -n "$GATE" ] || die "không có phiếu staging PASS nào cho SHA $SHA.
     Production chỉ nhận bản ĐÃ nghiệm thu ở staging."
RELEASE_ID="$(json_str "$GATE" release_id)"
valid_release_id "$RELEASE_ID" || die "phiếu có release_id sai dạng: $RELEASE_ID"
ok "phiếu $(basename "$GATE") · release $RELEASE_ID · nghiệm thu $(json_str "$GATE" verified_at)"
warn "phạm vi phiếu: $(json_str "$GATE" scope_note)"
warn "⇒ nginx/TLS của production được nghiệm thu Ở BƯỚC 12, không phải ở staging"

step "3. CỬA KHOÁ: GÓI PHẢI TRÙNG BẢN ĐÃ NGHIỆM THU"
AREC="$STATE_DIR/artifacts/$RELEASE_ID.json"
[ -f "$AREC" ] || die "thiếu sổ artifact $AREC — không chứng minh được đây là cùng gói đã test"
EXPECT="$(json_str "$AREC" artifact_sha256)"
require_host
build_artifact "$RELEASE_ID" "$DEPLOY_DIR/artifacts"
[ "$ARTIFACT_SHA256" = "$EXPECT" ] || die "mã băm gói LỆCH với bản đã nghiệm thu.
     staging : $EXPECT
     vừa dựng: $ARTIFACT_SHA256
     Nguồn đã khác ⇒ KHÔNG đẩy lên production."
ok "gói trùng byte với gói đã PASS staging"

step "4. GHI NHẬN BẢN ĐANG CHẠY"
PREV="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/current" 2>/dev/null | xargs -r basename || true
REMOTE
)"
PREV_SHA="$(remote_capture <<REMOTE
grep -o '"git_sha"[^,]*' "$ENV_ROOT/current/release.json" 2>/dev/null | head -1 | cut -d'"' -f4 || true
REMOTE
)"
printf '  PREVIOUS_RELEASE : %s\n' "${PREV:-(chưa có bản nào)}"
printf '  PREVIOUS_SHA     : %s\n' "${PREV_SHA:-(chưa có)}"
mkdir -p "$STATE_DIR/production"
cat > "$STATE_DIR/production/previous.json" <<JSON
{
  "recorded_at": "$(_ts)",
  "previous_release": "${PREV:-}",
  "previous_sha": "${PREV_SHA:-}",
  "incoming_release": "$RELEASE_ID",
  "incoming_sha": "$SHA"
}
JSON

step "5. SAO LƯU PRODUCTION"
"$DEPLOY_DIR/backup.sh" production

step "6. KIỂM MIGRATION"
migration_guard upgrade
if [ -n "${PREV_SHA:-}" ]; then
  N="$(git -C "$REPO_ROOT" diff --name-only "$PREV_SHA".."$SHA" -- backend/alembic/versions 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${N:-0}" -gt 0 ]; then
    warn "có $N file migration mới so với bản đang chạy"
    warn "rollback MÃ được; LƯỢC ĐỒ thì KHÔNG tự hạ cấp — xem docs/DIRECT-DEPLOY.md §6"
  else
    ok "không có migration mới ⇒ rollback mã là đủ"
  fi
else
  warn "chưa có bản trước ⇒ không so được migration; đây là lượt đầu tiên"
fi

step "7. ĐƯA LÊN PRODUCTION"
require_shared_env
ship_release "$RELEASE_ID"

step "8. MIGRATION TRÊN PRODUCTION"
remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$RELEASE_ID/deploy"
$(compose_prefix "$RELEASE_ID") run --rm $COMPOSE_APP_SERVICE alembic upgrade head
REMOTE
done_or_dry "alembic upgrade head trên production"

step "9. DỰNG TRỌN BỘ (db + app + nginx)"
# compose tự thay container của service có ảnh mới; db giữ nguyên volume.
compose_up_production "$RELEASE_ID"

step "10. HEALTH"
if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
  warn "chạy khô: không gọi health"
else
  got=""
  for i in $(seq 1 30); do
    got="$(app_probe "$HEALTH_PATH" "$PRODUCTION_PROXY_PORT")"
    [ "$got" = "200" ] && break
    sleep 2
  done
  if [ "$got" != "200" ]; then
    bad "health KHÔNG lên 200 (lần cuối: $got)"
    if [ -n "$PREV" ]; then
      "$DEPLOY_DIR/rollback.sh" production --yes
      die "đã rollback về $PREV. Bản $RELEASE_ID bị loại. Sửa ở máy, không sửa trên production."
    fi
    die "KHÔNG rollback được: chưa có bản trước nào. Đây là lượt triển khai đầu tiên."
  fi
  ok "$HEALTH_PATH → 200"
fi

step "11. ĐỔI current"
switch_current "$RELEASE_ID" "$SHA"
write_release_json "$RELEASE_ID" "$SHA" "$BRANCH" "$TREE"
remote_prune

step "12. NGHIỆM THU — HỎNG THÌ TỰ ROLLBACK"
if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
  warn "chạy khô: bỏ qua nghiệm thu"
elif "$DEPLOY_DIR/verify.sh" production; then
  ok "nghiệm thu production ĐẠT"
else
  bad "nghiệm thu KHÔNG đạt — tự rollback về ${PREV:-bản trước}"
  [ -n "$PREV" ] || die "KHÔNG rollback được: chưa có bản trước nào."
  "$DEPLOY_DIR/rollback.sh" production --yes
  die "đã rollback về $PREV. Bản $RELEASE_ID bị loại."
fi

step "KẾT QUẢ"
printf '  URL production : %s\n' "${PRODUCTION_URL:-(chưa có)}"
printf '  RELEASE_ID     : %s\n' "$RELEASE_ID"
printf '  GIT_SHA        : %s\n' "$SHA"
printf '  PREVIOUS       : %s / %s\n' "${PREV:-–}" "${PREV_SHA:-–}"
printf '  BƯỚC TIẾP THEO : git push && git tag — xem §7 tài liệu\n'
