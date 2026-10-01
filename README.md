# VIPORDER.COM.VN

Public website for **VIPORDER** — China → Vietnam transport, official import and
customs declaration.

The legacy customer/order portal stays at
**[khachhang.viporder.com.vn](https://khachhang.viporder.com.vn)** and is **not**
rebuilt in this repository.

---

## The two customer paths

**A — New customer registration**

```
viporder.com.vn
  → VIPORDER backend
      → validate
      → persist lead (PENDING)
      → KHAIBAO9610 registration adapter
          → success : REGISTERED + external customer code
          → failure : FAILED, lead RETAINED
  → show result, offer login at khachhang.viporder.com.vn
```

**B — Existing customer login**

```
viporder.com.vn → redirect → khachhang.viporder.com.vn
```

> A lead is **never** lost because the provider was unavailable.

---

## Status

See **[docs/PROJECT-STATUS.md](docs/PROJECT-STATUS.md)** for measured gate status,
the verified baseline SHAs, and the list of genuinely external blockers.

**KHAIBAO9610 status: MOCK.** The real production registration contract has not
been supplied. No guessed endpoint is hard-coded anywhere.

---

## What exists today

```
index.html              homepage
static/css/style.css    styles
static/js/app.js        front-end behaviour
tools/check_site.py     CI: HTML, links, assets, a11y, SEO baseline, link rules
tools/check_repo_hygiene.py  CI: secrets, tracked data, oversized files
docs/PROJECT-STATUS.md  measured project status
.github/workflows/ci.yml     CI
```

Components added by later gates (backend service, analytics layer, SEO assets,
deployment configuration) are recorded in `docs/PROJECT-STATUS.md` as they land.
This section lists **only what is actually in the tree**.

---

## Development

No dependencies are required for the website checks:

```bash
python3 tools/check_site.py           # HTML, links, assets, a11y, SEO, link rules
python3 tools/check_repo_hygiene.py   # secrets, tracked data, oversized files
```

Both are the same commands CI runs. `check_site.py` enforces business rules, not
just syntax — for example, every existing-customer entry point must resolve to
`khachhang.viporder.com.vn`, and an unapproved `viporder.com.vn` subdomain fails
the build.

Serve the site locally with any static server, e.g.:

```bash
python3 -m http.server 8080
```

---

## Branch flow

```
main        production-ready, protected
develop     integration branch, protected
feature/*   one gate / one CR per branch
```

**Never commit directly to `main` or `develop`.** Every change arrives through a
pull request: issue → branch → implementation → tests → PR → CI → review → merge.

---

## Secrets

No credential belongs in this repository — it is public.

* Configuration comes from the environment. `.env` is git-ignored; `.env.example`
  documents the keys with empty values.
* Registration **passwords are never persisted, logged, or traced**, at any layer.
* `tools/check_repo_hygiene.py` and the gitleaks CI job both fail on
  secret-shaped content.
