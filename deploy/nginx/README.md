# Nginx configuration — VIPORDER.COM.VN

## Files

| File | Installs to |
|---|---|
| `viporder.com.vn.conf` | `/etc/nginx/sites-available/viporder.com.vn.conf` |
| `proxy_params_viporder` | `/etc/nginx/proxy_params_viporder` |
| `viporder-security-headers.conf` | `/etc/nginx/snippets/viporder-security-headers.conf` |

```bash
install -d /etc/nginx/snippets
cp deploy/nginx/viporder-security-headers.conf /etc/nginx/snippets/
cp deploy/nginx/proxy_params_viporder      /etc/nginx/proxy_params_viporder
cp deploy/nginx/viporder.com.vn.conf       /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/viporder.com.vn.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

The repository filename and the installed filename are deliberately identical for
the snippet, so a mismatch is visible rather than silent.

---

## The trap this directory exists to avoid

**Nginx `add_header` does not merge across configuration levels.** The documented
rule is:

> These directives are inherited from the previous configuration level **if and
> only if there are no `add_header` directives defined on the current level**.

So a single `add_header Cache-Control …` inside a `location` block silently
cancels **every** server-level `add_header` for that location.

The homepage is served by `location = /`, which needs its own `Cache-Control`.
The first version of this configuration therefore shipped the HTML document with
**no CSP, no HSTS, no X-Frame-Options, no X-Content-Type-Options, no
Referrer-Policy and no Permissions-Policy** — while `curl -I` against any path
that did inherit looked perfectly correct.

Measured with real nginx 1.31.6, same config, only the include differing:

| Location | Without the include | With the include |
|---|---|---|
| `/` | HTTP 200, security headers **0 / 6** | HTTP 200, **6 / 6** |
| `/static/style.css` | HTTP 200, **0 / 6** | HTTP 200, **6 / 6** |

---

## Rules for editing this directory

1. **Never set a security header directly in a `location` block.** All six live in
   `viporder-security-headers.conf` and nowhere else. Two copies drift.
2. **Any `location` that declares its own `add_header` must also
   `include /etc/nginx/snippets/viporder-security-headers.conf;`** in that same
   block.
3. **Do not use `expires` together with an explicit `Cache-Control`.** `expires`
   emits its own `Cache-Control`, which produces two of them and lets a client
   pick either.
4. Run both checks before committing:

```bash
python3 tools/check_nginx_config.py     # enforces rules 1–3 (runs in CI)
nginx -t                                # real syntax validation
```

Rule enforcement is a **program**, not a comment: `tools/check_nginx_config.py`
fails the build if a location declares `add_header` without the include, or sets
a security header directly. It was negative-controlled against the exact bug
above before being trusted.

---

## What is verified, and what is not

**Verified locally (macOS, nginx 1.31.6):** configuration syntax parses; the six
security headers are present on `/`, a static asset and `robots.txt`; exactly one
`Cache-Control` per response.

**Not verified:** behaviour on the production host, TLS/OCSP stapling with a real
certificate, HTTP/2 negotiation, and the `/api/` proxy path (no backend was
running). `deploy/post-deploy-check.sh` covers these against the live domain.
