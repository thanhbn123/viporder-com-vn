# FINAL ACCEPTANCE — VIPORDER.COM.VN

**This document is the acceptance package.** It states what was measured, on what
revision, by what method — and what is still not true.

**MEASURED AT:** 2026-10-02 21:41 +0700
**CODE ACCEPTANCE CANDIDATE:** `d8689c7`
**EVERY NUMBER BELOW WAS MEASURED AT THAT SHA.** Nothing here is carried forward
from an earlier revision.

> ### Which SHA is the acceptance candidate?
>
> `d8689c7` is the **code** candidate: every test, rehearsal, guard and measurement
> in this document was run against it.
>
> The document cannot name its own merge as the candidate — publishing it moves the
> tip. So this was verified rather than asserted:
>
> ```
> git diff --name-only d8689c7..origin/develop
>   docs/FINAL-ACCEPTANCE.md          <- the only file
>
> git diff --name-only d8689c7..origin/develop | grep -cE '^(backend|static|deploy|tools|tests|index.html|404.html)'
>   0                                <- code, tests and deploy config: UNCHANGED
> ```
>
> The develop tip after this document is merged is therefore `d8689c7` **plus this
> file and nothing else**. If the tip ever contains a code or test change beyond
> that, this document is stale and must be re-measured — which is why the check is
> written down instead of the claim.

---

## 1. Repository

| | |
|---|---|
| `main` | `c0cbe28` — **untouched throughout; never merged into** |
| `develop` | **`d8689c7`** |
| Working tree | **CLEAN** |
| Remote branches | **2** — `main`, `develop` only |
| Merged pull requests | **43** — confirmed twice, by two independent methods (`limit 200` and the REST API), because a default page of 30 once made this number wrong |
| Required checks | **12** on both branches |
| CI at `d8689c7` | **completed / success** |

### PR #17 — the release candidate

| | |
|---|---|
| direction | `develop` → `main` |
| head | **`d8689c7`** |
| head == develop tip | **YES** (verified by SHA equality) |
| merge state | **CLEAN** |
| Open issues | **#4** only |

**PR #17 is open and NOT merged.** That is an instruction, not an oversight.

### Drift

**None unexplained.** `develop` moved from `87d5440` to `ae6c53a` during the
previous round, caused by PR #47 (this project's own owner-approval record), and
from `ae6c53a` to `d8689c7` by PR #48 this round. Both are accounted for by merge
commits in the history.

---

## 2. Tests — with the engine PROVEN, not inferred

A previous report of this project said *"backend (real PostgreSQL), 528 passed"*
when **only 12 of those tests were on PostgreSQL**; the shared fixture always built
SQLite. That is fixed, and these numbers are what the fix produced.

| Suite | Engine actually used | Passed | Failed | Skipped |
|---|---|---|---|---|
| `pytest` (default) | **SQLite** — proven via `engine.dialect.name`, `PRAGMA journal_mode` | **519** | **0** | 12 |
| `pytest` with `TEST_DATABASE_URL` | **PostgreSQL 16** — proven via `engine.dialect.name`, `SELECT version()` | **528** | **0** | 3 |
| JS logic (`node --test`) | n/a | **119** | **0** | 0 |
| Browser (Playwright) | **Chromium** only — see §6 | **75** | **0** | 0 |

**How the engine is proved, not assumed:**

* `conftest.py` prints `DATABASE_URL`, `TEST_DATABASE_URL`, the engine the default
  suite will actually use, and whether the PostgreSQL-only tests will run — **on
  every run**, with a warning when `DATABASE_URL` points at PostgreSQL while
  `TEST_DATABASE_URL` is unset.
* `test_engine_identity.py` asks the **connection** what it is rather than trusting
  the configured URL, and fails if a run declaring SQLite is PostgreSQL.
* The shared harness now isolates on **either** engine: SQLite gets a throwaway
  file, PostgreSQL gets a unique `search_path` schema created before `create_all`
  and dropped at session end.

**Browsers:** `desktop-chromium` and `mobile-chromium` (Pixel 7). **Firefox and
WebKit are NOT supported at this revision** — CI runs
`npx playwright install --with-deps chromium`, and the `firefox-1495` build present
on the preparation machine failed with `Library not loaded: @rpath/libmozglue.dylib`
(a corrupt download). No cross-browser claim is made.

---

## 3. Local production rehearsal

**`tools/local_rehearsal.sh` — 46 passed, 0 failed, 0 skipped.**

**This is a LOCAL PRODUCTION REHEARSAL. It is not staging and not production.**
There is no second host, no real certificate, no DNS and no real provider.

It builds the actual production stack on one machine:

* the **real publish command** — `rsync -a -r --delete --files-from=deploy/published-files.txt`
* the **real nginx site config**, only ports and paths rewritten
* a **real PostgreSQL 16 cluster**, `initdb` from nothing, migrated from zero
* uvicorn started exactly as the systemd unit starts it — `--workers 1 --proxy-headers`
* the real security-headers snippet

Thirteen steps: publish, database, application, nginx, health, security headers,
registration, tracking, source exposure, public assets, restart, rollback, logs.

Notable results:

* migrations reach **`0005_live_phone_rule (head)`**; `downgrade base` removes the
  table and `upgrade head` restores it, health 200 afterwards
* registration returns **201** with `lead_id`, `registration_status`,
  `external_customer_code`, `external_customer_id`, `login_url`; the lead row is
  persisted and **the password is absent from every log and from the lead data**
* tracking answers **503 in mock mode** — the honest answer — and a hostile keyword
  returns **400 without any provider call**
* **10 non-public paths probed with bait files planted: none served**, canary never
  leaked
* nginx sends **exactly one** HSTS header on `/api/*`, without `includeSubDomains`

---

## 4. The two defects that matter most from this round

### 4.1 `"528 passed on PostgreSQL"` meant 12 tests on PostgreSQL

The shared `harness` fixture — used by **18 test files** — built SQLite
unconditionally. `TEST_DATABASE_URL` reached only `test_postgres.py` and
`test_concurrency.py`, which read it themselves.

**The number was real; the label was not** — the same class of error as an earlier
green *"SQLite"* run that was secretly PostgreSQL, in the opposite direction.
**Twice is a structure, not a slip**, so the structure changed: see §2.

### 4.2 `/api/*` sent TWO HSTS headers, one with `includeSubDomains`

**Measured**, not inferred: `curl -D- /api/v1/health` returned two
`Strict-Transport-Security` headers. nginx was correctly staged to `max-age`
alone, but the **application** emitted its own header with `includeSubDomains`, and
nginx `add_header` **adds to** a proxied response rather than replacing it.

**HSTS is host-scoped, not path-scoped.** An `/api/*` response could therefore pin
**every subdomain for a year** — defeating the entire staged decision, whose whole
purpose is that `viporder.com.vn`, `www` and `khachhang` share one IP while the apex
certificate does not cover `khachhang`.

**Why it survived every previous check:** every earlier inspection — including the
security sweep and this project's own nginx work — looked at `/`, which nginx serves
from disk. **Only the proxy path is where both tiers speak.** The static guard and
the application were each individually correct; the defect lived in the seam.

**Fixed at both tiers** (either alone leaves the hole open), and the rehearsal now
asserts it on every run.

---

## 5. Deployment security

| | |
|---|---|
| Docker source exposure | **NOT RUN** — Docker is not installed on the preparation machine; `tools/check_compose.py` validates the compose/nginx **seam** (14 mounts, 4 configs, 0 errors) but the stack has never been executed |
| systemd source exposure | **PASS** — verified against real nginx with bait files planted in the repository |
| CSS/JS publish | **PASS** |
| `nginx -t` | **PASS** — real nginx 1.31.6 against the real site config |

The publish path was publishing `README.md`, `package.json`, `package-lock.json`,
`playwright.config.js`, `tests/` and **`node_modules/`** to the web root, because the
deploy copied the whole tree and subtracted a few names — a deny list, which
publishes new files **by default**. Now an explicit allow list
(`deploy/published-files.txt`), guarded by `tools/check_deploy_exposure.py`, which
also asserts that every local asset the pages reference is still covered — because
an allow list fails by **under**-publishing, and the site breaks.

`rsync --files-from` **cancels** the `-r` implied by `-a`: without an explicit `-r`
the directories are created and their contents are not, producing a site with no CSS
and no JS. The rehearsal checks for the assets explicitly.

**HSTS:** `max-age=31536000`, **`includeSubDomains` NOT sent**, `preload` NOT added.
Prerequisites before enabling it are recorded in `docs/SECURITY.md` §5.2.

---

## 6. Security

| | |
|---|---|
| Secret scan (gitleaks) | **no leaks found** |
| bandit | **0 high severity** |
| pip-audit | **no known vulnerabilities** |
| PII in analytics | **PASS** — `tracking_search` carries `search_type` + `result` only; the keyword cannot be sent because the function has no parameter for it |
| Password leakage | **PASS** — absent from the database, the application log, the nginx log and the persisted lead data |

---

## 7. Provider integration

**Owner-supplied and shipped:** base `https://apiviporder.com/frontend/v1`;
`POST /register` with `name, phone, email, password, confirmPassword, acceptTerms`;
`GET /warehouse-imports/{keyword}`; `GET /package-sealings/{keyword}`.

**Measured live, read-only, 2026-10-02:**

| Call | Status |
|---|---|
| `warehouse-imports/KY4001103376087` | **404** — the value in the original brief does not resolve |
| `warehouse-imports/KY4001103376087-2-4-\|s` | **200** — the **full** code is required |
| `package-sealings/A1918106` | **200** |
| `package-sealings/NOPE-123` | **404** |
| `warehouse-imports` (bare list) | **401 Unauthenticated** — keyword search needs no auth (OBSERVED) |

**The two envelopes differ:** `warehouse-imports` returns a **bare object**;
`package-sealings` returns **`{"data": {...}}`**.

**Live registration POST: NOT EXECUTED.** No safe test identity has been supplied,
and creating an uncontrolled customer account on the provider's production system is
not a decision this side can make. `tools/test_live_registration.py` is
**READY_FOR_ONE_SHOT_TEST**: one POST per invocation, no retry, password never
printed, refuses unless the flag, a complete identity and a non-marketing base URL
are all present. All four refusal paths executed; none sent anything.

**Issue #4 narrowed from 12 unknowns to 6.** Remaining: exact success response
(and whether it returns the customer code), duplicate response, validation-error
response, rate limit, timeout expectation, and whether registration needs auth.

---

## 8. Visual and accessibility

**OWNER VISUAL APPROVAL: APPROVED**, 2026-10-02, over the eleven screenshots in
`docs/visual-acceptance/`. **The approval is not claimed to be broader than it was:**
the items below are **not** discharged by it.

**AUTOMATED ACCESSIBILITY CHECKS: PASS** — 0 elements below WCAG AA, 0 below the
12px floor, focus changes rendering, 0 unnamed controls, 0 horizontal overflow.

Three real contrast/type defects were found and fixed by measurement: `"QUY TRÌNH"`
3.42:1 → **5.05:1**; the footer muted grey 3.91:1 → **5.37:1**; the header tagline
11px → **12px**. Narrowest margin on the page: **4.97:1** against a 4.5 requirement.

**Not covered, and stated as open:** the accessibility suite audits **the homepage
only** — `404.html` and every interaction-gated state were never contrast- or
type-audited; one viewport per audit; no breakpoint sweep, no tablet, no landscape,
no 200% zoom, **no screen-reader run**; the captures are of a staging **candidate**,
not a deployed host.

**No WCAG certification is claimed.**

---

## 9. What is NOT true

Stated plainly, because a green CI run does not make these true:

1. **Nothing is deployed.** No staging host, no production deploy, no DNS change,
   no contact with the live Apache host.
2. **No live registration POST has ever been made.** `MOCK` is not working
   registration.
3. **Docker has never been executed.** Every container statement is
   configuration-derived.
4. **Staging has never been verified**, because staging does not exist.
5. **The real provider contract is incomplete** — six items, issue #4.
6. **Only Chromium is supported.** No Firefox, no WebKit.
7. **More than one uvicorn worker is untested**; `--workers 1` is pinned because the
   rate limiter keeps counters in process memory.
8. **The `khachhang.viporder.com.vn` portal is a separate system** this project does
   not serve, own or control — it must be preserved, and it is the reason HSTS
   `includeSubDomains` is withheld.

---

## 10. Owner acceptance steps

**These are the only actions left, and every one needs the owner.**

1. **Provide a staging host** — IP/hostname, SSH access, and the values listed in
   `STAGING_HOST_REQUIREMENTS.md`. Optionally a DNS record such as
   `staging.viporder.com.vn`; **no record has been created**.
2. **Supply a safe test identity** (name, phone, email, password) if the single live
   registration is to be executed — otherwise the registration response contract
   stays unknown.
3. **Authorise the merge of PR #17** when satisfied. It is CLEAN at `d8689c7` with
   12/12 checks.
4. **Decide the DNS cutover method.** The plan recommends nginx **in front of**
   Apache, because all three hostnames share one IP, the apex certificate does not
   cover `khachhang`, and HSTS carries `includeSubDomains` for a year if it is ever
   enabled before that is proven.
5. **Provide the legal content** required for the registration flow.

---

## 11. Verdicts

| | |
|---|---|
| **REPOSITORY ACCEPTANCE** | **PASS** |
| **LOCAL PRODUCTION REHEARSAL** | **PASS** — 46/46 |
| **READY FOR STAGING DEPLOY** | **YES** — everything a staging host needs exists; only the host is missing |
| **STAGING ACCEPTANCE** | **NOT YET RUN** — no staging host exists |
| **READY FOR MAIN MERGE** | **YES** — PR #17 CLEAN at `d8689c7`, 12/12 checks; needs authorisation, not work |
| **READY FOR PRODUCTION CUTOVER** | **NO** — no staging evidence and no live registration evidence |
| **OWNER CAN BEGIN FINAL ACCEPTANCE** | **YES** |

**OWNER CAN BEGIN FINAL ACCEPTANCE = YES.** Every condition is met: develop is
clean, there is no unexplained drift, all unblocked repository work is complete, CI
is fully green, SQLite and PostgreSQL are both **proven** by their engines, the
browser suite is green, the deployment-exposure guards are green, the live GETs were
measured, the registration mapping is proven, the one-shot tool is ready, both
runbooks exist, the visual work is owner-approved, and PR #17 is aligned with
`develop`.

A real staging host and a real live registration remain **explicitly external**.
