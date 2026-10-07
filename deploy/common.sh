#!/usr/bin/env bash
# =============================================================================
# THƯ VIỆN CHUNG cho bộ triển khai trực tiếp (direct deploy) — VIPORDER.COM.VN
# =============================================================================
# Mô hình: LOCAL CODE → LOCAL TEST → VPS STAGING → STAGING VERIFY →
#          OWNER ACCEPTANCE → VPS PRODUCTION → PRODUCTION VERIFY → SYNC GITHUB
#
# GitHub KHÔNG còn là cửa quyết định triển khai. Nhưng Git, SHA, test, sao lưu,
# staging, rollback và nghiệm thu sau triển khai thì KHÔNG bỏ cái nào.
#
# File này chỉ định nghĩa hàm và nạp cấu hình. Nó KHÔNG tự làm gì khi được nạp.
#
# ⚠️  BA ĐIỀU FILE NÀY CỐ Ý KHÔNG LÀM:
#   1. Không bao giờ in giá trị secret. Chỉ in TÊN biến và trạng thái có/không.
#   2. Không bao giờ chép secret từ máy trạm lên máy chủ. Secret nằm ở
#      <APP_ROOT>/shared/.env trên máy chủ, do người đặt một lần.
#   3. Không tự hạ cấp (downgrade) database. Xem `migration_guard()`.
# =============================================================================

set -Eeuo pipefail

# --- Vị trí ---------------------------------------------------------------
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
STATE_DIR="$DEPLOY_DIR/state"

# --- Màu và ghi log -------------------------------------------------------
# Tắt màu khi không phải terminal, để log file không lẫn mã escape.
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'; C_DIM=$'\033[2m'; C_0=$'\033[0m'
else
  C_OK=''; C_BAD=''; C_WARN=''; C_DIM=''; C_0=''
fi

_ts() { date "+%Y-%m-%d %H:%M:%S %z"; }

log()   { printf '%s %s\n' "$(_ts)" "$*"; }
step()  { printf '\n%s=== %s ===%s\n' "$C_DIM" "$*" "$C_0"; }
ok()    { printf '  %sPASS%s  %s\n' "$C_OK" "$C_0" "$*"; }
bad()   { printf '  %sFAIL%s  %s\n' "$C_BAD" "$C_0" "$*"; }
warn()  { printf '  %sWARN%s  %s\n' "$C_WARN" "$C_0" "$*"; }

# die: dừng hẳn với mã thoát khác 0. Dùng cho mọi cửa khoá (fail closed).
die() { printf '\n%sDỪNG:%s %s\n' "$C_BAD" "$C_0" "$*" >&2; exit 1; }

# Bẫy lỗi: `set -E` + ERR trap để một lệnh hỏng ở giữa không bị trôi qua im lặng
# (§12.2 của CLAUDE.md: cấm nuốt lỗi).
_on_err() {
  local code=$? line=${BASH_LINENO[0]:-?} cmd=${BASH_COMMAND:-?}
  printf '\n%sLỖI%s mã %d tại dòng %s: %s\n' "$C_BAD" "$C_0" "$code" "$line" "$cmd" >&2
  exit "$code"
}
trap _on_err ERR

# --- Nạp cấu hình ---------------------------------------------------------
# deploy/deploy.conf là cấu hình KHÔNG secret, có trong Git.
# deploy/deploy.local.conf (nếu có) ghi đè, KHÔNG vào Git — dùng cho đường dẫn
# khoá SSH riêng của từng máy trạm.
load_conf() {
  [ -f "$DEPLOY_DIR/deploy.conf" ] || die "thiếu deploy/deploy.conf"
  # shellcheck disable=SC1091
  . "$DEPLOY_DIR/deploy.conf"
  if [ -f "$DEPLOY_DIR/deploy.local.conf" ]; then
    # shellcheck disable=SC1091
    . "$DEPLOY_DIR/deploy.local.conf"
  fi
  : "${PROJECT:?deploy.conf thiếu PROJECT}"
  : "${APP_ROOT:?deploy.conf thiếu APP_ROOT}"
  : "${KEEP_RELEASES:=5}"
  : "${HEALTH_PATH:=/api/health}"
  : "${READY_PATH:=/api/ready}"
}

# --- Môi trường: staging | production ------------------------------------
# Đặt các biến ENV_* theo môi trường được chọn. Fail closed: môi trường lạ thì dừng.
select_env() {
  local env="${1:-}"
  case "$env" in
    staging)
      ENV_NAME=staging
      ENV_HOST="${STAGING_HOST:-}"
      ENV_USER="${STAGING_USER:-}"
      ENV_KEY="${STAGING_SSH_KEY:-}"
      ENV_URL="${STAGING_URL:-}"
      ENV_ROOT="${APP_ROOT}/staging"
      ENV_RUNTIME="${STAGING_RUNTIME:-docker}"
      ;;
    production)
      ENV_NAME=production
      ENV_HOST="${PRODUCTION_HOST:-}"
      ENV_USER="${PRODUCTION_USER:-}"
      ENV_KEY="${PRODUCTION_SSH_KEY:-}"
      ENV_URL="${PRODUCTION_URL:-}"
      ENV_ROOT="${APP_ROOT}/production"
      ENV_RUNTIME="${PRODUCTION_RUNTIME:-docker}"
      ;;
    *) die "môi trường phải là 'staging' hoặc 'production', nhận được: '${env:-(rỗng)}'" ;;
  esac
}

# require_host: dừng nếu môi trường chưa có host/khoá. Đây là cửa khoá quan
# trọng nhất của cả bộ: KHÔNG cho phép "chạy một nửa rồi báo thành công".
require_host() {
  [ -n "${ENV_HOST:-}" ] || die "$ENV_NAME: chưa có host. Điền ${ENV_NAME^^}_HOST trong deploy/deploy.local.conf"
  [ -n "${ENV_USER:-}" ] || die "$ENV_NAME: chưa có user. Điền ${ENV_NAME^^}_USER"
  [ -n "${ENV_KEY:-}"  ] || die "$ENV_NAME: chưa có khoá SSH. Điền ${ENV_NAME^^}_SSH_KEY"
  # Nở rộng ~ một cách tường minh: `[ -f "~/..." ]` luôn sai.
  ENV_KEY="${ENV_KEY/#\~/$HOME}"
  [ -f "$ENV_KEY" ] || die "$ENV_NAME: không thấy file khoá SSH tại $ENV_KEY"
}

# --- SSH ------------------------------------------------------------------
# MỌI lệnh trên máy chủ đi qua đúng một hàm này, nên chỉ có một chỗ phải sửa khi
# đổi cách kết nối, và chỉ có một chỗ phải đọc khi muốn biết script làm gì ở xa.
#
# `-n` KHÔNG được dùng ở đây: remote_sh nhận script qua stdin (`bash -s`). Thay
# vào đó các vòng lặp gọi remote_sh phải tự đóng stdin của mình — xem
# ssh-trong-script-nuot-stdin trong ghi nhớ.
remote_sh() {
  local script
  script="$(cat)"
  if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
    printf '  %s[CHẠY KHÔ] ssh %s@%s bash -s <<<%s dòng%s\n' \
      "$C_DIM" "$ENV_USER" "$ENV_HOST" "$(printf '%s' "$script" | wc -l | tr -d ' ')" "$C_0"
    return 0
  fi
  printf '%s' "$script" | ssh \
    -o BatchMode=yes -o ConnectTimeout="${SSH_CONNECT_TIMEOUT:-10}" \
    -o StrictHostKeyChecking=accept-new \
    -i "$ENV_KEY" "$ENV_USER@$ENV_HOST" 'bash -seuo pipefail'
}

# remote_capture: như remote_sh nhưng trả stdout để so sánh. Ở chế độ chạy khô
# nó trả chuỗi rỗng — và mọi chỗ gọi nó PHẢI coi chuỗi rỗng là "chưa đo được",
# không bao giờ coi là "khớp".
remote_capture() { remote_sh; }

# done_or_dry: in PASS khi làm thật, in "CHƯA LÀM" khi chạy khô.
#
# Vì sao cần hàm riêng: lượt chạy khô đầu tiên in ra
# "PASS đã đưa bản lên máy chủ và kiểm mã băm hai đầu" trong khi nó KHÔNG hề
# nối tới máy chủ nào. Đó đúng là dạng lỗi §12.1 của CLAUDE.md — thứ dùng để
# báo kết quả lại tự nó không trung thực. Một dòng PASS sai còn tệ hơn không có
# dòng nào, vì nó được tin.
done_or_dry() {
  if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
    printf '  %sCHƯA LÀM%s  %s %s(chạy khô — không chạm máy chủ)%s\n' \
      "$C_WARN" "$C_0" "$1" "$C_DIM" "$C_0"
  else
    ok "$1"
  fi
}

# --- Nhận dạng bản phát hành ---------------------------------------------
# Dạng: YYYYMMDD-HHMMSS-<short_sha>, ví dụ 20261007-182500-a31df21
make_release_id() {
  local sha="$1"
  printf '%s-%s' "$(date +%Y%m%d-%H%M%S)" "${sha:0:7}"
}

RELEASE_ID_RE='^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$'
valid_release_id() { [[ "$1" =~ $RELEASE_ID_RE ]]; }

# --- Cửa khoá Git ---------------------------------------------------------
git_sha()    { git -C "$REPO_ROOT" rev-parse HEAD; }
git_branch() { git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD; }

# Băm CÂY LÀM VIỆC đã commit — dùng để đối chiếu với máy chủ về sau.
# Đây là phép đo ĐỘC LẬP với release.json: release.json do chính lượt triển khai
# viết ra, nên nó không thể tự chứng minh rằng mã trên máy chủ đúng (§12.1).
git_tree_sha() { git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}'; }

require_clean_worktree() {
  local dirty
  dirty="$(git -C "$REPO_ROOT" status --porcelain)"
  if [ -n "$dirty" ]; then
    printf '%s\n' "$dirty" | sed 's/^/    /'
    die "cây làm việc KHÔNG sạch. Triển khai mã chưa commit là triển khai mã không có SHA.
     Hãy commit hoặc stash. Script này KHÔNG tự xoá, không tự stash, không ghi đè gì của bạn."
  fi
}

# --- Sổ nghiệm thu staging (cửa vào production) --------------------------
# File này là ĐIỀU KIỆN BẮT BUỘC để production.sh chạy. Nó CHỈ được sinh ra bởi
# verify.sh sau khi đã đo thật trên staging — không script nào khác được ghi.
gate_file() { printf '%s/%s.json' "$STATE_DIR/staging-pass" "$1"; }

# --- Đọc một khoá đơn giản trong JSON ------------------------------------
# Cố ý KHÔNG dùng jq: máy trạm không chắc có jq, và thiếu jq mà im lặng bỏ qua
# phép kiểm thì đúng là hỏng im lặng. Chỉ đọc chuỗi phẳng, đủ cho release.json.
json_str() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 1
  sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -1
}

# --- HTTP -----------------------------------------------------------------
# Trả về "<mã http> <thời gian giây>"; mã 000 nghĩa là không nối được.
http_probe() {
  local url="$1" timeout="${2:-15}"
  curl -sS -o /dev/null -m "$timeout" -w '%{http_code} %{time_total}' "$url" 2>/dev/null || printf '000 0'
}

# --- Cửa khoá migration ---------------------------------------------------
# KHÔNG tự hạ cấp database. Alembic có downgrade, nhưng một downgrade chỉ an
# toàn khi chính nó được viết để đảo ngược được — mà điều đó không thể suy ra
# từ việc hàm downgrade() có tồn tại.
migration_guard() {
  local direction="$1"
  case "$direction" in
    upgrade) return 0 ;;
    downgrade)
      die "TỪ CHỐI hạ cấp database tự động.
     Lược đồ không phải lúc nào cũng đảo ngược được, và một downgrade sai làm
     MẤT DỮ LIỆU — thứ không có rollback nào vá lại được.
     Cách đúng: migration tương thích ngược (backward-compatible), rồi rollback
     chỉ phần MÃ. Xem docs/DIRECT-DEPLOY.md §6."
      ;;
    *) die "migration_guard: hướng lạ '$direction'" ;;
  esac
}

# --- Tóm tắt cuối ---------------------------------------------------------
summary_line() { printf '\n%s %s\n' "$1" "$2"; }

export -f log step ok bad warn die 2>/dev/null || true

# =============================================================================
# PHẦN 2 — CƠ CHẾ PHÁT HÀNH (chạy bằng Docker Compose đã có trong repo)
# =============================================================================
# Repo này ĐÃ CÓ deploy/docker-compose.yml, deploy/Dockerfile, deploy/nginx/ và
# deploy/systemd/. Lớp này KHÔNG thay thế chúng — nó chỉ thêm phần còn thiếu:
# bản phát hành có mã định danh, bản trước để quay về, sao lưu, và nghiệm thu.
#
# Bố cục trên máy chủ:
#   <ENV_ROOT>/releases/<release_id>/   toàn bộ cây repo của một bản
#   <ENV_ROOT>/current                  liên kết mềm tới bản ĐANG CHẠY
#   <ENV_ROOT>/previous                 bản TRƯỚC (đích rollback)
#   <ENV_ROOT>/shared/.env              secret, người đặt, script KHÔNG tạo
#   <ENV_ROOT>/backups/                 sao lưu CSDL
#   <ENV_ROOT>/history.log              sổ triển khai, chỉ ghi thêm
#
# ⚠️  HAI MÔI TRƯỜNG CHẠY KHÁC NHAU, CỐ Ý:
#   staging    → chỉ `db` + `app`, app mở ở 127.0.0.1:<cổng>. KHÔNG nginx,
#                KHÔNG TLS. Vì sao: compose gắn cứng 80:80/443:443 và cấu hình
#                nginx cần chứng chỉ trong deploy/certs — staging trên host
#                dùng chung sẽ đâm cổng với dự án khác và không có chứng chỉ.
#                Hệ quả phải biết: nghiệm thu staging KHÔNG nói gì về nginx,
#                TLS, security header hay rate limit ở tầng proxy.
#   production → trọn bộ `db` + `app` + `nginx`, và chạy thêm
#                deploy/post-deploy-check.sh từ NGOÀI Internet.
# =============================================================================

build_artifact() {
  local release_id="$1" out_dir="$2"
  mkdir -p "$out_dir"
  ARTIFACT="$out_dir/$PROJECT-$release_id.tar.gz"
  git -C "$REPO_ROOT" archive --format=tar.gz -o "$ARTIFACT" HEAD
  ARTIFACT_SHA256="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
  ok "gói artifact: $(basename "$ARTIFACT") ($(wc -c <"$ARTIFACT" | tr -d ' ') byte)"
  ok "sha256 artifact: $ARTIFACT_SHA256"
}

ship_release() {
  local release_id="$1"
  if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
    printf '  %s[CHẠY KHÔ] scp %s → %s@%s:%s/incoming/%s\n' "$C_DIM" \
      "$(basename "$ARTIFACT")" "$ENV_USER" "$ENV_HOST" "$ENV_ROOT" "$C_0"
  else
    remote_sh <<REMOTE
mkdir -p "$ENV_ROOT/releases" "$ENV_ROOT/shared" "$ENV_ROOT/backups" "$ENV_ROOT/incoming"
REMOTE
    scp -q -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i "$ENV_KEY" \
      "$ARTIFACT" "$ENV_USER@$ENV_HOST:$ENV_ROOT/incoming/" </dev/null
  fi
  remote_sh <<REMOTE
ROOT="$ENV_ROOT"
REL="\$ROOT/releases/$release_id"
ART="\$ROOT/incoming/$(basename "$ARTIFACT")"
[ -f "\$ART" ] || { echo "artifact không có trên máy chủ: \$ART" >&2; exit 1; }
GOT="\$(sha256sum "\$ART" | awk '{print \$1}')"
[ "\$GOT" = "$ARTIFACT_SHA256" ] || {
  echo "sha256 LỆCH: máy chủ \$GOT ≠ máy trạm $ARTIFACT_SHA256" >&2; exit 1; }
rm -rf "\$REL"; mkdir -p "\$REL"
tar -xzf "\$ART" -C "\$REL"
rm -f "\$ART"
# Secret KHÔNG nằm trong bản phát hành. Compose đọc nó qua liên kết mềm tới
# shared/.env, nên đổi bản không làm mất cấu hình và không nhân bản secret.
ln -sfn "\$ROOT/shared/.env" "\$REL/deploy/.env"
echo "đã giải nén: \$REL"
REMOTE
  done_or_dry "đã đưa bản $release_id lên máy chủ và kiểm mã băm hai đầu"
}

require_shared_env() {
  local out
  out="$(remote_capture <<REMOTE
if [ -f "$ENV_ROOT/shared/.env" ]; then
  echo "CO \$(grep -cE '^[A-Z_]+=' "$ENV_ROOT/shared/.env" || true)"
else
  echo "THIEU"
fi
REMOTE
)"
  if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then warn "chạy khô: không kiểm được shared/.env"; return 0; fi
  case "$out" in
    CO\ *) ok "shared/.env có mặt (${out#CO } biến; giá trị KHÔNG được in ra)" ;;
    *) die "$ENV_NAME: thiếu $ENV_ROOT/shared/.env.
     Mẫu có sẵn: deploy/env.production.example. Người đặt một lần, trực tiếp
     trên máy chủ; script cố ý không chép secret từ máy trạm lên." ;;
  esac
}

remote_tree_sha() {
  local release_id="$1"
  remote_capture <<REMOTE
cd "$ENV_ROOT/releases/$release_id" || exit 1
# Bỏ qua các thứ KHÔNG đến từ git archive: liên kết .env và thư mục sinh ra lúc chạy.
find . -type f -not -path './.git/*' -not -path './deploy/certs/*' -not -path './var/*' -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print \$1}'
REMOTE
}

# compose_prefix: một chỗ duy nhất quyết định lệnh compose, nên không có hai nơi
# cùng "biết" tên project và đường dẫn file.
compose_prefix() {
  local release_id="$1"
  printf 'docker compose -p %s-%s -f %s/releases/%s/deploy/docker-compose.yml --project-directory %s/releases/%s/deploy' \
    "$PROJECT" "$ENV_NAME" "$ENV_ROOT" "$release_id" "$ENV_ROOT" "$release_id"
}

write_release_json() {
  local release_id="$1" sha="$2" branch="$3" tree="$4"
  remote_sh <<REMOTE
cat > "$ENV_ROOT/releases/$release_id/release.json" <<'JSON'
{
  "project": "$PROJECT",
  "release_id": "$release_id",
  "git_sha": "$sha",
  "git_tree_sha": "$tree",
  "branch": "$branch",
  "built_at": "$(_ts)",
  "environment": "$ENV_NAME",
  "artifact_sha256": "$ARTIFACT_SHA256"
}
JSON
python3 - "$ENV_ROOT/releases/$release_id/release.json" <<'PY'
import json,sys,datetime
p=sys.argv[1]; d=json.load(open(p))
d["deployed_at"]=datetime.datetime.now().astimezone().isoformat(timespec="seconds")
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
REMOTE
  done_or_dry "đã ghi release.json trên máy chủ"
}

# compose_up_staging: chỉ db + app. App mở ở 127.0.0.1 nên không ai ngoài máy
# chủ gọi được một ứng dụng chưa có proxy đứng trước.
compose_up_staging() {
  local release_id="$1" port="$2"
  remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$release_id/deploy"
# File ghi đè của riêng staging: mở cổng app, và KHÔNG dựng nginx.
cat > docker-compose.override.yml <<'YML'
services:
  app:
    ports:
      - "127.0.0.1:__PORT__:8000"
    environment:
      APP_ENV: staging
YML
sed -i "s/__PORT__/$port/" docker-compose.override.yml
$(compose_prefix "$release_id") up -d --build db app
REMOTE
  done_or_dry "compose up (db + app) cho $release_id ở cổng $port"
}

compose_up_production() {
  local release_id="$1"
  remote_sh <<REMOTE
cd "$ENV_ROOT/releases/$release_id/deploy"
# Production chạy trọn bộ. Không có file ghi đè nào — cấu hình production là
# chính docker-compose.yml trong Git, không phải một bản vá tại máy chủ.
rm -f docker-compose.override.yml
$(compose_prefix "$release_id") up -d --build
REMOTE
  done_or_dry "compose up trọn bộ (db + app + nginx) cho $release_id"
}

switch_current() {
  local release_id="$1" sha="$2"
  remote_sh <<REMOTE
ROOT="$ENV_ROOT"
if [ -L "\$ROOT/current" ]; then
  cp -P "\$ROOT/current" "\$ROOT/previous.tmp" && mv -f "\$ROOT/previous.tmp" "\$ROOT/previous"
fi
ln -sfn "\$ROOT/releases/$release_id" "\$ROOT/current.tmp" && mv -f "\$ROOT/current.tmp" "\$ROOT/current"
printf '%s\t%s\t%s\t%s\n' "\$(date -Is)" "$ENV_NAME" "$release_id" "$sha" >> "\$ROOT/history.log"
REMOTE
  done_or_dry "current → $release_id (bản cũ thành previous)"
}

remote_prune() {
  remote_sh <<REMOTE
ROOT="$ENV_ROOT"
CUR="\$(readlink "\$ROOT/current" 2>/dev/null | xargs -r basename || true)"
PREV="\$(readlink "\$ROOT/previous" 2>/dev/null | xargs -r basename || true)"
cd "\$ROOT/releases" || exit 0
ls -1t | tail -n +$((KEEP_RELEASES + 1)) | while read -r r; do
  [ "\$r" = "\$CUR" ] && continue
  [ "\$r" = "\$PREV" ] && continue
  rm -rf "\$r" && echo "đã dọn bản cũ: \$r"
done
REMOTE
}

# app_probe: gọi HTTP vào ứng dụng TỪ TRONG máy chủ.
# Ở staging gọi thẳng cổng app; ở production đi qua nginx (cổng 80) nên nó đo
# luôn cả đường proxy — hai phạm vi khác nhau, và verify.sh in rõ đang đo cái nào.
app_probe() {
  local path="$1" port="$2"
  remote_capture <<REMOTE
curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$port$path" || echo 000
REMOTE
}
