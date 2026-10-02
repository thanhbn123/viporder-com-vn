#!/usr/bin/env bash
# LOCAL PRODUCTION REHEARSAL — not a staging deployment.
#
# WHAT THIS IS. The production path is: real nginx -> uvicorn (--workers 1) ->
# PostgreSQL, serving a web root built by an explicit rsync allow list. This script
# builds that stack on ONE machine and walks the operator sequence against it.
#
# WHAT THIS IS NOT. It is not staging and it is not production. There is no second
# host, no real TLS certificate, no DNS, and no real provider. Every line of output
# says LOCAL PRODUCTION REHEARSAL and the results are labelled that way.
#
# WHY IT EXISTS. "Docker was never executed" and "only one uvicorn worker was ever
# tested" were both true and are both recorded as residuals. This closes as much of
# that as one machine can: the systemd path is exercised for real, against a real
# PostgreSQL, behind a real nginx, with the REAL publish command.
#
# USAGE:  bash tools/local_rehearsal.sh
# EXIT:   0 only if every step passed.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
R="${REHEARSAL_DIR:-/tmp/viporder-rehearsal}"
APP_PORT="${REHEARSAL_APP_PORT:-8143}"
NGINX_PORT="${REHEARSAL_NGINX_PORT:-8453}"
NGINX_HTTP_PORT="${REHEARSAL_NGINX_HTTP_PORT:-8081}"
PG_PORT="${REHEARSAL_PG_PORT:-55503}"
PGDATA="$R/pgdata"
PG_BIN="/opt/homebrew/opt/postgresql@16/bin"
VENV="$ROOT/.venv"

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; SKIP=$((SKIP+1)); }
step() { printf '\n=== %s ===\n' "$1"; }

cleanup() {
  [ -f "$R/logs/uvicorn.pid" ] && kill "$(cat "$R/logs/uvicorn.pid")" 2>/dev/null
  # nginx writes its pid to the `pid` directive in nginx.conf, which points at
  # $R/nginx/logs/nginx.pid — NOT $R/logs. Looking in the wrong place made the
  # trap a no-op and left a stray master holding the ports for the next run.
  [ -f "$R/nginx/logs/nginx.pid" ] && nginx -p "$R/nginx" -c "$R/nginx/conf/nginx.conf" -s stop 2>/dev/null
  "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null 2>&1
}
trap cleanup EXIT

# A stale listener on the app port makes every later step report a misleading
# failure (uvicorn dies on bind; nginx then proxies to whatever IS listening).
for _p in "$APP_PORT" "$NGINX_PORT" "$NGINX_HTTP_PORT"; do
  if lsof -ti "tcp:$_p" >/dev/null 2>&1; then
    echo "REFUSING: port $_p is already in use by pid(s): $(lsof -ti tcp:$_p | tr '\n' ' ')"
    echo "Stop them by PID and re-run. This script will not start a half-stack."
    exit 3
  fi
done

echo "############################################################"
echo "#  LOCAL PRODUCTION REHEARSAL — NOT A STAGING DEPLOYMENT   #"
echo "#  revision: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "#  host:     $(hostname -s)   $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "############################################################"

rm -rf "$R"; mkdir -p "$R"/{app,site,var,logs,nginx/conf,nginx/logs,nginx/snippets,nginx/upstreams}

step "1. PUBLISH THE WEB ROOT — the real allow-list command"
# The `-r` is not decoration: --files-from cancels the -r implied by -a, and
# without it the directories are created and their CONTENTS are not copied.
rsync -a -r --delete --files-from="$ROOT/deploy/published-files.txt" "$ROOT/" "$R/site/" 2>"$R/logs/rsync.err"
if [ -f "$R/site/index.html" ]; then ok "index.html published"; else bad "index.html missing"; fi
CSS=$(find "$R/site/static" -name '*.css' 2>/dev/null | wc -l | tr -d ' ')
JS=$(find "$R/site/static" -name '*.js' 2>/dev/null | wc -l | tr -d ' ')
[ "$CSS" -gt 0 ] && ok "CSS published ($CSS file(s))" || bad "NO CSS — the --files-from/. -r trap"
[ "$JS" -gt 0 ] && ok "JS published ($JS file(s))" || bad "NO JS — the --files-from/-r trap"
LEAK=$(cd "$ROOT" && rsync -a -r --delete --files-from=deploy/published-files.txt ./ "$R/site/" --dry-run 2>/dev/null | grep -cE 'package\.json|node_modules|tests/|playwright\.config|README' || true)
[ "$LEAK" = "0" ] && ok "publish list leaks nothing non-public" || bad "publish list would ship $LEAK non-public path(s)"

step "2. POSTGRESQL — fresh cluster, then migrations from zero"
"$PG_BIN/initdb" -D "$PGDATA" -U viporder --auth=trust >/dev/null 2>&1 \
  && ok "initdb" || bad "initdb"
"$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $PG_PORT -k $R -c listen_addresses=127.0.0.1" -l "$R/logs/pg.log" start >/dev/null 2>&1
sleep 3
"$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U viporder viporder 2>/dev/null \
  && ok "createdb" || bad "createdb"
DSN="postgresql+psycopg://viporder@127.0.0.1:$PG_PORT/viporder"

( cd "$ROOT/backend" && DATABASE_URL="$DSN" "$VENV/bin/alembic" upgrade head >"$R/logs/alembic.log" 2>&1 ) \
  && ok "alembic upgrade head (fresh DB)" || { bad "alembic upgrade head"; tail -5 "$R/logs/alembic.log"; }
REV=$(cd "$ROOT/backend" && DATABASE_URL="$DSN" "$VENV/bin/alembic" current 2>/dev/null | tail -1)
[ -n "$REV" ] && ok "revision after upgrade: $REV" || bad "could not read alembic current"
TABLES=$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U viporder -d viporder -tAc \
  "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null)
[ "${TABLES:-0}" -ge 2 ] && ok "tables created: $TABLES" || bad "expected >=2 tables, found ${TABLES:-0}"

step "3. APPLICATION — uvicorn --workers 1, exactly as systemd runs it"
export DATABASE_URL="$DSN"
export KHAIBAO9610_MODE=mock
export RATE_LIMIT_ENABLED=true
export LOGIN_URL="https://khachhang.viporder.com.vn"
export ADMIN_API_TOKEN=""
# `$!` must be captured in the SAME shell that starts the process. Writing it
# inside a subshell records the subshell's pid, which exits immediately — the
# process is fine but `kill -0` on a dead pid reports "did not restart".
start_app() {
  cd "$ROOT/backend" || return 1
  nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$APP_PORT" \
    --workers 1 --proxy-headers >>"$R/logs/uvicorn.log" 2>&1 &
  echo $! > "$R/logs/uvicorn.pid"
  cd "$ROOT" || return 1
}
start_app
sleep 6
APP_PID=$(cat "$R/logs/uvicorn.pid" 2>/dev/null)
kill -0 "$APP_PID" 2>/dev/null && ok "uvicorn running (pid $APP_PID, 1 worker)" || { bad "uvicorn not running"; tail -5 "$R/logs/uvicorn.log"; }
WORKERS=$(pgrep -P "$APP_PID" 2>/dev/null | wc -l | tr -d ' ')
ok "worker processes: 1 at entrypoint (children: $WORKERS)"

step "4. NGINX — the real site config, upstream pointed at the rehearsal app"
sed -e "s#127.0.0.1:8000#127.0.0.1:$APP_PORT#" "$ROOT/deploy/nginx/upstream-systemd.conf" > "$R/nginx/upstreams/viporder-upstream.conf"
cp "$ROOT/deploy/nginx/viporder-security-headers.conf" "$R/nginx/snippets/"
cp "$ROOT/deploy/nginx/proxy_params_viporder" "$R/nginx/conf/proxy_params"
sed -e "s#/srv/viporder/site#$R/site#g" \
    -e "s#/etc/nginx/snippets/#$R/nginx/snippets/#g" \
    -e "s#/etc/nginx/upstreams/#$R/nginx/upstreams/#g" \
    -e "s#/etc/nginx/proxy_params_viporder#$R/nginx/conf/proxy_params#g" \
    -e "s#/var/log/nginx/#$R/nginx/logs/#g" \
    -e "s#/var/www/certbot#$R/site#g" \
    -e "s#listen 80;#listen $NGINX_HTTP_PORT;#g" \
    -e "s#listen \\[::\\]:80;#listen [::]:$NGINX_HTTP_PORT;#g" \
    -e "s#listen 443 ssl[^;]*;#listen $NGINX_PORT ssl;#g" \
    -e "s#listen \[::\]:443 ssl[^;]*;#listen [::]:$NGINX_PORT ssl;#g" \
    "$ROOT/deploy/nginx/viporder.com.vn.conf" > "$R/nginx/conf/site.conf"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=localhost" \
  -keyout "$R/nginx/key.pem" -out "$R/nginx/cert.pem" >/dev/null 2>&1
python3 - "$R" <<'PY'
import pathlib, sys
P = sys.argv[1]
p = pathlib.Path(f"{P}/nginx/conf/site.conf")
out = []
for l in p.read_text().split("\n"):
    s, ind = l.strip(), l[:len(l) - len(l.lstrip())]
    if s.startswith("ssl_certificate_key"):
        out.append(f"{ind}ssl_certificate_key {P}/nginx/key.pem;")
    elif s.startswith("ssl_certificate"):
        out.append(f"{ind}ssl_certificate {P}/nginx/cert.pem;")
    elif "ssl_trusted_certificate" in s:
        continue
    else:
        out.append(l)
p.write_text("\n".join(out))
PY
cat > "$R/nginx/conf/nginx.conf" <<EOF
worker_processes 1;
error_log $R/nginx/logs/error.log;
pid $R/nginx/logs/nginx.pid;
events { worker_connections 64; }
http {
  include /opt/homebrew/etc/nginx/mime.types;
  default_type application/octet-stream;
  access_log $R/nginx/logs/access.log;
  client_body_temp_path $R/nginx/logs/cb;
  proxy_temp_path $R/nginx/logs/px;
  fastcgi_temp_path $R/nginx/logs/fc;
  uwsgi_temp_path $R/nginx/logs/uw;
  scgi_temp_path $R/nginx/logs/sc;
  include $R/nginx/conf/site.conf;
}
EOF
nginx -p "$R/nginx" -c "$R/nginx/conf/nginx.conf" -t >"$R/logs/nginx-t.log" 2>&1 \
  && ok "nginx -t (real config)" || { bad "nginx -t"; tail -4 "$R/logs/nginx-t.log"; }
nginx -p "$R/nginx" -c "$R/nginx/conf/nginx.conf" >/dev/null 2>&1
sleep 2
H="https://127.0.0.1:$NGINX_PORT"
curl -sk -H "Host: viporder.com.vn" -o /dev/null -m 8 "$H/" && ok "nginx serving" || bad "nginx not serving"

step "5. HEALTH"
CODE=$(curl -sk -H "Host: viporder.com.vn" -o "$R/logs/health.json" -w '%{http_code}' -m 10 "$H/api/v1/health")
[ "$CODE" = "200" ] && ok "/api/v1/health -> 200" || bad "/api/v1/health -> $CODE"
python3 -c "import json;d=json.load(open('$R/logs/health.json'));print('        body:',json.dumps(d)[:120])" 2>/dev/null

step "6. SECURITY HEADERS"
HDR=$(curl -sk -H "Host: viporder.com.vn" -D - -o /dev/null -m 8 "$H/" | tr -d '\r')
for h in "Strict-Transport-Security" "X-Content-Type-Options" "X-Frame-Options" "Referrer-Policy" "Permissions-Policy"; do
  echo "$HDR" | grep -qi "^$h" && ok "header present: $h" || bad "header MISSING: $h"
done
if echo "$HDR" | grep -i "strict-transport-security" | grep -q "includeSubDomains"; then
  bad "HSTS carries includeSubDomains — it must NOT, by decision"
else
  ok "HSTS without includeSubDomains (as decided)"
fi

step "7. REGISTRATION — mock provider through the real stack"
REG=$(curl -sk -H "Host: viporder.com.vn" -o "$R/logs/reg.json" -w '%{http_code}' -m 15 -X POST "$H/api/v1/registrations" \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Khách Rehearsal","phone":"0912000099","email":"rehearsal@example.com","password":"rehearsal-pw-123","confirm_password":"rehearsal-pw-123","accept_terms":true,"consent":true}')
case "$REG" in 200|201|202) ok "registration -> $REG";; *) bad "registration -> $REG";; esac
python3 -c "import json;d=json.load(open('$R/logs/reg.json'));print('        keys:',sorted(d)[:10])" 2>/dev/null
# The password must not appear in the database.
PW=$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U viporder -d viporder -tAc \
  "select count(*) from leads where phone like '%912000099%'" 2>/dev/null)
[ "${PW:-0}" -ge 1 ] && ok "lead persisted before/around the provider call (rows: $PW)" || bad "no lead row found"
LEAKPW=$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U viporder -d viporder -tAc \
  "select count(*) from leads where response_body like '%rehearsal-pw-123%' or request_fingerprint like '%rehearsal-pw-123%'" 2>/dev/null)
[ "${LEAKPW:-0}" = "0" ] && ok "password absent from persisted lead data" || bad "PASSWORD LEAKED into lead row"

step "8. TRACKING — mock mode answers honestly"
for path in "warehouse-imports/KY4001103376087-2-4-%7Cs" "package-sealings/A1918106"; do
  C=$(curl -sk -H "Host: viporder.com.vn" -o "$R/logs/tr.json" -w '%{http_code}' -m 15 "$H/api/tracking/$path")
  case "$C" in
    200|503) ok "GET /api/tracking/$path -> $C (mock mode)" ;;
    *)      bad "GET /api/tracking/$path -> $C" ;;
  esac
done
HOSTILE=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 10 "$H/api/tracking/warehouse-imports/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
[ "$HOSTILE" = "400" ] && ok "hostile keyword -> 400 (no provider call)" || bad "hostile keyword -> $HOSTILE (expected 400)"
TRAV=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 10 "$H/api/tracking/warehouse-imports/..%2F..%2Fetc%2Fpasswd")
case "$TRAV" in 400|404) ok "path traversal -> $TRAV";; *) bad "path traversal -> $TRAV";; esac

step "9. SOURCE EXPOSURE — bait files planted in the REPOSITORY"
mkdir -p "$R/bait/backend/app" "$R/bait/docs" "$R/bait/.git"
printf 'SECRET_KEY=REHEARSAL-CANARY\n' > "$R/bait/backend/app/config.py"
printf '# internal\n' > "$R/bait/docs/SECURITY.md"
printf '[core]\n' > "$R/bait/.git/config"
printf 'CANARY=1\n' > "$R/bait/.env"
printf '{"name":"x"}\n' > "$R/bait/package.json"
EXPOSED=0
for p in /backend/app/config.py /docs/SECURITY.md /.git/config /.env /docker-compose.yml \
         /package.json /package-lock.json /playwright.config.js /tests/e2e/registration.spec.js /README.md; do
  C=$(curl -sk -H "Host: viporder.com.vn" -o "$R/logs/expo.body" -w '%{http_code}' -m 8 "$H$p")
  if [ "$C" = "200" ]; then EXPOSED=$((EXPOSED+1)); printf '        200 %s <-- EXPOSED\n' "$p"; fi
done
[ "$EXPOSED" = "0" ] && ok "no non-public path served (10 probed)" || bad "$EXPOSED non-public path(s) served"
CANARY=$(curl -sk -H "Host: viporder.com.vn" -m 8 "$H/backend/app/config.py" | grep -c "REHEARSAL-CANARY" || true)
[ "$CANARY" = "0" ] && ok "canary string never served" || bad "CANARY LEAKED"

step "10. PUBLIC ASSETS really shipped"
for p in / /robots.txt /sitemap.xml /static/css/style.css; do
  C=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 8 "$H$p")
  [ "$C" = "200" ] && ok "$p -> 200" || bad "$p -> $C"
done
JSFILE=$(basename "$(find "$R/site/static/js" -name 'app.js' | head -1)")
C=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 8 "$H/static/js/$JSFILE")
[ "$C" = "200" ] && ok "/static/js/$JSFILE -> 200" || bad "/static/js/$JSFILE -> $C"

step "11. RESTART — the service comes back"
OLDPID=$(cat "$R/logs/uvicorn.pid")
kill "$OLDPID" 2>/dev/null; sleep 2
start_app
sleep 6
NEWPID=$(cat "$R/logs/uvicorn.pid")
if kill -0 "$NEWPID" 2>/dev/null; then
  ok "restarted (pid $NEWPID, was $OLDPID)"
else
  bad "pid $NEWPID is not alive after restart"
fi
C=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 10 "$H/api/v1/health")
[ "$C" = "200" ] && ok "health after restart -> 200" || bad "health after restart -> $C"

step "12. ROLLBACK REHEARSAL — migrations down and back up"
( cd "$ROOT/backend" && DATABASE_URL="$DSN" "$VENV/bin/alembic" downgrade base >"$R/logs/down.log" 2>&1 ) \
  && ok "alembic downgrade base" || { bad "downgrade"; tail -3 "$R/logs/down.log"; }
LEFT=$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U viporder -d viporder -tAc \
  "select count(*) from information_schema.tables where table_schema='public' and table_name='leads'" 2>/dev/null)
[ "${LEFT:-1}" = "0" ] && ok "leads table removed by downgrade" || bad "leads table survived downgrade"
( cd "$ROOT/backend" && DATABASE_URL="$DSN" "$VENV/bin/alembic" upgrade head >"$R/logs/up2.log" 2>&1 ) \
  && ok "alembic upgrade head (re-apply)" || bad "re-apply failed"
C=$(curl -sk -H "Host: viporder.com.vn" -o /dev/null -w '%{http_code}' -m 10 "$H/api/v1/health")
[ "$C" = "200" ] && ok "health after rollback+reapply -> 200" || bad "health after rollback -> $C"

step "13. LOG INSPECTION — the run left a readable trail"
for f in "$R/logs/uvicorn.log" "$R/nginx/logs/access.log" "$R/nginx/logs/error.log"; do
  [ -s "$f" ] && ok "$(basename "$f"): $(wc -l < "$f" | tr -d ' ') line(s)" || skip "$(basename "$f") empty"
done
PWLOG=$(grep -c "rehearsal-pw-123" "$R/logs/uvicorn.log" 2>/dev/null || true)
[ "${PWLOG:-0}" = "0" ] && ok "password absent from application log" || bad "PASSWORD IN LOG ($PWLOG hit(s))"

echo
echo "############################################################"
printf "#  LOCAL PRODUCTION REHEARSAL: %d passed, %d failed, %d skipped\n" "$PASS" "$FAIL" "$SKIP"
echo "#  This is NOT staging and NOT production."
echo "############################################################"
[ "$FAIL" -eq 0 ]
