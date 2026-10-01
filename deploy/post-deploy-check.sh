#!/usr/bin/env bash
# =============================================================================
# VIPORDER.COM.VN — post-deployment verification
# =============================================================================
# Run this AFTER a deploy and BEFORE reporting success. It measures the site
# from the outside, over the public internet, which is the only vantage point
# that matters. `systemctl status` says a process exists; this says the site
# actually works.
#
#   ./deploy/post-deploy-check.sh https://viporder.com.vn
#
# Exit 0 = every check passed. Exit 1 = at least one failed.
#
# The registration probe deliberately sends an INVALID payload: it proves
# validation is live without creating a real customer.
# =============================================================================

set -uo pipefail

BASE="${1:-https://viporder.com.vn}"
BASE="${BASE%/}"
PORTAL="https://khachhang.viporder.com.vn"
TIMEOUT=15

PASS=0
FAIL=0
WARN=0

c_pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); }
c_fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
c_warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; WARN=$((WARN + 1)); }
section() { printf '\n== %s ==\n' "$1"; }

command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

# Return the HTTP status code, or exactly "000" when the request never
# completed. Without this helper, `curl -w '%{http_code}' ... || echo 000`
# prints "000000" on failure — the fallback appends to what curl already wrote.
http_code() {
  local out
  out="$(curl -s -o /dev/null -w '%{http_code}' -m "$TIMEOUT" "$@" 2>/dev/null)" || true
  if [ -z "$out" ] || [ "$out" = "000" ]; then
    printf '000'
  else
    printf '%s' "$out"
  fi
}

# ---------------------------------------------------------------------------
section "Reachability"
# ---------------------------------------------------------------------------
code="$(http_code "$BASE/")"
if [ "$code" = "200" ]; then c_pass "GET $BASE/ → 200"; else c_fail "GET $BASE/ → $code"; fi

# ---------------------------------------------------------------------------
section "Health endpoint"
# ---------------------------------------------------------------------------
health_body="$(curl -s -m "$TIMEOUT" "$BASE/api/v1/health" || true)"
health_code="$(http_code "$BASE/api/v1/health")"
if [ "$health_code" = "200" ]; then
  c_pass "GET /api/v1/health → 200"
  if printf '%s' "$health_body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    c_pass "health reports status=ok"
  else
    c_fail "health body does not report status=ok: $health_body"
  fi
  provider_mode="$(printf '%s' "$health_body" | sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [ -n "$provider_mode" ]; then
    printf '  \033[36mINFO\033[0m  provider mode = %s\n' "$provider_mode"
    [ "$provider_mode" = "mock" ] && printf '  \033[36mINFO\033[0m  provider is MOCK — real registration is NOT live\n'
  fi
else
  c_fail "GET /api/v1/health → $health_code"
fi

# ---------------------------------------------------------------------------
section "HTTP → HTTPS"
# ---------------------------------------------------------------------------
host="${BASE#https://}"
redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -m "$TIMEOUT" "http://$host/" || echo "000 ") "
if printf '%s' "$redirect" | grep -q '^30[18]'; then
  c_pass "http://$host/ redirects (${redirect})"
else
  c_fail "http://$host/ did not redirect: ${redirect}"
fi

# ---------------------------------------------------------------------------
section "TLS"
# ---------------------------------------------------------------------------
if curl -s -o /dev/null -m "$TIMEOUT" --fail "$BASE/" >/dev/null 2>&1; then
  if curl -sI -m "$TIMEOUT" "$BASE/" | grep -qi '^strict-transport-security:'; then
    c_pass "Strict-Transport-Security present"
  else
    c_fail "Strict-Transport-Security missing"
  fi
  expiry="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null \
            | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
  if [ -n "$expiry" ]; then
    c_pass "TLS certificate valid until: $expiry"
  else
    c_warn "could not read the certificate expiry (openssl unavailable?)"
  fi
else
  c_fail "TLS handshake to $host:443 failed"
fi

# ---------------------------------------------------------------------------
section "Security headers"
# ---------------------------------------------------------------------------
headers="$(curl -sI -m "$TIMEOUT" "$BASE/" || true)"
check_header() {
  if printf '%s' "$headers" | grep -qi "^$1:"; then c_pass "$1 present"; else c_fail "$1 missing"; fi
}
check_header "X-Content-Type-Options"
check_header "X-Frame-Options"
check_header "Referrer-Policy"
check_header "Content-Security-Policy"

# ---------------------------------------------------------------------------
section "Existing-customer path (BUSINESS RULE)"
# ---------------------------------------------------------------------------
home="$(curl -s -m "$TIMEOUT" "$BASE/" || true)"
if printf '%s' "$home" | grep -q "$PORTAL"; then
  c_pass "homepage links to the customer portal $PORTAL"
else
  c_fail "homepage does NOT link to $PORTAL"
fi

portal_code="$(http_code "$PORTAL")"
if [ "$portal_code" = "200" ] || [ "$portal_code" = "302" ] || [ "$portal_code" = "301" ]; then
  c_pass "customer portal $PORTAL reachable ($portal_code)"
else
  c_warn "customer portal returned $portal_code (a third-party host — investigate, do not hotfix)"
fi

# ---------------------------------------------------------------------------
section "Crawlability"
# ---------------------------------------------------------------------------
robots="$(curl -s -m "$TIMEOUT" "$BASE/robots.txt" || true)"
if printf '%s' "$robots" | grep -qi 'sitemap:'; then
  c_pass "robots.txt declares a sitemap"
else
  c_fail "robots.txt missing or does not declare a sitemap"
fi

sitemap_code="$(http_code "$BASE/sitemap.xml")"
if [ "$sitemap_code" = "200" ]; then c_pass "sitemap.xml → 200"; else c_fail "sitemap.xml → $sitemap_code"; fi

# ---------------------------------------------------------------------------
section "Registration endpoint validates (safe probe: invalid payload)"
# ---------------------------------------------------------------------------
probe_code="$(http_code \
  -X POST "$BASE/api/v1/registrations" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: post-deploy-probe-invalid' \
  -d '{"full_name":"","phone":"not-a-phone","password":"short","consent":false}')"
case "$probe_code" in
  400|422) c_pass "invalid payload rejected with $probe_code (validation is live, no customer created)" ;;
  429)     c_warn "rate limited ($probe_code) — validation not exercised on this run" ;;
  000)     c_fail "registration endpoint unreachable" ;;
  *)       c_fail "invalid payload returned $probe_code, expected 400 or 422" ;;
esac

# ---------------------------------------------------------------------------
section "Summary"
# ---------------------------------------------------------------------------
printf '  PASS=%d  FAIL=%d  WARN=%d\n' "$PASS" "$FAIL" "$WARN"
if [ "$FAIL" -eq 0 ]; then
  printf '  \033[32mDEPLOYMENT VERIFIED\033[0m (%s)\n' "$BASE"
  exit 0
fi
printf '  \033[31mDEPLOYMENT NOT VERIFIED\033[0m — %d check(s) failed\n' "$FAIL"
printf '  Consider rolling back to the previous release SHA.\n'
exit 1
