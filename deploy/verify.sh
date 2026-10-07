#!/usr/bin/env bash
# =============================================================================
# NGHIỆM THU — VIPORDER.COM.VN      ./deploy/verify.sh staging|production
# =============================================================================
# Chỉ lệnh này sinh phiếu cho production (deploy/state/staging-pass/<id>.json).
#
# ⚠️  §12.1 của CLAUDE.md, viết lại cho script này: thứ dùng để kiểm chứng phải
#     ĐỘC LẬP với thứ được kiểm chứng. Nên:
#       · không tin release.json để biết mã nào đang chạy — chính lượt deploy
#         viết nó. Mã đang chạy đo bằng băm nội dung file trên máy chủ, so với
#         cây commit bằng CÙNG MỘT công thức.
#       · "container tồn tại" KHÔNG phải "ứng dụng chạy được".
#       · mỗi phạm vi không đo được thì in ra, không bỏ qua.
# =============================================================================
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_conf
select_env "${1:-}"
require_host

if [ "$ENV_NAME" = staging ]; then
  PROBE_PORT="$STAGING_PORT"
  PROBE_WHAT="thẳng vào app (KHÔNG qua nginx, KHÔNG TLS)"
else
  PROBE_PORT="$PRODUCTION_PROXY_PORT"
  PROBE_WHAT="qua nginx của compose (có cả đường proxy)"
fi

PASS=0; FAIL=0
p() { ok "$1"; PASS=$((PASS+1)); }
f() { bad "$1"; FAIL=$((FAIL+1)); }

step "NGHIỆM THU $ENV_NAME — $(_ts)"
log "phép đo HTTP đi $PROBE_WHAT, từ bên trong máy chủ (127.0.0.1:$PROBE_PORT)"

CUR="$(remote_capture <<REMOTE
readlink "$ENV_ROOT/current" 2>/dev/null | xargs -r basename || true
REMOTE
)"
[ -n "$CUR" ] || die "không đọc được current trên $ENV_NAME — chưa triển khai bản nào?"
log "current: $CUR"
CP="$(compose_prefix "$CUR")"

# --- 1. Các service compose đang chạy ------------------------------------
want="$COMPOSE_DB_SERVICE $COMPOSE_APP_SERVICE"
[ "$ENV_NAME" = production ] && want="$want $COMPOSE_NGINX_SERVICE"
for svc in $want; do
  out="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy" && $CP ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s=$svc '\$1==s{print \$2}'
REMOTE
)"
  case "$out" in
    running) p "service $svc: running" ;;
    "")      f "service $svc: KHÔNG có trong compose ps" ;;
    *)       f "service $svc: $out" ;;
  esac
done

# --- 2. Số lần container tự dựng lại -------------------------------------
# "running" khi RestartCount tăng dần nghĩa là nó đang chết rồi được dựng lại.
out="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy"
id=\$($CP ps -q $COMPOSE_APP_SERVICE 2>/dev/null | head -1)
[ -n "\$id" ] && docker inspect -f '{{.RestartCount}}' "\$id" || echo "-"
REMOTE
)"
case "$out" in
  0) p "app chưa phải dựng lại lần nào (RestartCount=0)" ;;
  -|"") f "không đọc được RestartCount ⇒ chưa kết luận app ổn định" ;;
  *) f "app RestartCount=$out — đang chết rồi tự dựng lại" ;;
esac

# --- 3. Health ----------------------------------------------------------
out="$(app_probe "$HEALTH_PATH" "$PROBE_PORT")"
[ "$out" = "200" ] && p "$HEALTH_PATH → 200" || f "$HEALTH_PATH → $out"

# --- 4. Health nói CSDL ok, và không lộ DSN -----------------------------
body="$(remote_capture <<REMOTE
curl -s -m 10 "http://127.0.0.1:$PROBE_PORT$HEALTH_PATH" || echo '{}'
REMOTE
)"
case "$body" in
  *'"database":"ok"'*|*'"database": "ok"'*) p "health báo database ok" ;;
  *) f "health không báo database ok: $(printf '%s' "$body" | head -c 200)" ;;
esac
case "$body" in
  *postgresql*|*password*|*@*:*5432*) f "health trả ra thứ giống DSN/credential — phải sửa, không phải ghi chú" ;;
  *) p "health không chứa DSN/credential" ;;
esac

# --- 5. CSDL nối được và migration ở head -------------------------------
dbhead="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy"
$CP exec -T $COMPOSE_DB_SERVICE psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select coalesce((select version_num from alembic_version limit 1),'KHONG-CO')" 2>/dev/null | tr -d ' \r' || echo LOI
REMOTE
)"
codehead="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy"
$CP run --rm $COMPOSE_APP_SERVICE alembic heads 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -1 || true
REMOTE
)"
if [ "$dbhead" = "LOI" ] || [ -z "$dbhead" ]; then
  f "không truy vấn được CSDL $PG_DB"
elif [ "$dbhead" = "KHONG-CO" ]; then
  f "CSDL chưa có alembic_version — chưa migration"
elif [ -n "$codehead" ] && [ "$dbhead" = "$codehead" ]; then
  p "migration ở head: $dbhead (khớp alembic heads của mã đang chạy)"
elif [ -n "$codehead" ]; then
  f "migration LỆCH: CSDL $dbhead ≠ mã $codehead"
else
  f "đọc được CSDL head ($dbhead) nhưng KHÔNG đọc được alembic heads ⇒ chưa kết luận được"
fi

# --- 6. Trang chính + file tĩnh (chỉ có nghĩa khi có nginx) --------------
if [ "$ENV_NAME" = production ]; then
  for path in "/" "/static/css/style.css" "/robots.txt"; do
    out="$(app_probe "$path" "$PROBE_PORT")"
    [ "$out" = "200" ] && p "GET $path → 200" || f "GET $path → $out"
  done
  # Thứ KHÔNG được publish thì phải 404. Đây là chốt cho đúng lỗi đã xảy ra:
  # deploy từng copy cả cây nên /package.json và /tests/… fetch được qua HTTPS.
  for path in "/package.json" "/playwright.config.js" "/backend/app/main.py" "/deploy/docker-compose.yml"; do
    out="$(app_probe "$path" "$PROBE_PORT")"
    case "$out" in
      404|403) p "GET $path → $out (đúng: không publish)" ;;
      200) f "GET $path → 200 — ĐANG PHÁT TÁN thứ không nên publish" ;;
      *) f "GET $path → $out" ;;
    esac
  done
else
  warn "staging không dựng nginx ⇒ KHÔNG đo được trang tĩnh, header, hay danh sách publish"
fi

# --- 7. Smoke test KHÔNG PHÁ DỮ LIỆU -----------------------------------
# Payload SAI: chứng minh tầng kiểm tra dữ liệu còn sống mà không tạo khách thật.
# Đây cũng là cách deploy/post-deploy-check.sh đã làm, giữ cùng một nếp.
out="$(remote_capture <<REMOTE
curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  -H 'Content-Type: application/json' -d '{"phone":"KHONG-PHAI-SO"}' \
  "http://127.0.0.1:$PROBE_PORT/api/v1/registrations" || echo 000
REMOTE
)"
case "$out" in
  400|422) p "POST /api/v1/registrations dữ liệu sai → $out (validation sống, KHÔNG tạo khách)" ;;
  200|201) f "dữ liệu SAI lại trả $out — validation KHÔNG chạy" ;;
  404) f "đường /api/v1/registrations trả 404 — sai đường hoặc router chưa nạp" ;;
  *) f "POST /api/v1/registrations → $out" ;;
esac

# --- 8. Log lỗi và log secret -------------------------------------------
out="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy"
$CP logs --since 10m $COMPOSE_APP_SERVICE 2>&1 | grep -cE 'Traceback|CRITICAL|ERROR' || true
REMOTE
)"
[ "${out:-0}" = "0" ] && p "log 10 phút: 0 dòng Traceback/ERROR/CRITICAL" \
  || f "log 10 phút có $out dòng lỗi"
out="$(remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$CUR/deploy"
$CP logs --since 60m $COMPOSE_APP_SERVICE 2>&1 \
  | grep -cE 'POSTGRES_PASSWORD|ADMIN_API_TOKEN|postgresql(\+psycopg)?://[^ ]*:[^ @]*@' || true
REMOTE
)"
[ "${out:-0}" = "0" ] && p "log 60 phút: 0 dòng khớp mẫu secret" || f "log có $out dòng khớp mẫu secret"

# --- 9. Mã đang chạy so với Git — phép đo độc lập -----------------------
step "9. ĐỐI CHIẾU MÃ ĐANG CHẠY VỚI GIT"
SHA_LOCAL="$(git_sha)"
rsha="$(remote_tree_sha "$CUR")"
tmp="$(mktemp -d)"
git -C "$REPO_ROOT" archive --format=tar "$SHA_LOCAL" | tar -x -C "$tmp"
lsha="$( cd "$tmp" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 \
          | awk '{print $1"  "$2}' | shasum -a 256 | awk '{print $1}' )"
rm -rf "$tmp"
log "băm máy chủ : ${rsha:-(không đo được)}"
log "băm máy trạm: ${lsha:-(không đo được)}"
if [ -z "$rsha" ]; then
  f "KHÔNG đo được băm trên máy chủ ⇒ không kết luận mã đang chạy khớp Git"
elif [ "$rsha" = "$lsha" ]; then
  p "mã trên máy chủ KHỚP BYTE với cây commit $SHA_LOCAL"
else
  f "mã trên máy chủ KHÁC cây commit $SHA_LOCAL"
fi

# --- 10. Production: đo thêm từ NGOÀI Internet --------------------------
if [ "$ENV_NAME" = production ] && [ -n "${PRODUCTION_URL:-}" ]; then
  step "10. ĐO TỪ NGOÀI INTERNET (deploy/post-deploy-check.sh)"
  log "đây là điểm nhìn duy nhất nói được về DNS, TLS và nginx"
  if bash "$DEPLOY_DIR/post-deploy-check.sh" "$PRODUCTION_URL"; then
    p "post-deploy-check.sh đạt trên $PRODUCTION_URL"
  else
    f "post-deploy-check.sh KHÔNG đạt trên $PRODUCTION_URL"
  fi
else
  [ "$ENV_NAME" = production ] && warn "chưa có PRODUCTION_URL ⇒ chưa đo gì từ ngoài Internet"
fi

step "PHẠM VI CỦA PHIẾU NÀY"
cat <<SCOPE
  Đo: service compose, số lần dựng lại, health, CSDL + migration head,
  smoke test không phá dữ liệu, log lỗi, log secret, băm mã so với cây commit.
  $( [ "$ENV_NAME" = production ] && echo "Production còn đo: trang tĩnh, danh sách publish, và một lượt từ ngoài Internet." )
  KHÔNG đo: hành vi dưới tải; trình duyệt thật (việc của Playwright);
  $( [ "$ENV_NAME" = staging ] && echo "nginx, TLS, security header, rate limit tầng proxy — staging không dựng nginx." )
SCOPE

step "KẾT QUẢ: $PASS đạt · $FAIL không đạt"
if [ "$FAIL" -ne 0 ]; then
  [ "$ENV_NAME" = staging ] && { rm -f "$(gate_file "$CUR")"; log "KHÔNG sinh phiếu (và xoá phiếu cũ của $CUR nếu có)"; }
  die "nghiệm thu $ENV_NAME KHÔNG ĐẠT — $FAIL mục hỏng. Production vẫn bị chặn."
fi

if [ "$ENV_NAME" = staging ]; then
  mkdir -p "$STATE_DIR/staging-pass"
  G="$(gate_file "$CUR")"
  cat > "$G" <<JSON
{
  "project": "$PROJECT",
  "release_id": "$CUR",
  "git_sha": "$SHA_LOCAL",
  "verified_at": "$(_ts)",
  "staging_host_tree_sha256": "$rsha",
  "local_tree_sha256": "$lsha",
  "checks_passed": $PASS,
  "checks_failed": 0,
  "scope_note": "staging chạy db+app, KHÔNG nginx/TLS — phiếu này không nghiệm thu tầng proxy",
  "verified_by": "deploy/verify.sh"
}
JSON
  ok "đã sinh phiếu staging PASS: $G"
  printf '\n  BƯỚC TIẾP THEO: Owner nghiệm thu, rồi ./deploy/production.sh\n'
else
  ok "production nghiệm thu ĐẠT cho bản $CUR"
fi
