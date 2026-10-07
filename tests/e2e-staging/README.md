# Staging acceptance — a real browser against a real deployment

`tests/e2e-staging/` drives the **same** application as `tests/e2e/`, but against a
**deployed host** rather than a local dev server. It is deliberately a separate
directory and a separate config, so the CI gate never depends on a staging box
existing.

## Run it

```bash
STAGING_URL=https://viporder.com.vn:18443 \
STAGING_HTTPS=1 \
STAGING_HOST=viporder.com.vn \
STAGING_IP=160.22.170.20 \
npx playwright test -c playwright.staging.config.js
```

## Three things that had to be got right, each learned by failing

1. **`channel: "chrome"` is required here.** Without it Playwright looks for the
   bundled headless shell; if that build is absent the tests die on
   `browserType.launch`, which reads as a staging failure and is not.

2. **The `Host` header cannot be set for navigation.** Chrome rejects it with
   `net::ERR_INVALID_ARGUMENT`. `--host-resolver-rules=MAP <name> <ip>` maps the
   name to the staging IP *inside this browser only* — the equivalent of
   `curl --resolve`, touching neither DNS nor `/etc/hosts`.

3. **`page.request` does NOT honour that mapping.** It is a separate HTTP stack, so
   a relative URL resolved by **real DNS** and the check silently hit a different
   server. Every check goes through `page.goto` for that reason.

## What it asserts

Public pages render · robots/sitemap/CSS/JS are really served — **content-type
and a marker string per asset, not just `status === 200`** · **no non-public
repository path is served** · a valid warehouse code shows a customer-facing
result · a valid sealing code does the same (its **mode must be selected** — the
form defaults to warehouse) · a **wrong** code shows the specific Vietnamese
message and the result region stays hidden · the login CTA points at the customer
portal · the registration form refuses an empty submit.

## Proving this suite can fail — the negative control

"Robots, sitemap, CSS and JS are really served" once asserted only
`status === 200` and `body.length > 50`. A host that answers **200 + index.html
for every path** satisfies that, so the check passed while none of the four
assets was served. Both halves of the fixed check are therefore shown to be
load-bearing:

```bash
# control 1 — HTML and text/html for every path -> the content-type assert fails
python3 tools/serve_html_for_everything.py --port 8231 &
STAGING_URL=http://127.0.0.1:8231 \
  npx playwright test -c playwright.staging.config.js -g "robots, sitemap"

# control 2 — the real content-type per extension, still index.html as the body
#             -> the MARKER assert fails
python3 tools/serve_html_for_everything.py --port 8232 --spoof-types &
STAGING_URL=http://127.0.0.1:8232 \
  npx playwright test -c playwright.staging.config.js -g "robots, sitemap"

# positive control — the same check against a server that serves the real files
python3 -m http.server 8233 --bind 127.0.0.1 &
STAGING_URL=http://127.0.0.1:8233 \
  npx playwright test -c playwright.staging.config.js -g "robots, sitemap"
```

MEASURED: control 1 -> `2 failed` (`content-type is not robots.txt`); control 2 ->
`2 failed` (`body is not robots.txt`, the content-type assert having passed);
positive control -> `2 passed`.
