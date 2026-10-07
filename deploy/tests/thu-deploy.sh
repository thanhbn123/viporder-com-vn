#!/usr/bin/env bash
# =============================================================================
# BỘ THỬ cho bộ triển khai trực tiếp.  Chạy:  bash deploy/tests/thu-deploy.sh
# =============================================================================
# Mỗi cửa khoá được thử HAI chiều:
#   · chiều thuận: điều kiện đủ thì script đi tiếp
#   · chiều NGƯỢC: điều kiện thiếu thì script DỪNG với mã khác 0
# Chỉ thử chiều thuận là cách chắc chắn nhất để có một cửa khoá không khoá gì.
#
# Bộ thử này KHÔNG cần máy chủ, KHÔNG cần Docker, KHÔNG cần mạng.
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(dirname "$HERE")"
REPO="$(dirname "$DEPLOY")"

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
grp() { printf '\n== %s\n' "$1"; }

# Chạy một đoạn bash có nạp common.sh; trả mã thoát và in ra stdout+stderr.
run_lib() {
  bash -c "
    set +e
    cd '$REPO'
    . '$DEPLOY/common.sh' 2>/dev/null
    $1
  " 2>&1
}
code_of() { run_lib "$1" >/dev/null 2>&1; echo $?; }

grp "1. Cú pháp mọi script"
for f in "$DEPLOY"/*.sh "$HERE"/*.sh; do
  if bash -n "$f" 2>/dev/null; then ok "bash -n $(basename "$f")"; else bad "bash -n $(basename "$f")"; fi
done

grp "2. Mọi script đều fail closed (set -Eeuo pipefail qua common.sh)"
if grep -q 'set -Eeuo pipefail' "$DEPLOY/common.sh"; then
  ok "common.sh đặt set -Eeuo pipefail"
else
  bad "common.sh KHÔNG đặt set -Eeuo pipefail"
fi
for f in staging.sh production.sh rollback.sh backup.sh verify.sh; do
  if grep -q 'common.sh"$' "$DEPLOY/$f" || grep -q 'common\.sh"' "$DEPLOY/$f"; then
    ok "$f nạp common.sh"
  else
    bad "$f KHÔNG nạp common.sh ⇒ không có pipefail, không có bẫy ERR"
  fi
done

grp "3. Dạng mã bản phát hành"
out="$(run_lib 'load_conf; make_release_id 0123456789abcdef')"
if [[ "$out" =~ ^[0-9]{8}-[0-9]{6}-0123456$ ]]; then
  ok "make_release_id → $out"
else
  bad "make_release_id sai dạng: $out"
fi
for good in 20261007-182500-a31df21; do
  [ "$(code_of "valid_release_id $good")" = 0 ] && ok "nhận đúng: $good" || bad "từ chối sai: $good"
done
# Chiều ngược: mọi dạng rác đều phải bị từ chối.
for junk in '20261007-182500-A31DF21' '2026107-182500-a31df21' 'latest' '../../etc/passwd' '20261007-182500-a31df21x' ''; do
  [ "$(code_of "valid_release_id '$junk'")" != 0 ] \
    && ok "từ chối rác: '${junk:-(rỗng)}'" || bad "NHẬN rác: '$junk'"
done

grp "4. Cửa khoá môi trường"
[ "$(code_of 'load_conf; select_env staging')"    = 0 ] && ok "select_env staging đi tiếp"    || bad "select_env staging bị chặn"
[ "$(code_of 'load_conf; select_env production')" = 0 ] && ok "select_env production đi tiếp" || bad "select_env production bị chặn"
for junk in prod stg '' 'staging production' 'staging; rm -rf /'; do
  [ "$(code_of "load_conf; select_env '$junk'")" != 0 ] \
    && ok "từ chối môi trường lạ: '${junk:-(rỗng)}'" || bad "NHẬN môi trường lạ: '$junk'"
done

grp "5. Cửa khoá host (phải DỪNG khi chưa có host/khoá)"
# Ca này phải TỰ DỰNG điều kiện "chưa có host", không được dựa vào việc máy
# trạm chưa có deploy.local.conf. Máy nào đã điền file đó thì ca sẽ tự đạt mà
# chẳng kiểm gì — đã dính đúng thế ở lượt chạy khô đầu tiên.
NOHOST='load_conf; STAGING_HOST=; STAGING_USER=; STAGING_SSH_KEY=; select_env staging; require_host'
out="$(run_lib "$NOHOST")"
if [ "$(code_of "$NOHOST")" != 0 ]; then
  ok "require_host dừng khi chưa có host (điều kiện do chính ca này dựng)"
  case "$out" in
    *deploy.local.conf*) ok "thông báo chỉ đúng chỗ cần điền (deploy.local.conf)" ;;
    *) bad "thông báo không nói phải điền ở đâu: $out" ;;
  esac
else
  bad "require_host ĐI TIẾP dù chưa có host — đây là cửa khoá hỏng"
fi
# Chiều thuận: có host + user + file khoá thật thì đi tiếp.
K="$(mktemp)"; printf 'khoa-gia\n' > "$K"
[ "$(code_of "load_conf; STAGING_HOST=1.2.3.4; STAGING_USER=deploy; STAGING_SSH_KEY=$K; select_env staging; require_host")" = 0 ] \
  && ok "require_host đi tiếp khi đủ host/user/khoá" || bad "require_host chặn dù đã đủ"
# Chiều ngược: khoá trỏ tới file KHÔNG tồn tại.
[ "$(code_of 'load_conf; STAGING_HOST=1.2.3.4; STAGING_USER=deploy; STAGING_SSH_KEY=/khong/co/khoa; select_env staging; require_host')" != 0 ] \
  && ok "require_host dừng khi file khoá không tồn tại" || bad "require_host nhận khoá không tồn tại"
rm -f "$K"

grp "6. Cửa khoá migration — KHÔNG tự hạ cấp"
[ "$(code_of 'migration_guard upgrade')" = 0 ] && ok "upgrade được phép" || bad "upgrade bị chặn"
[ "$(code_of 'migration_guard downgrade')" != 0 ] && ok "downgrade bị TỪ CHỐI" || bad "downgrade được cho qua — mất dữ liệu là chuyện không rollback được"
[ "$(code_of 'migration_guard bay-gio')" != 0 ] && ok "hướng lạ bị từ chối" || bad "hướng lạ được cho qua"

grp "7. Cửa khoá cây làm việc sạch"
T="$(mktemp -d)"
git -C "$T" init -q 2>/dev/null
git -C "$T" config user.email t@t; git -C "$T" config user.name t
echo a > "$T/a"; git -C "$T" add a; git -C "$T" commit -qm a
mkdir -p "$T/deploy"; cp "$DEPLOY/common.sh" "$DEPLOY/deploy.conf" "$T/deploy/"
# PHẢI commit cả deploy/ — nếu không thì chính nó làm cây bẩn, và ca "cây sạch"
# sẽ hỏng vì lý do không liên quan tới thứ đang thử. (Lỗi phép đo đã gặp thật
# ở lượt chạy đầu: ca này đỏ vì `?? deploy/`.)
git -C "$T" add deploy; git -C "$T" commit -qm deploy
clean_code="$(cd "$T" && bash -c ". deploy/common.sh 2>/dev/null; require_clean_worktree" >/dev/null 2>&1; echo $?)"
[ "$clean_code" = 0 ] && ok "cây sạch thì đi tiếp" || bad "cây sạch vẫn bị chặn (mã $clean_code)"
echo b > "$T/b"
dirty_code="$(cd "$T" && bash -c ". deploy/common.sh 2>/dev/null; require_clean_worktree" >/dev/null 2>&1; echo $?)"
[ "$dirty_code" != 0 ] && ok "cây BẨN thì dừng (kể cả chỉ có file chưa theo dõi)" || bad "cây bẩn vẫn cho triển khai"
dirty_msg="$(cd "$T" && bash -c ". deploy/common.sh 2>/dev/null; require_clean_worktree" 2>&1)"
case "$dirty_msg" in
  *"KHÔNG tự xoá"*) ok "thông báo nói rõ script không xoá/stash gì của người dùng" ;;
  *) bad "thông báo thiếu lời cam kết không xoá dữ liệu người dùng" ;;
esac
rm -rf "$T"

grp "8. production.sh phải DỪNG khi chưa có phiếu staging PASS"
# Chạy trên BẢN SAO SẠCH. Chạy ngay trong repo đang làm việc thì nó dừng ở cửa
# khoá "cây làm việc sạch" và ta đo nhầm cửa khoá — đã dính đúng lỗi này.
C="$(mktemp -d)/repo"
git clone -q --local --no-hardlinks "$REPO" "$C" 2>/dev/null
rm -rf "$C/deploy"; cp -R "$DEPLOY" "$C/deploy"; rm -rf "$C/deploy/state" "$C/deploy/artifacts"
git -C "$C" add -A >/dev/null; git -C "$C" -c user.email=t@t -c user.name=t commit -qm deploy >/dev/null
if [ -n "$(git -C "$C" status --porcelain)" ]; then
  bad "không dựng được bản sao sạch để thử ⇒ ca 8 KHÔNG đo được (không kết luận là đạt)"
fi
out="$(cd "$C" && bash deploy/production.sh 2>&1; echo "mã=$?")"
case "$out" in
  *"mã=0"*) bad "production.sh chạy tới cùng mà KHÔNG có phiếu staging" ;;
  *) ok "production.sh dừng khi chưa có phiếu (hoặc cây chưa sạch)" ;;
esac
case "$out" in
  *"chưa có phiếu staging nào"*|*"phiếu staging PASS nào cho SHA"*)
      ok "dừng ĐÚNG ở cửa khoá phiếu staging (bước 2), không phải ở cửa nào khác" ;;
  *) bad "dừng ở chỗ khác, không phải cửa phiếu staging: $(printf '%s' "$out" | tail -4)" ;;
esac

# 8b. Có phiếu, nhưng của SHA KHÁC ⇒ vẫn phải dừng. Đây là ca quan trọng nhất:
#     một phiếu tồn tại KHÔNG có nghĩa là phiếu cho bản này.
mkdir -p "$C/deploy/state/staging-pass"
cat > "$C/deploy/state/staging-pass/20261007-120000-deadbee.json" <<JSON
{ "release_id": "20261007-120000-deadbee", "git_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" }
JSON
out="$(cd "$C" && bash deploy/production.sh 2>&1; echo "mã=$?")"
case "$out" in
  *"phiếu staging PASS nào cho SHA"*) ok "có phiếu của SHA KHÁC thì vẫn dừng" ;;
  *"mã=0"*) bad "NHẬN phiếu của SHA khác — cửa khoá vô dụng" ;;
  *) bad "dừng nhưng không phải vì phiếu lệch SHA: $(printf '%s' "$out" | tail -3)" ;;
esac

# 8c. Phiếu ĐÚNG SHA nhưng thiếu sổ artifact ⇒ phải dừng ở bước 3, vì không
#     chứng minh được "cùng một gói đã test ở staging".
REAL_SHA="$(git -C "$C" rev-parse HEAD)"
rm -f "$C/deploy/state/staging-pass"/*.json
cat > "$C/deploy/state/staging-pass/20261007-120000-abcdef0.json" <<JSON
{ "release_id": "20261007-120000-abcdef0", "git_sha": "$REAL_SHA" }
JSON
out="$(cd "$C" && bash deploy/production.sh 2>&1; echo "mã=$?")"
case "$out" in
  *"thiếu sổ artifact"*) ok "có phiếu đúng SHA nhưng thiếu sổ artifact thì vẫn dừng" ;;
  *"mã=0"*) bad "đi tiếp dù không chứng minh được cùng một gói" ;;
  *) bad "dừng vì lý do khác: $(printf '%s' "$out" | tail -3)" ;;
esac
rm -rf "$(dirname "$C")"

grp "8d. state/ và artifacts/ PHẢI được gitignore"
# Nếu không, lượt staging đầu tiên làm cây bẩn và production.sh bị khoá chết.
# Đây là lỗi thật mà bộ thử này đã bắt được lúc viết, không phải ca giả định.
# Đo bằng đường dẫn FILE bên trong: mẫu `state/` chỉ khớp thư mục, và
# `git check-ignore` trên một đường dẫn chưa tồn tại thì không biết nó là thư
# mục nên trả "không khớp" — đúng cái bẫy đã làm ca này đỏ oan lúc viết.
for d in deploy/state/phieu.json deploy/artifacts/goi.tar.gz deploy/deploy.local.conf; do
  if git -C "$REPO" check-ignore -q "$d" 2>/dev/null; then
    ok "$d bị gitignore"
  else
    bad "$d KHÔNG bị gitignore ⇒ deploy xong là cây bẩn, lượt sau bị chặn"
  fi
done

grp "9. Không in secret, không chép secret"
# Chỉ được in TÊN biến. Mọi lệnh in nội dung shared/.env là lỗi.
if grep -nE '(cat|less|head|tail|printf .*|echo .*)[^|]*shared/\.env' "$DEPLOY"/*.sh | grep -v 'grep -c' | grep -v 'grep -oE' | grep -q .; then
  grep -nE '(cat|less|head|tail)[^|]*shared/\.env' "$DEPLOY"/*.sh | sed 's/^/    /'
  bad "có lệnh in nội dung shared/.env"
else
  ok "không script nào in nội dung shared/.env"
fi
if grep -nE 'scp .*\.env|rsync .*\.env' "$DEPLOY"/*.sh | grep -q .; then
  bad "có lệnh chép .env lên/ về máy chủ"
else
  ok "không script nào chép .env đi đâu"
fi
if grep -q 'env-keys.txt' "$DEPLOY/backup.sh" && grep -q "grep -oE '\^\[A-Z_\]+'" "$DEPLOY/backup.sh"; then
  ok "backup.sh chỉ sao lưu TÊN biến, không sao lưu giá trị"
else
  bad "backup.sh không chứng minh được là chỉ lấy tên biến"
fi

grp "10. deploy.conf KHÔNG được chứa host phỏng đoán"
for k in STAGING_HOST PRODUCTION_HOST STAGING_SSH_KEY PRODUCTION_SSH_KEY; do
  v="$(grep -E "^$k=" "$DEPLOY/deploy.conf" | cut -d= -f2-)"
  [ -z "$v" ] && ok "$k để trống trong deploy.conf (đúng: chưa đo thì không điền)" \
               || bad "$k đã có giá trị '$v' trong file vào Git"
done

grp "11. Dùng LẠI compose/Dockerfile có sẵn, không dựng song song cái thứ hai"
for f in docker-compose.yml Dockerfile nginx/viporder.com.vn.conf post-deploy-check.sh published-files.txt; do
  [ -e "$DEPLOY/$f" ] && ok "còn nguyên deploy/$f" || bad "mất deploy/$f — lớp mới không được thay thế thứ đang dùng"
done
if grep -q 'compose_prefix' "$DEPLOY/common.sh"; then
  ok "mọi lệnh compose đi qua một hàm duy nhất (compose_prefix)"
else
  bad "không có chỗ duy nhất quyết định lệnh compose ⇒ hai nơi cùng biết tên project"
fi
# Đúng một chỗ biết tên project compose. Hai chỗ là hai chỗ sẽ lệch nhau.
n="$(grep -c 'docker compose -p' "$DEPLOY"/*.sh | awk -F: '{s+=$2} END{print s}')"
[ "$n" = "1" ] && ok "chỉ 1 chỗ viết 'docker compose -p'" || bad "có $n chỗ viết 'docker compose -p'"
if grep -q 'docker-compose.override.yml' "$DEPLOY/.gitignore"; then
  ok "file ghi đè của staging bị gitignore (nó sinh tại máy chủ, không vào Git)"
else
  bad "docker-compose.override.yml KHÔNG gitignore"
fi
if grep -q 'rm -f docker-compose.override.yml' "$DEPLOY/common.sh"; then
  ok "production tự xoá file ghi đè ⇒ cấu hình production là file trong Git"
else
  bad "production không xoá file ghi đè ⇒ có thể chạy bằng bản vá tại máy chủ"
fi

grp "11b. verify.sh phải chốt danh sách publish"
# Lỗi đã xảy ra thật: deploy copy cả cây nên /package.json, /playwright.config.js
# và /tests/e2e/*.spec.js fetch được qua HTTPS. Phiếu nghiệm thu phải đo lại.
for path in package.json playwright.config.js backend/app/main.py; do
  grep -q "/$path" "$DEPLOY/verify.sh" && ok "verify.sh đo /$path phải 404" \
    || bad "verify.sh KHÔNG đo /$path"
done

grp "11c. Đường tĩnh trong verify.sh phải TỒN TẠI trong cây"
# Lỗi thật khi viết: verify.sh đo /static/css/site.css trong khi repo có
# style.css ⇒ nghiệm thu production sẽ FAIL oan. Đoán tên file là đoán.
for path in $(grep -oE '"/static/[^"]+"' "$DEPLOY/verify.sh" | tr -d '"'); do
  if [ -e "$REPO${path}" ]; then ok "có thật: $path"; else bad "verify.sh đo $path nhưng cây KHÔNG có file đó"; fi
done

grp "12. Chạy khô không được chạm máy chủ"
if grep -q 'DEPLOY_DRY_RUN' "$DEPLOY/common.sh" \
   && awk '/^remote_sh\(\)/,/^}/' "$DEPLOY/common.sh" | grep -q 'DEPLOY_DRY_RUN'; then
  ok "remote_sh tự chặn ở chế độ chạy khô"
else
  bad "remote_sh KHÔNG chặn khi chạy khô ⇒ 'chạy khô' có thể chạm máy chủ thật"
fi

grp "12b. Chạy khô KHÔNG được in PASS cho việc nó chưa làm"
# Lỗi thật đã gặp: lượt chạy khô in "PASS đã đưa bản lên máy chủ" trong khi
# không nối tới máy chủ nào. Dòng PASS sai còn tệ hơn không có dòng nào.
if grep -q 'done_or_dry()' "$DEPLOY/common.sh"; then
  ok "có hàm done_or_dry"
else
  bad "thiếu done_or_dry ⇒ chạy khô có thể in PASS cho việc chưa làm"
fi
for pair in "common.sh:lên máy chủ và kiểm mã băm" "common.sh:đã ghi release.json" \
            "common.sh:đã đổi bản đang chạy sang" "backup.sh:sao lưu \$ENV_NAME xong" \
            "staging.sh:migration ở head" "production.sh:migration ở head"; do
  file="${pair%%:*}"; txt="${pair#*:}"
  if grep -n "$txt" "$DEPLOY/$file" | grep -q '  ok "'; then
    bad "$file: '$txt' vẫn in bằng ok ⇒ chạy khô sẽ nói dối"
  else
    ok "$file: '$txt' đi qua done_or_dry"
  fi
done

grp "13. Phiếu staging chỉ do verify.sh sinh ra"
writers="$(grep -ln 'staging-pass' "$DEPLOY"/*.sh | xargs -n1 basename | sort | tr '\n' ' ')"
case "$writers" in
  *verify.sh*) ok "verify.sh có ghi phiếu" ;;
  *) bad "verify.sh không ghi phiếu" ;;
esac
if grep -ln 'cat > "\$G"' "$DEPLOY"/*.sh | grep -qv verify.sh; then
  bad "có script KHÁC verify.sh cũng ghi phiếu"
else
  ok "chỉ verify.sh ghi nội dung phiếu"
fi

printf '\n== KẾT QUẢ: %d đạt · %d không đạt\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
