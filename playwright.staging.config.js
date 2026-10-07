/* Playwright against a DEPLOYED staging host.
 *
 * WHY A SEPARATE CONFIG. `playwright.config.js` hardcodes
 * `baseURL: http://127.0.0.1:8123` and starts its own `webServer`, so it cannot be
 * pointed at a remote host without editing it — and editing it would make the CI
 * gate depend on a staging server that does not exist in CI. This config drives the
 * SAME specs against a real deployment instead.
 *
 * USAGE — use the HOSTNAME, never the bare IP:
 *   STAGING_URL=https://viporder.com.vn:18443 \
 *   STAGING_HTTPS=1 \
 *   STAGING_HOST=viporder.com.vn \
 *   STAGING_IP=160.22.170.20 \
 *   npx playwright test -c playwright.staging.config.js
 *
 * WHY THE HOSTNAME. `--host-resolver-rules` below maps the NAME to the staging
 * IP, and the staging vhost matches on `server_name`. With the bare IP as the
 * base URL the browser reaches the DEFAULT vhost instead, which 301s away —
 * MEASURED: `STAGING_URL=https://160.22.170.20:18443` makes all 16 staging tests
 * fail, which reads as a broken deployment and is a broken command line. (The
 * value is also in the certificate: it is self-signed for
 * `staging.viporder.com.vn`, so verification is skipped HERE AND NOWHERE ELSE.)
 *
 * `tests/e2e-staging/README.md` has always had the correct form; this comment
 * did not, and the comment is what gets copied.
 */
const { defineConfig, devices } = require("@playwright/test");

const BASE = process.env.STAGING_URL;
if (!BASE) throw new Error("STAGING_URL is required, e.g. https://viporder.com.vn:18443");

module.exports = defineConfig({
  testDir: "./tests/e2e-staging",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: BASE,
    ignoreHTTPSErrors: process.env.STAGING_HTTPS === "1",
    // The staging vhost answers to the PRODUCTION `server_name`, so the browser has
    // to send that name — but Chrome refuses a `Host` header set for a top-level
    // navigation (`net::ERR_INVALID_ARGUMENT`), and following the bare-IP default
    // server would 301 to the real domain. `--host-resolver-rules` maps the NAME to
    // the staging IP inside this browser only, which is what `curl --resolve` does
    // for command-line checks. Nothing on the machine or in DNS is touched.
    launchOptions: {
      args: [
        `--host-resolver-rules=MAP ${process.env.STAGING_HOST || "viporder.com.vn"} ${process.env.STAGING_IP || "160.22.170.20"}, MAP www.${process.env.STAGING_HOST || "viporder.com.vn"} ${process.env.STAGING_IP || "160.22.170.20"}`,
      ],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // `channel: chrome` uses the system Chrome, which is what the main suite does
    // locally. Without it Playwright looks for the bundled headless shell, and on
    // this machine `chromium_headless_shell-1194` is not installed — the tests then
    // fail on `browserType.launch`, which looks like a staging failure and is not.
    { name: "staging-desktop", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "staging-mobile", use: { ...devices["Pixel 7"], channel: "chrome" } },
  ],
});
