# Project status — VIPORDER.COM.VN

**Repository:** `thanhbn123/viporder-com-vn`
**Owner:** thanhbn123
**Last updated:** 2026-10-01

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
| **G00** | Discovery / baseline, CI foundation | **PASS** | drift measured and corrected; `tools/check_site.py` + `tools/check_repo_hygiene.py` green; CI workflow added |
| **G01** | Homepage | **PASS** (pre-existing) | merged via PR #1; verified sound, not rewritten |
| **G02** | Registration + lead core | **NOT STARTED** | — |
| **G03** | Data / lead store | **NOT STARTED** | — |
| **G04** | KHAIBAO9610 integration contract | **BLOCKED_EXTERNAL** | contract not supplied — issue #4 |
| **G05** | Marketing / analytics | **NOT STARTED** | — |
| **G06** | SEO / public website | **NOT STARTED** | — |
| **G07** | UX / conversion | **NOT STARTED** | — |
| **G08** | Security | **NOT STARTED** | — |
| **G09** | Testing | **NOT STARTED** | — |
| **G10** | CI | **PASS** (baseline jobs only) | `.github/workflows/ci.yml` — backend jobs added by G02 |
| **G11** | Documentation | **NOT STARTED** | — |
| **G12** | Staging | **NOT STARTED** | — |
| **G13** | Release | **BLOCKED_EXTERNAL** | release PR not opened; production READY not claimed |

Statuses in this table are updated by the gate that changes them. If a row and a
PR disagree, **the PR is wrong** — re-measure. A gate is `PASS` only when the
command output that proves it is quoted in the gate's PR.

---

## 4. Verification commands

Commands that exist **today**:

```bash
# Site checks (HTML, links, assets, a11y, SEO baseline, business link rules)
python tools/check_site.py

# Repository hygiene (secrets, tracked data, oversized files)
python tools/check_repo_hygiene.py
```

Added by later gates, and listed here only once they exist:

```bash
# Backend test suite — lands with G02
cd backend && python -m pytest -q
```

A `PASS` in this document is only valid together with the command output that
produced it.

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
