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
index.html               homepage
404.html                 branded error page (noindex, served with a real 404)
robots.txt               crawl policy, points at the sitemap
sitemap.xml              the pages that exist

static/css/style.css     styles
static/js/app.js         page behaviour: mobile nav, footer year, click hooks
static/js/register.js    the registration form: validation, idempotency, states
static/js/analytics.js   first-party event layer (dormant; no third-party egress)
static/img/              favicon and Open Graph assets

backend/                 FastAPI service: registration, lead store, provider adapter
  app/                   routers, services, repositories, providers, middleware
  alembic/versions/      migrations 0001..0003
  tests/                 the suite, including the PostgreSQL and concurrency tests

deploy/                  nginx, systemd, Docker, env template, go-live check
docs/                    architecture, registration flow, integration, security,
                         deployment, go-live checklist, measured project status
tools/                   CI guards: site, repo hygiene, nginx config, brand images
.github/workflows/ci.yml 10 CI jobs, all required before merge
```

This list is the repository, not an aspiration. If something is here it exists;
if it is not here, it does not.

## Running the checks

```bash
# Website (no dependencies)
python3 tools/check_site.py            # HTML, links, a11y, SEO, business link rules
node --test "tools/js/*.test.js"       # registration failure handling (no deps)
python3 tools/check_nginx_config.py    # add_header inheritance guard
python3 tools/check_repo_hygiene.py    # secrets, tracked data, oversized files

# Backend
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                    # 371 tests; the PostgreSQL ones skip

# Backend against a real PostgreSQL (see backend/tests/test_postgres.py)
TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/viporder_test \
  python -m pytest tests/test_postgres.py tests/test_concurrency.py -q
```

Local development with the browser seeing a single origin, exactly as production
does behind nginx:

```bash
cd backend && uvicorn app.main:app --port 8000     # terminal 1
python3 tools/dev_server.py                        # terminal 2 -> :8080
```

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
