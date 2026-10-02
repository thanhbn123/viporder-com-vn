/* Playwright configuration for the VIPORDER.COM.VN browser gate (G12A).
 *
 * TWO SERVERS, ONE ORIGIN. The tests drive `tools/dev_server.py`, which serves
 * the repository root and reverse-proxies `/api/*` to the backend — so the
 * browser sees a single origin, exactly as it will behind nginx in production.
 * CORS is deliberately disabled in the application, so testing the pages and the
 * API on two origins would test a configuration that does not ship.
 *
 * Everything here is DETERMINISTIC:
 *   - the provider is the MOCK adapter, never the live one;
 *   - failure scenarios are chosen by PHONE SUFFIX, not by timing or by restarting
 *     the server between tests (see backend/app/providers/mock.py):
 *         ...0000 duplicate · ...0001 invalid · ...0002 unavailable
 *         ...0003 timeout   · ...0004 error   · anything else: success
 *   - the database is a throwaway file under `.tmp-e2e/`, deleted before the run.
 *
 * RATE LIMITING IS OFF, and that is deliberate rather than convenient: the E2E
 * suite sends many registrations from one IP on purpose, and the limiter would
 * turn an intended assertion into a 429. The limiter has its own tests
 * (backend/tests/test_security.py) and its own configuration check.
 */

"use strict";

const { defineConfig, devices } = require("@playwright/test");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = __dirname;
const PORT = Number(process.env.E2E_PORT || 8123);
const API_PORT = Number(process.env.E2E_API_PORT || 8124);
const DB_DIR = path.join(ROOT, ".tmp-e2e");
const DB_PATH = path.join(DB_DIR, "e2e.sqlite3");

/* A fresh database per run. Left in place afterwards for inspection when a run
 * fails; `.tmp-e2e/` is git-ignored. */
fs.mkdirSync(DB_DIR, { recursive: true });
for (const suffix of ["", "-wal", "-shm"]) {
  try {
    fs.unlinkSync(DB_PATH + suffix);
  } catch {
    /* absent is the normal case */
  }
}

/* The interpreter: the project's virtualenv if one exists, otherwise `python3`.
 * Never bare `python` — it does not exist on macOS and is not guaranteed in CI. */
const PY = fs.existsSync(path.join(ROOT, ".venv", "bin", "python"))
  ? path.join(ROOT, ".venv", "bin", "python")
  : "python3";

/* Which browser binary to drive.
 *
 * Default: Playwright's own bundled Chromium, which is what CI installs.
 * `E2E_CHANNEL=chrome` drives the Chrome already on the machine instead, which
 * needs no download at all.
 *
 * That escape hatch exists because a ~130 MB browser fetch from Playwright's CDN
 * stalled repeatedly on the network this was developed on — twice for 20 minutes
 * with no bytes moving. A browser gate that cannot run because a download hangs is
 * not a gate, and the point of this workstream is to have one that RUNS. CI has a
 * reliable network and uses the bundled build, so the thing CI tests is the thing
 * the suite is written for; the channel is only a local convenience.
 */
const CHANNEL = process.env.E2E_CHANNEL || undefined;

const BACKEND_ENV = [
  `DATABASE_URL=sqlite:///${DB_PATH}`,
  "AUTO_CREATE_SCHEMA=true",
  "KHAIBAO9610_MODE=mock",
  "MOCK_PROVIDER_BEHAVIOUR=success",
  "KHAIBAO9610_TIMEOUT_SECONDS=1",
  "RATE_LIMIT_ENABLED=false",
  "ADMIN_API_TOKEN=",
  "ENABLE_API_DOCS=",
  "LOG_LEVEL=WARNING",
].join(" ");

module.exports = defineConfig({
  testDir: path.join(ROOT, "tests", "e2e"),
  /* A browser gate is only useful if a failure is reproducible, so no retries:
   * a flake should be visible, not absorbed. */
  retries: 0,
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : [["list"]],
  outputDir: path.join(ROOT, "test-results"),

  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: CHANNEL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    ...devices["Desktop Chrome"],
  },

  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], channel: CHANNEL },
      testIgnore: /mobile\.spec\.js$/,
    },
    {
      name: "mobile-chromium",
      /* Pixel 7 is a real, common Vietnamese-market Android profile rather than a
       * hand-picked width, so the layout is exercised at a size that exists. */
      use: { ...devices["Pixel 7"], channel: CHANNEL },
      testMatch: /mobile\.spec\.js$/,
    },
  ],

  webServer: [
    {
      command: `cd backend && ${BACKEND_ENV} "${PY}" -m uvicorn app.main:app --port ${API_PORT} --log-level warning`,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `"${PY}" tools/dev_server.py --port ${PORT} --api http://127.0.0.1:${API_PORT}`,
      url: `http://127.0.0.1:${PORT}/index.html`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
