# Project status — VIPORDER.COM.VN

**Repository:** `thanhbn123/viporder-com-vn`
**Owner:** thanhbn123
**Last updated:** 2026-10-02

This document records **measured** state, not intended state. Every SHA and
status label here was read from the remote with `git` / `gh api`, or produced by
a command whose output is quoted. A label is only ever one of `PASS`, `FAIL`,
`BLOCKED` or `NOT STARTED`.

---

## 1. Purpose

`viporder.com.vn` becomes the new public website for VIPORDER. The legacy
customer/order portal already lives at **`khachhang.viporder.com.vn`** and is
**not** being rebuilt here.

Two customer paths:

| Path | Journey |
|---|---|
| **A — New customer registration** | `viporder.com.vn` → VIPORDER backend → validate → persist lead → KHAIBAO9610 adapter → real customer code + VIPORDER lead |
| **B — Existing customer login** | `viporder.com.vn` → redirect → `khachhang.viporder.com.vn` |

---

## 2. Measured baseline (2026-10-01)

| Item | Value |
|---|---|
| `main` | `c0cbe28b9e6087433e7fa0e342f3814fa7c34efc` |
| `develop` | `8a097c0054507e6bf0150d399bce197f84298c22` |
| merge-base | `c0cbe28` (develop 2 ahead / 0 behind main) |
| PR #1 | MERGED into `develop` 2026-10-01T11:22:48Z, head `77dc7f55` |
| Default branch | `main` |
| Tracked tree on `develop` | `README.md` `.gitignore` `index.html` `static/css/style.css` `static/js/app.js` |

Owner-supplied SHAs **matched the remote exactly**.

### Drift found and corrected at G00

1. `default_branch` was `feature/g01-homepage-html`, **not** `main`.
2. `feature/g01-homepage-html` was an **orphan root commit**
   (`44a30ab34e2cccbe74776d56cdefcaf47248168a`, no parents, no common ancestor
   with `develop`) — the superseded v1 of the branch that became PR #1.
   `git diff --stat 44a30ab 77dc7f5` proved it carried **no unique work**
   (only *lacked* README content).
3. No CI workflows, no branch protection — nothing enforced the mandated flow.

Corrected: default branch set to `main`; orphan branch deleted after proving
redundancy (SHA retained above for recovery).

---

## 3. Gate status

| Gate | Scope | Status | Evidence |
|---|---|---|---|
| **G00** | Discovery / baseline, CI foundation | **PASS** | drift measured and corrected (§2); PR #5 → `9e851286` |
| **G01** | Homepage | **PASS** (pre-existing) | merged via PR #1; verified sound, **not rewritten** |
| **G02** | Registration + lead core | **PASS** | PR #11 → `df9b84ea`; replay CR PR #13 → `4d026def`. Live: exact browser payload → **201**; provider down → **202** `PENDING` with the lead **retained**; duplicate → **409** leaking nothing; **replay of a 409 returns 409 with a byte-identical body** |
| **G03** | Data / lead store | **PASS, PostgreSQL now executed** | PR #19, PR #24. Repository interface, Alembic `0001`–`0005`, `request_fingerprint`, consent columns, and an **in-flight phone claim** that makes "one attempt per phone at a time" a database invariant — so two concurrent registrations cannot both call the provider. The partial unique index is **proven on real PostgreSQL 16**, in CI: a second `REGISTERED` row for one phone is refused while a `PENDING` one is allowed |
| **G04** | KHAIBAO9610 integration contract | **BLOCKED_EXTERNAL** | adapter + MOCK shipped; contract not supplied — **issue #4**. `docs/KHAIBAO9610-INTEGRATION.md` |
| **G05** | Marketing / analytics | **PASS** | PR #10. All 7 events + `viporder_lead_pending`; UTM captured server-side. **No PII in any payload** — and this is now MACHINE-CHECKED (`tools/js/no-pii-in-analytics.test.js`) rather than verified by reading, which is what this row previously claimed and was not true of. The property is structural: `analytics.js` never names a customer field, so an event cannot carry one. The check is a source tripwire, so it cannot see a value passed in under a neutral name, and it does not read the two third-party loaders' own code. Tracking **dormant** |
| **G06** | SEO / public website | **PASS** | OG/Twitter, static JSON-LD, `sitemap.xml`, `robots.txt`, favicon + OG image (1200×630), branded **404** served with a real 404 status and 6/6 headers (PR #21). JSON-LD is parsed by CI |
| **G07** | UX / conversion | **PASS** | two primary actions, accessible mobile nav, success/pending/error states. **Mobile not visually verified** (no browser) |
| **G08** | Security | **PASS, with named residuals** | `docs/SECURITY.md`. Four real defects were found by adversarial review and fixed: password in exception tracebacks; a cross-customer data leak via a reused idempotency key; nginx dropping **all** security headers from the homepage (0/6 → 6/6); and `--workers 2` doubling the in-process rate limit while the app-level proxy setting implied a control it did not provide |
| **G09** | Testing | **PASS** | **390** backend tests (378 run without a database, 12 skip) plus **67 browser tests**. The backend suite includes 7 PostgreSQL schema tests and 5 concurrency tests; the browser suite covers the three pieces of page logic that decide what is SENT, what is CLASSIFIED and what the customer is TOLD. Plus 3 CI-enforced repo tools. Negative controls run for the site checker, the nginx guard, the deploy check, and by mutation for the backend's own tests |
| **G10** | CI | **PASS** | `.github/workflows/ci.yml` — **10 jobs producing 11 required checks** (the Python matrix is two), all pinned to the PR **head SHA** and all required before merge. Includes a PostgreSQL service and a negative control for the destructive-test guard |
| **G11** | Documentation | **PASS** | `docs/` — architecture, flow, integration, deployment, security, go-live |
| **G12** | Staging | **PASS (config only)** | PR #6, plus CR PR #15 (`--workers 1`, pinned `--forwarded-allow-ips`, upstream made deployment-specific). **Nothing deployed** — no VPS or DNS access. **Docker was never executed** |
| **G13** | Release | **BLOCKED_EXTERNAL** | release PR (`develop` → `main`) opened as a **candidate only**, awaiting owner authorization. Staging acceptance cannot be performed without infrastructure, and KHAIBAO9610 is MOCK |

Statuses in this table are updated by the gate that changes them. If a row and a
PR disagree, **the PR is wrong** — re-measure. A gate is `PASS` only when the
command output that proves it is quoted in the gate's PR.

### What `PASS` does not mean here

`PASS` means *the stated mechanism was measured to work in the environment named
in the Evidence column*. It is not a claim about production. **Four** things are
deliberately **not** covered — and this sentence said "three" while the table
below it listed four, which is the same class of error as the numbers this audit
was written to find. A limit list that undercounts itself is a limit list that
gets skimmed.

| Not verified | Why it matters |
|---|---|
| **The real KHAIBAO9610 API** | No live call has ever been made from this repository. **`MOCK` is not working registration.** |
| **A real browser at real breakpoints** | The mobile nav, the layout and the 404 page were reviewed as code and through DOM stubs, never rendered. No session here had image input — the OG image was measured geometrically but never *looked at*. |
| **Docker / docker-compose** | Docker is not installed on the machine this was built on. The compose file is validated as configuration (YAML parses, every key is a real setting, defaults are safe) but the stack has **never been run**. |
| **More than one uvicorn worker** | Both deployment paths pin `--workers 1`. The concurrency guarantees — the phone claim and the idempotency binding — are verified within a process (sync handlers run in a threadpool, so the races are real), but they have **not** been exercised across multiple workers. Raise the worker count only after re-running `tests/test_concurrency.py` against that topology. |

**Closed since the first revision of this document:**

* **PostgreSQL.** Recorded as "never executed" for most of this project. It is now
  migrated, asserted and enforced in CI on every pull request
  (`backend/tests/test_postgres.py`), including the partial unique index the whole
  lead design rests on.
* **The duplicate provider call under concurrency.** PR #23 stopped a losing
  concurrent request from getting a 500, but noted honestly that **both requests
  had already called the provider** — so two customers could exist upstream with
  one recorded here. PR #24 makes "one attempt in flight per phone" a database
  invariant, so the second attempt is refused **before** the provider is reached.
  That bullet said the residual "is not fixed here"; it now is, and the sentence
  is kept so the history of the claim is visible rather than quietly edited away.

  **A number in this paragraph has been RETRACTED.** It read "Measured: **1 call
  instead of 6** for one phone under six concurrent registrations." CI later
  failed with `the provider was called 2 time(s) for one phone` while twelve
  local runs passed, so the figure was true of a fast machine and false in
  general. The first implementation guarded a **window** rather than a **rule**
  (two indexes, one per state, with a gap between them); `0005_live_phone_rule`
  replaced them with one. The guarantee is now proven **deterministically** by
  `backend/tests/test_repository.py::test_a_registered_phone_cannot_be_claimed_by_a_new_attempt`
  — no threads, no PostgreSQL — rather than by a timing-dependent count. A
  measured number that only holds on one machine is not evidence, which is why
  the replacement test does not measure timing at all.

---

## 4. Verification commands

```bash
# Site checks: HTML, links, assets, a11y, SEO baseline, business link rules
python tools/check_site.py

# Nginx add_header inheritance guard (nginx silently drops server-level headers
# in any location that declares its own — this once cost the whole set)
python tools/check_nginx_config.py

# Repository hygiene: secrets, tracked runtime/lead data, oversized files
python tools/check_repo_hygiene.py

# Backend test suite
cd backend && python -m pytest -q
```

External verification, after a deploy (it measures over the public internet and
does **not** trust `systemctl status`):

```bash
./deploy/post-deploy-check.sh https://viporder.com.vn
```

A `PASS` in this document is only valid together with the command output that
produced it.

### Measurements actually taken (01/10/2026)

| Measurement | Result |
|---|---|
| `pytest -q` (backend, SQLite) | `378 passed, 12 skipped` |
| `pytest -q` (backend, PostgreSQL wired in) | `390 passed` |
| `node --test "tools/js/*.test.js"` | `41 passed` |
| `check_site.py` / `check_nginx_config.py` / `check_repo_hygiene.py` | 0 errors each |
| CI checks on merged PR heads | **10 / 10** green (9 jobs), all required before merge |
| Live POST, exact browser payload | `201`, lead row written, no password in DB bytes or logs |
| Live POST, provider down | `202` `PENDING`, **lead retained** |
| Live POST, same `Idempotency-Key` + different phone | `409 IDEMPOTENCY_KEY_REUSED`, no other customer's data |
| `pytest tests/test_postgres.py` against real PostgreSQL 16 | `7 passed` |
| `pytest tests/test_concurrency.py` against real PostgreSQL 16 | `5 passed` |
| Provider calls for one phone under 6 concurrent registrations | **1** (was **6** before the claim) |
| nginx serving a mistyped URL | `404` with the branded body and `6/6` security headers |
| Live same-origin POST through the dev proxy | `201` |
| Live replay of a `409` (same key, same body) | `409` with a byte-identical body |
| nginx security headers on the homepage | `6/6` (was **0/6** before PR #12) |

---

## 5. Known external blockers

| # | Blocker | Why it is external | Impact |
|---|---|---|---|
| 1 | Real KHAIBAO9610 / `apiviporder.com` registration contract | Only the owner/provider can supply it | Real customer registration stays in MOCK. Issue #4 |
| 2 | DNS + VPS access for `viporder.com.vn` | Owner-held | Nothing can be deployed. Prep only. |
| 3 | Final legal/contact content (company name, address, phone) | Business-authoritative | Footer/contact cannot be completed truthfully |

Blocked gates do not stop other gates — see the gate table.

---

## 6. What "production ready" means here

Production **READY** may only be claimed when *all* of the following hold, each
with measured evidence:

- every required CI job green on the **release PR head**;
- no unexplained drift from the verified baseline;
- registration works end to end in MOCK and the failure path retains the lead;
- security review passed;
- staging acceptance passed;
- **KHAIBAO9610 integration status reported truthfully** — while issue #4 is
  open, the site may be released, but real registration must be described as
  MOCK, never as working.
