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

Public pages render · robots/sitemap/CSS/JS are really served · **no non-public
repository path is served** · a valid warehouse code shows a customer-facing result ·
a valid sealing code does the same (its **mode must be selected** — the form
defaults to warehouse) · a **wrong** code shows the specific Vietnamese message and
the result region stays hidden · the login CTA points at the customer portal · the
registration form refuses an empty submit.
