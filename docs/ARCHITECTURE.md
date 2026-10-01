# Architecture — VIPORDER.COM.VN

**Revision documented:** the tree at `origin/develop` `4d026def`
(this document is committed on top of it, without changing it)

> Values in the examples below are placeholders. Real tokens, lead IDs and
> customer codes are never reproduced in this repository.

This document describes the system **as it is built at that revision**. Every
non-obvious claim carries a `file:line` anchor. Where something is not present or
not exercised, it is stated — see §9.

Nothing here is a status claim. A mechanism being described does not mean it has
been exercised in production; §9 lists what has not been verified.

---

## 1. What runs where

### 1.1 The tiers

```
                    ┌──────────────────────────────────────────────┐
  browser ────────► │ nginx  (TLS termination, static files,       │
                    │         reverse proxy to the app)            │
                    └───────┬──────────────────────┬───────────────┘
                            │ /                    │ /api/
                            ▼                      ▼
              /srv/viporder/site            127.0.0.1:8000
              (static files, served         uvicorn, 1 worker
               directly by nginx)            app.main:app
                                                    │
                                                    ▼
                                            leads table
                                     SQLite (dev) / PostgreSQL (prod)
```

| Tier | What it is | Where it is defined |
|---|---|---|
| nginx | TLS termination, static file serving, reverse proxy to the app | `deploy/nginx/viporder.com.vn.conf` |
| Static site | Files served verbatim by nginx from `/srv/viporder/site` | `index.html`, `static/css/style.css`, `static/js/{app,register,analytics}.js` |
| Application (ASGI) | FastAPI app under uvicorn, bound to loopback only | `backend/app/main.py:151`, `deploy/systemd/viporder-web.service:48-55` |
| Persistence | SQLAlchemy engine; SQLite in dev, PostgreSQL in production | `backend/app/db.py:35-54`, `backend/app/db.py:57-104` |

**The homepage is static.** There is no server-side template rendering, no
templating dependency in `backend/requirements.txt`, and the app registers no
route that returns HTML. nginx serves the site from disk: `root /srv/viporder/site`
(`deploy/nginx/viporder.com.vn.conf:111`) and `try_files /index.html =404` for
`location = /` (`:137-139`).

**The app is not reachable from outside.** uvicorn binds `--host 127.0.0.1`
(`deploy/systemd/viporder-web.service:49`) and nginx proxies to
`upstream viporder_app { server 127.0.0.1:8000 ... }`
(`deploy/nginx/viporder.com.vn.conf:29-32`, used at `:119` and `:129`). The
systemd unit states the intent: "Binds to loopback only — Nginx is the only thing
that may talk to it" (`deploy/systemd/viporder-web.service:29`).

The API is mounted under `/api/` and is a separate concern from the static files.
Its routes are `/api/v1/health`, `/api/v1/registrations`,
`/api/v1/registrations/{lead_id}`,
`/api/v1/admin/registrations/{lead_id}/retry`, plus the optional API docs
(`backend/app/main.py:65-66`, `backend/app/routers/health.py:22`,
`backend/app/routers/registrations.py:12`, `backend/app/routers/admin.py:30`).

### 1.2 The API process

`create_app()` (`backend/app/main.py:43-129`) is a factory, not module-level
wiring. Everything the app needs is constructed inside it and parked on
`app.state` (`backend/app/main.py:70-73`):

- `app.state.settings` — the pydantic-settings object (`backend/app/config.py:48-149`)
- `app.state.database` — the `Database` wrapper owning the engine (`backend/app/db.py:57`)
- `app.state.provider` — the selected `RegistrationProvider` (`backend/app/providers/factory.py:19-36`)
- `app.state.repository_factory` — a zero-argument callable returning a fresh
  `SqlAlchemyLeadRepository` bound to a new session (`backend/app/main.py:73`)

The factory exists so a test can build an app with a fake provider, a temp
database and rate limiting off, without monkeypatching import state — stated as
the reason in the module docstring (`backend/app/main.py:1-6`) and demonstrated
by the test fixture (`backend/tests/conftest.py:132-140`).

Schema creation is conditional: `AUTO_CREATE_SCHEMA` defaults to `True`, so a
bare `uvicorn app.main:app` works with no setup step; a deployment where Alembic
owns the schema sets it to `no` (`backend/app/config.py:79`,
`backend/app/main.py:75-77`). Both migrations are written to be no-ops when the
tables already exist (`backend/alembic/versions/0001_create_leads.py:33-46`,
`backend/alembic/versions/0002_consent_and_fingerprint.py:44-67`), so the two
paths do not fight.

**API docs are gated.** `/api/docs` and `/api/openapi.json` enumerate every route,
parameter and error code, so they are off when `APP_ENV=production` unless an
operator overrides `ENABLE_API_DOCS` (`backend/app/config.py:131-143`,
`backend/app/main.py:60-66`). Measured:

```
APP_ENV=production  /api/docs              -> 404
APP_ENV=production  /api/openapi.json      -> 404
APP_ENV=production  /api/v1/health         -> 200
APP_ENV=development /api/docs              -> 200
APP_ENV=development /api/openapi.json      -> 200
```

ReDoc is never served (`redoc_url=None`, `backend/app/main.py:67`).

### 1.3 Server process configuration, and two consequences

Both deployment paths run a **single uvicorn worker**, and the unit file
says so explicitly rather than by omission:

```
ExecStart=/srv/viporder/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips 127.0.0.1 \
    --no-server-header \
    --log-level info
```
(`deploy/systemd/viporder-web.service:48-55`; the container path passes
`--workers 1` at `deploy/Dockerfile:68`, and pins `--forwarded-allow-ips *` at
`:66`, which its own comment allows only because the compose service does not
publish the port, leaving the nginx container as the sole peer —
`deploy/Dockerfile:58-61`, and `deploy/docker-compose.yml:58-62` for the
`expose` that keeps it unpublished.)

Two consequences follow, and both are measurements rather than inferences.

**(a) The rate limit is not multiplied.** The limiter's state is per-process
(§3.3), so N workers hold N independent counters and the effective limit becomes
N × `RATE_LIMIT_ATTEMPTS`. With `--workers 1` a single counter owns the limit, so
the observable value matches `RATE_LIMIT_ATTEMPTS`. The unit file records the
trade explicitly — "a correct limit matters more than a second worker" — and says
to raise the count only after moving the limiter to a shared store
(`deploy/systemd/viporder-web.service:31-39`). This is a configuration guarantee,
not a property of the limiter.

**(b) `--proxy-headers` — not the app's `TRUST_PROXY_HEADERS` — is the operative
proxy control.** uvicorn rewrites `scope["client"]` and `scope["scheme"]` from
`X-Forwarded-For` / `X-Forwarded-Proto` *before* the application sees the request.
uvicorn believes those headers only from a peer listed in
`--forwarded-allow-ips` (default `'127.0.0.1,::1'`), and the unit file now pins it
to `127.0.0.1` in so many words rather than leaning on the default
(`deploy/systemd/viporder-web.service:53`; rationale at `:40-47`). The app binds
loopback only, so nginx is the only peer that can set those headers — and it sets
them from `$proxy_add_x_forwarded_for` and `$scheme`
(`deploy/nginx/proxy_params_viporder:7-8`).

Measured against a real uvicorn started with the unit file's exact flags **and**
the app-level `TRUST_PROXY_HEADERS=no`:

```
=== A) HSTS with TRUST_PROXY_HEADERS=no, but uvicorn --proxy-headers ===
  plain (no XFP):          0 HSTS header(s)
  X-Forwarded-Proto:https: 1 HSTS header(s)

=== B) rate limiter keying: does it use the forwarded client IP? ===
  1st, XFF 203.0.113.9  -> 201
  2nd, XFF 203.0.113.9  -> 429   (same client; keyed on the forwarded IP)
  3rd, XFF 198.51.100.4 -> 409   (different client; past the limiter, then DUPLICATE_PHONE)
```

The third request's `409` is the duplicate-phone rule, which proves it got *past*
the limiter — a rate-limited request would have been `429`. So HSTS *is* emitted
over the proxy even with `TRUST_PROXY_HEADERS=no`, and the limiter *does* key on
the real client IP. The app-level flag is a second, independent gate that only
matters if the app is exposed without uvicorn's rewriting in front of it.
`docs/SECURITY.md` §5.3 records the corrected reasoning, and §12 records the
residual risk that remains.

### 1.4 What is not in this tier

There is no cache tier, no queue, no scheduler and no background worker. The only
processes are nginx and uvicorn. `docs/DEPLOYMENT.md` covers the operational
procedure; `docs/GO-LIVE-CHECKLIST.md` covers pre-live checks.

### 1.5 Dependency direction

```
index.html
  └── static/js/app.js        page chrome + mobile nav (no API call)
  └── static/js/register.js   the registration form controller -> POST /api/v1/registrations
  └── static/js/analytics.js  attribution + event layer (dormant by default)
        |
        v   HTTP/JSON
routers/          registrations.py, admin.py, health.py
        |
        v
services/         registration.py          <- all orchestration lives here
        |
        +--> repositories/  base.py (Protocol) -> sqlalchemy_repo.py
        |                                              |
        |                                              v
        |                                          models.py -> db.py
        |
        +--> providers/     base.py (Protocol) -> mock.py | khaibao9610.py
                                       ^
                                       |
                                   factory.py
```

Dependencies point one way: routers import services; services import the two
protocols plus models; repositories and providers import models/base
respectively. Neither `models.py` nor `repositories/base.py` imports a provider,
and `providers/` imports nothing from `repositories/` or `routers/`. The router
layer never touches a session or a provider directly — it receives a
`RegistrationService` from a FastAPI dependency (`backend/app/dependencies.py:33-44`)
and calls one method (`backend/app/routers/registrations.py:31-34`).

Script load order is `defer`, and `app.js` and `register.js` do not depend on each
other: `analytics.js`, then `register.js`, then `app.js`
(`index.html:92-94`). `register.js` talks to `analytics.js` only through the
optional `window.VIPOrderAnalytics` global, with a guarded call and a local
fallback (`static/js/register.js:84-98`, `:369-375`), so a failed analytics load
cannot break registration. `app.js` no longer touches the form at all: its only
`preventDefault` is in the mobile-nav toggle (`static/js/app.js:140`), and its
stated responsibilities are the footer year, mobile navigation and contact /
service-view tracking (`static/js/app.js:1-14`).

---

## 2. Request path

```
  Browser  ──POST /api/v1/registrations──►  nginx (TLS, proxy)
                                              │  X-Forwarded-For / -Proto
                                              ▼
                                         uvicorn (ASGI, 1 worker)
                                              │
  ┌───────────────────────────────────────────┴──────────────────────────────┐
  │ ServerErrorMiddleware                     (Starlette, outermost)         │
  │ ┌──────────────────────────────────────────────────────────────────────┐ │
  │ │ CORSMiddleware            only when CORS_ALLOW_ORIGINS is non-empty  │ │
  │ │ ┌──────────────────────────────────────────────────────────────────┐ │ │
  │ │ │ SecurityHeadersMiddleware   stamps 6 headers on the way out      │ │ │
  │ │ │ ┌──────────────────────────────────────────────────────────────┐ │ │ │
  │ │ │ │ RequestContextMiddleware   X-Request-Id in, echoed out       │ │ │ │
  │ │ │ │ ┌──────────────────────────────────────────────────────────┐ │ │ │ │
  │ │ │ │ │ RateLimitMiddleware   POST + exact path only -> 429      │ │ │ │ │
  │ │ │ │ │ ┌──────────────────────────────────────────────────────┐ │ │ │ │ │
  │ │ │ │ │ │ RequestSizeLimitMiddleware   > max_bytes -> 413       │ │ │ │ │ │
  │ │ │ │ │ │ ┌──────────────────────────────────────────────────┐ │ │ │ │ │ │
  │ │ │ │ │ │ │ ExceptionMiddleware  -> error handlers            │ │ │ │ │ │ │
  │ │ │ │ │ │ │ ┌──────────────────────────────────────────────┐ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │ APIRouter  /api/v1/registrations             │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │   pydantic validates RegistrationCreate      │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │   RegistrationService.register()             │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │     repository.create()   ← COMMIT #1        │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │     provider.register()   ← network call     │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ │     repository.update_status() ← COMMIT #2   │ │ │ │ │ │ │ │
  │ │ │ │ │ │ │ └──────────────────────────────────────────────┘ │ │ │ │ │ │ │
  │ │ │ │ │ │ └──────────────────────────────────────────────────┘ │ │ │ │ │ │
  │ │ │ │ │ └──────────────────────────────────────────────────────┘ │ │ │ │ │
  │ │ │ │ └──────────────────────────────────────────────────────────┘ │ │ │ │
  │ │ │ └──────────────────────────────────────────────────────────────┘ │ │ │
  │ │ └──────────────────────────────────────────────────────────────────┘ │ │
  │ └──────────────────────────────────────────────────────────────────────┘ │
  └──────────────────────────────────────────────────────────────────────────┘
```

nginx's own header set applies to the static responses it serves directly and to
the proxied ones; it is a separate layer from the application's (§5 of
`docs/SECURITY.md`).

The two commits are the load-bearing part: the lead row is durable **before** the
provider is contacted (§4 of `docs/REGISTRATION-FLOW.md`).

---

## 3. Middleware ordering — measured, not assumed

### 3.1 The real order

Starlette's `add_middleware` **inserts at the front** of `user_middleware`, so the
last call is the outermost wrapper. The source says so
(`backend/app/main.py:79-83`):

> `# add_middleware inserts at the front, so the LAST call is the OUTERMOST.`
> `# Order below (inner -> outer): size limit, rate limit, request context,`
> `# security headers, CORS.`

The calls in source order are `RequestSizeLimitMiddleware`
(`backend/app/main.py:84`), `RateLimitMiddleware` (`:85-91`),
`RequestContextMiddleware` (`:92`), `SecurityHeadersMiddleware` (`:93-96`), and
`CORSMiddleware` **only if `settings.cors_origins` is non-empty**
(`backend/app/main.py:98-110`).

Read out of a built app, outermost first:

```
=== middleware stack, OUTERMOST first (no CORS configured) ===
   SecurityHeadersMiddleware
   RequestContextMiddleware
   RateLimitMiddleware
   RequestSizeLimitMiddleware

=== with CORS_ALLOW_ORIGINS set ===
   CORSMiddleware
   SecurityHeadersMiddleware
   RequestContextMiddleware
   RateLimitMiddleware
   RequestSizeLimitMiddleware
```

So the effective request order is:

1. `ServerErrorMiddleware` (Starlette)
2. `CORSMiddleware` — *only when `CORS_ALLOW_ORIGINS` is set*
3. `SecurityHeadersMiddleware`
4. `RequestContextMiddleware`
5. `RateLimitMiddleware`
6. `RequestSizeLimitMiddleware`
7. `ExceptionMiddleware` → routers

### 3.2 Why the order matters

Three consequences follow directly from rate limiting being **outside** body-size
limiting:

**(a) A 413 still consumes a rate-limit attempt.** The limiter increments its
counter before the inner middleware ever inspects `Content-Length`. Measured
against a real app with `RATE_LIMIT_ATTEMPTS=1` and `MAX_REQUEST_BYTES=2048`,
posting two oversized bodies:

```
1st oversized -> 413 PAYLOAD_TOO_LARGE
2nd oversized -> 429 RATE_LIMITED
```

The second request is answered `429`, not `413`. An oversized body is therefore
counted as an attempt. That is a deliberate-looking property, not an accident of
configuration, but it is worth stating because it means a client that
accidentally sends a large body burns its budget.

**(b) The rate limiter answers without reading the body.** `RateLimitMiddleware.__call__`
returns the `429` before delegating inward (`backend/app/middleware.py:186-199`),
so no body is buffered for a rejected request. `RequestSizeLimitMiddleware` is
written as a raw ASGI wrapper specifically so it can reject *before* buffering
(`backend/app/middleware.py:1-8`).

**(c) The security headers decorate the 413 and 429.** `SecurityHeadersMiddleware`
wraps everything inside it and uses `MutableHeaders.setdefault` on
`http.response.start` (`backend/app/middleware.py:271-276`), so responses
produced by the rate limiter or the size limiter still receive the header set.
This is asserted for the 413 and 429 paths in
`backend/tests/test_security.py:43-50`.

If the two were swapped — rate limiting innermost — a 413 would not count as an
attempt and an attacker could probe body limits without limit. The current order
is the stricter one.

`RequestContextMiddleware` sits **outside** the rate limiter so that a `429` still
carries a correlation id (`backend/app/middleware.py:216-224`).

---

## 4. Module responsibilities

| Module | Responsibility | Anchor |
|---|---|---|
| `config.py` | Env-backed settings; server-side constants; no secret ever gets a default | `config.py:1-11`, `:48-149` |
| `db.py` | Engine + session factory; SQLite pragmas; health probe | `db.py:35-54`, `:57-104` |
| `models.py` | The `Lead` ORM model, its columns, indexes and constraints | `models.py:68-211` |
| `phone.py` | Vietnamese phone normalisation and display formatting | `phone.py:29-67`, `:75-80` |
| `schemas.py` | Pydantic request models and validation rules | `schemas.py:33-70`, `:73-123` |
| `errors.py` | The single `{"error": {...}}` envelope and its handlers | `errors.py:37-60`, `:115-158` |
| `logging_filters.py` | Password redaction at record and render level | `logging_filters.py:146-274` |
| `middleware.py` | Size limit, rate limit, request id, security headers | `middleware.py:67-298` |
| `dependencies.py` | Per-request repository/service construction and teardown | `dependencies.py:23-44` |
| `repositories/base.py` | `LeadRepository` protocol — the storage contract | `repositories/base.py:23-73` |
| `repositories/sqlalchemy_repo.py` | The SQLAlchemy implementation; commits per call | `repositories/sqlalchemy_repo.py:19-149` |
| `services/registration.py` | The whole registration sequence, incl. the fingerprint | `services/registration.py:80-568` |
| `providers/base.py` | `RegistrationProvider` protocol + request/result dataclasses | `providers/base.py:28-94` |
| `providers/factory.py` | The one place that chooses an adapter | `providers/factory.py:19-36` |
| `providers/mock.py` | Deterministic in-process provider (the default) | `providers/mock.py:54-135` |
| `providers/khaibao9610.py` | Live HTTP adapter, candidate contract, double-gated | `providers/khaibao9610.py:76-267` |
| `routers/health.py` | Liveness: database reachable, provider mode | `routers/health.py:22-42` |
| `routers/registrations.py` | Public POST/GET | `routers/registrations.py:19-51` |
| `routers/admin.py` | Operator retry, 404 when unconfigured | `routers/admin.py:49-69` |
| `static/js/register.js` | Form controller: validation, `Idempotency-Key`, outcomes | `static/js/register.js:1-673` |
| `static/js/analytics.js` | Attribution capture + event layer, dormant by default | `static/js/analytics.js:72-76` |
| `static/js/app.js` | Footer year, mobile nav, contact/service-view tracking | `static/js/app.js:1-14` |
| `tools/check_nginx_config.py` | Enforces the `add_header`/include rule | `tools/check_nginx_config.py:90-160` |

### 4.1 Error envelope

Every client-visible error has one shape:
`{"error": {"code", "message", "fields"?}}` (`backend/app/errors.py:1-12`).
`code` is stable and machine-readable; the codes are module constants
(`backend/app/errors.py:24-36`), including `IDEMPOTENCY_KEY_REUSED` (`:27-28`).
No exception detail reaches the client — `_internal_error_handler` logs the
traceback server-side and returns a flat 500 (`backend/app/main.py:132-148`),
which `backend/tests/test_security.py:346-360` asserts by planting an exception
containing a fake path and a fake secret and checking neither appears in the
response.

The validation handler deliberately drops the echoed input. `validation_fields`
flattens pydantic errors but drops `input`/`ctx`, because pydantic echoes the
offending value and for a password field that would put plaintext into the HTTP
response *and* the access log (`backend/app/errors.py:82-94`); `sanitised_errors`
does the same for the FastAPI-shaped `detail` list (`backend/app/errors.py:97-108`).

---

## 5. The repository interface

`LeadRepository` is a `runtime_checkable` `Protocol`
(`backend/app/repositories/base.py:23-24`) with exactly six methods:

| Method | Purpose | Anchor |
|---|---|---|
| `create(lead) -> Lead` | Persist a new lead, return it with defaults applied | `repositories/base.py:27-29` |
| `get(lead_id) -> Lead \| None` | Primary-key lookup | `:31-33` |
| `get_by_idempotency_key(key) -> Lead \| None` | Replay lookup | `:35-37` |
| `update_status(lead_id, *, status, ...) -> Lead \| None` | The **only** mutation | `:39-59` |
| `find_registered_by_phone(phone) -> Lead \| None` | Duplicate rule | `:61-63` |
| `list_pending(limit=100) -> list[Lead]` | The retry work queue | `:65-73` |

The docstring names what is **absent** on purpose: "there is no method that takes
or returns a password, and no generic 'update anything' escape hatch"
(`backend/app/repositories/base.py:5-8`). `update_status` is narrow by design —
it takes a target status and a fixed set of optional columns, so no caller can
write an arbitrary field. Note that `request_fingerprint` and the consent columns
are written once at creation (`services/registration.py:235-237`) and cannot be
changed through `update_status`, which is the right shape for evidence.

Why this matters architecturally: the service depends on the protocol, not on
SQLAlchemy, so the storage engine is replaceable and tests can substitute a fake
without touching route code. `Database` itself is backend-agnostic — the same
models and the same Alembic migrations serve SQLite and
`postgresql+psycopg://...` (`backend/app/db.py:1-7`).

Two implementation details are worth recording:

- **Each repository call commits.** `create()` does `session.add` then
  `session.commit()` (`backend/app/repositories/sqlalchemy_repo.py:35-45`), and
  `update_status()` commits too (`:92-101`). The class docstring states the reason:
  "a lead must be on disk *before* the provider call, so a crash mid-request still
  leaves the customer recoverable" (`:21-25`).
- **Sessions are per request and always closed.** `get_service` yields a service
  and closes the repository in a `finally` (`backend/app/dependencies.py:33-44`).

---

## 6. Why the provider is swappable

Two mechanisms, one structural and one configurational.

**Structural.** `RegistrationProvider` is a `Protocol` with exactly two members —
a `name` attribute and `register(request) -> RegistrationResult`
(`backend/app/providers/base.py:67-73`). Nothing in `services/` or `repositories/`
imports `mock.py` or `khaibao9610.py`; the service holds whatever object satisfies
the protocol (`backend/app/services/registration.py:81-90`). That is what lets
`backend/tests/test_lead_retention.py` inject a provider that deliberately raises
and `test_security.py:205-252` inject a provider that fails once then succeeds,
without patching imports.

**Configurational.** `build_provider()` is the single decision point
(`backend/app/providers/factory.py:19-36`): `mock` returns
`MockRegistrationProvider`; `http` returns `ViporderFrontendProvider.from_settings`;
anything else raises. Swapping providers is therefore a config change, not a code
change — stated as the intent in the module docstring (`:1-7`).

The boundary is enforced by data, not convention. `RegistrationResult` validates
in `__post_init__` that `status` is a `ProviderStatus` member
(`backend/app/providers/base.py:62-64`), so an adapter cannot invent a new
outcome — and the caller re-checks the type at runtime, because a duck-typed fake
can still return the wrong object (`services/registration.py:264-267`).
`RegistrationRequest.password` is `field(repr=False)`
(`backend/app/providers/base.py:43`) so an accidental f-string cannot print it,
and `redact_request()` exists to produce a log-safe view
(`providers/base.py:88-94`).

`RegistrationResult.error_code` is an additive field added so a connection
refusal and a read timeout — which both arrive with `http_status = None` — stay
distinguishable in the stored lead (`providers/base.py:56-60`). The adapters set
it (`providers/khaibao9610.py:206`, `:220`, `:255-257`), and the service falls
back to the old heuristic only when an adapter does not
(`services/registration.py:550-562`).

### 6.1 The two adapters

**`MockRegistrationProvider`** is the default and never touches the network
(`backend/app/providers/mock.py:55`). Behaviour is selected two ways: a
process-wide `MOCK_PROVIDER_BEHAVIOUR`, or a phone-suffix convention that lets one
running process answer differently per request (`mock.py:6-29`, `:43-49`,
`:72-74`). The suffix wins over the environment variable (`mock.py:26-28`). The
`error` behaviour raises a `RuntimeError` on purpose — the comment states it is
there to prove that a provider which blows up cannot destroy the lead
(`mock.py:103-106`).

**`ViporderFrontendProvider`** is the live HTTP adapter and is labelled in its own
module header as a **candidate, unverified** contract
(`backend/app/providers/khaibao9610.py:1-9`). Reaching it requires both switches;
the guard is in `__init__` (`khaibao9610.py:91-115`). Its request-body shape
(`name`, `phone`, `email`, `password`, `confirmPassword`, `acceptTerms`) is
documented at `khaibao9610.py:20-27` and asserted on the wire in
`backend/tests/test_khaibao9610_provider.py:246-255`.

Outcome mapping is a pure function of the HTTP response
(`khaibao9610.py:221-263`): 2xx → `SUCCESS`; 409/422 whose body matches the
duplicate phrasings → `DUPLICATE`; 429 or ≥500 → `UNAVAILABLE` with
`retryable=True` and an `error_code` of `PROVIDER_RATE_LIMITED` (429) or
`PROVIDER_UNAVAILABLE`; any other non-2xx → `INVALID`. Duplicate detection is
accent-stripped and lowercased so both `"Số điện thoại đã được đăng ký"` and
`"da dang ky"` match (`khaibao9610.py:53-73`).

---

## 7. Persistence design

### 7.1 The `leads` table

Columns are declared in `backend/app/models.py:68-169`. Grouped by purpose:

| Group | Column | Type / null | Anchor |
|---|---|---|---|
| Identity | `lead_id` | `String(36)` PK | `models.py:71` |
| | `lead_type` | enum-as-string, NOT NULL, default `REGISTER_LEAD` | `:73-78` |
| | `registration_status` | enum-as-string, NOT NULL, default `PENDING` | `:79-89` |
| Customer | `full_name` | `String(120)` NOT NULL | `:92` |
| | `phone` | `String(20)` NOT NULL, canonical `+84...` | `:93` |
| | `phone_display` | `String(32)` NOT NULL, e.g. `0912 345 678` | `:94` |
| | `email` | `String(254)` NULL | `:95` |
| | `province` | `String(120)` NULL | `:96` |
| | `service_interest` | `String(32)` NULL | `:97` |
| Attribution | `source`, `medium`, `campaign`, `content`, `term`, `landing_page`, `referrer` | each `String(300)` NULL | `:99-106` |
| Provider outcome | `external_customer_id` | `String(64)` NULL | `:109` |
| | `external_customer_code` | `String(64)` NULL | `:110` |
| Dedup / tracking | `idempotency_key` | `String(128)` NULL | `:113` |
| | `tracking_token` | `String(64)` NOT NULL | `:114` |
| | `request_fingerprint` | `String(64)` NULL — SHA-256 hex | `:124` |
| Consent evidence | `consent_given_at` | `DateTime(timezone=True)` NULL | `:134-136` |
| | `consent_version` | `String(32)` NULL | `:137` |
| Stored reply | `response_status` | `Integer` NULL — the exact HTTP status the terminal outcome produced | `:153` |
| | `response_body` | `JSON` NULL — the exact body returned, so a replay is verbatim | `:154` |
| Retry bookkeeping | `attempt_count` | `Integer` NOT NULL, default/server_default `0` | `:157-159` |
| | `last_error_code` | `String(64)` NULL | `:160` |
| | `last_error_message` | `String(500)` NULL | `:161` |
| Audit | `created_at` | `DateTime(timezone=True)` NOT NULL, default `utcnow` | `:164-166` |
| | `updated_at` | as above, `onupdate=utcnow` | `:167-169` |

Four deliberate design points:

1. **There is no password column.** Stated in the module docstring: "there is
   deliberately no column for it, no shadow column, and no 'recent payload' blob"
   (`models.py:7-9`), and asserted structurally by
   `backend/tests/test_password_leakage.py:152-158`. The `request_fingerprint`
   deliberately excludes the password for the same reason — see §7.5.
2. **Both a canonical and a display phone are stored.** Canonical is what
   duplicate detection compares; display is what a human reads
   (`phone.py:1-7`, `:75-80`).
3. **Timestamps are timezone-aware.** `utcnow()` returns `datetime.now(UTC)`
   (`models.py:32-34`), and `as_utc()` re-labels values read back from SQLite,
   which does not persist offsets (`models.py:202-211`).
4. **Consent is recorded as evidence, not only enforced.** `consent_given_at` and
   `consent_version` are written on every lead the service creates
   (`services/registration.py:236-237`), with the version taken from the
   server-side constant `CONSENT_VERSION = "2026-02-v1"` (`config.py:41-45`). The
   distinction is stated in the model comment: "'Consent is required by the
   schema' is *enforcement*; these columns are the *evidence*, which is a
   different thing" (`models.py:127-130`). Asserted in
   `backend/tests/test_consent.py:22-50`, including that evidence is recorded even
   when the provider is down.

`__repr__` is overridden to keep PII out of logs — it prints `lead_id`, status and
`phone_display` only (`models.py:195-199`).

### 7.2 Constraints

Three `CheckConstraint`s (`models.py:172-180`):

| Name | Predicate |
|---|---|
| `ck_leads_lead_type` | `lead_type IN ('REGISTER_LEAD', 'QUOTE_LEAD')` |
| `ck_leads_registration_status` | `registration_status IN ('PENDING', 'REGISTERED', 'FAILED')` |
| `ck_leads_attempt_count` | `attempt_count >= 0` |

The enums are stored as strings with `native_enum=False` (`models.py:74`,
`:81-85`), so the same DDL works on SQLite and PostgreSQL; the check constraints
are the enforcement that survives that choice.

### 7.3 Indexes, including the partial unique index

Six indexes (`models.py:181-192`):

| Name | Columns | Unique | Anchor |
|---|---|---|---|
| `ix_leads_phone` | `phone` | no | `:181` |
| `ix_leads_registration_status` | `registration_status` | no | `:182` |
| `ix_leads_created_at` | `created_at` | no | `:183` |
| `uq_leads_idempotency_key` | `idempotency_key` | **yes** | `:184` |
| `uq_leads_tracking_token` | `tracking_token` | **yes** | `:185` |
| `uq_leads_registered_phone` | `phone` | **yes, partial** | `:186-192` |

The predicate is a module-level constant (`models.py:65`):

```python
_REGISTERED_PHONE_PREDICATE = "lead_type = 'REGISTER_LEAD' AND registration_status = 'REGISTERED'"
```

and is passed as **both** `sqlite_where` and `postgresql_where`
(`models.py:190-191`):

```python
Index(
    "uq_leads_registered_phone",
    "phone",
    unique=True,
    sqlite_where=text(_REGISTERED_PHONE_PREDICATE),
    postgresql_where=text(_REGISTERED_PHONE_PREDICATE),
),
```

Rendered DDL, compiled from the ORM object for each dialect (no database
involved — this is SQLAlchemy's own DDL compiler):

```sql
-- sqlite
CREATE UNIQUE INDEX uq_leads_registered_phone ON leads (phone)
  WHERE lead_type = 'REGISTER_LEAD' AND registration_status = 'REGISTERED'

-- postgresql
CREATE UNIQUE INDEX uq_leads_registered_phone ON leads (phone)
  WHERE lead_type = 'REGISTER_LEAD' AND registration_status = 'REGISTERED'
```

The Alembic migration declares the identical predicate as its own constant and
passes it the same way (`backend/alembic/versions/0001_create_leads.py:30`,
`:115-122`), so the migrated schema and the ORM metadata stay in step. Parity is
asserted rather than trusted
(`backend/tests/test_alembic.py:21-56`, `:59`).

**Why partial.** The business rule is "at most one `REGISTERED` registration lead
per phone number", and `PENDING`/`FAILED` rows are deliberately outside the index
because "a customer whose first attempt failed must be able to try again"
(`models.py:59-64`). The rule is enforced by the database rather than by
application code, so two concurrent registrations for the same phone cannot both
win. Both halves are tested: the index rejects a second `REGISTERED` row
(`backend/tests/test_duplicate.py:129-150`) and permits many `FAILED` rows
(`:153-175`).

`update_status()` handles the losing side of that race: an `IntegrityError` on
commit is rolled back and re-raised so the caller can answer `409` rather than
inventing a duplicate customer (`backend/app/repositories/sqlalchemy_repo.py:92-101`).

The two unique indexes are nullable, which is what makes `idempotency_key`
optional: SQLite and PostgreSQL both allow multiple `NULL`s in a unique index, and
`_normalise_idempotency_key` returns `None` for a blank header
(`backend/app/services/registration.py:495-507`).

### 7.4 Migrations

Three revisions. `0001_create_leads` creates the table and all six indexes
(`backend/alembic/versions/0001_create_leads.py:44-122`). `0002_consent_and_fingerprint`
adds three nullable columns (`0002_...py:37-41`), inspecting the live
schema first so it is a no-op when `AUTO_CREATE_SCHEMA` already produced them
(`:44-67`). `0003_stored_response` adds the two stored-reply columns
(`0003_stored_response.py:31-38`), so a replay of an `Idempotency-Key` returns the
reply the original attempt produced instead of a reconstructed one — see
`docs/REGISTRATION-FLOW.md` §8.3. All five
columns are nullable so they can be added to an existing
SQLite database without inventing a server default for rows that predate them;
"nullable" does not become "usually empty" because every lead the service creates
sets the consent and fingerprint columns, and a test asserts it (`0002_...py:17-20`,
`backend/tests/test_consent.py:22-38`). The stored-reply pair stays `NULL` while a
lead is `PENDING`, because `PENDING` is not terminal.

### 7.5 The request fingerprint

`request_fingerprint` binds an `Idempotency-Key` to one specific body. It is a
SHA-256 over a canonical JSON object of the identity, contact, service-interest,
consent and attribution fields (`services/registration.py:516-547`):

```python
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

**The password is deliberately excluded** (`services/registration.py:519-522`):
"a hash of a password is still password-derived material, and storing it would
create an offline-cracking target". Asserted directly by
`backend/tests/test_idempotency.py:233-241`, which hashes two different passwords
under the same body and asserts the digests are equal and that the password does
not appear in the digest.

Why the binding is needed is stated in the model comment
(`backend/app/models.py:116-123`): "the front end keeps one key for the whole form
session and only clears it after a completed registration, so 'same key, edited
phone number' is a reachable path. Without this, the replay would hand the second
customer the first customer's lead and customer code."

The comparison is fail-closed (`services/registration.py:187-211`):

```python
        if existing.request_fingerprint != fingerprint:
            ...
            raise ApiError(
                409,
                IDEMPOTENCY_KEY_REUSED,
                "This Idempotency-Key was already used for a different "
                "registration. Start a new registration.",
            )
```

A row whose stored fingerprint is `NULL` (one that predates migration 0002) is
refused rather than replayed, because `None != fingerprint` — "a lead with no
stored fingerprint cannot be proven to match, so it is refused"
(`services/registration.py:196-198`). Measured:

```
=== same key, DIFFERENT body -> 409 IDEMPOTENCY_KEY_REUSED? ===
body A -> 201 REGISTERED
body B (same key, new phone) -> 409 {"error": {"code": "IDEMPOTENCY_KEY_REUSED", ...}}
         rows: 1

=== null fingerprint (legacy row) -> fail closed? ===
replay w/ null stored fingerprint -> 409 {"error": {"code": "IDEMPOTENCY_KEY_REUSED", ...}}
```

### 7.6 Engine configuration

`make_engine()` (`backend/app/db.py:35-54`) creates the parent directory for a
file-backed SQLite URL (`:24-32`), sets `check_same_thread=False` for SQLite
because TestClient runs the app across threads (`:38-40`), and registers a
connect hook that enables `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL`
(`:43-52`). PostgreSQL gets neither branch.

`is_healthy()` runs `SELECT 1` and swallows any exception into `False`
(`db.py:95-101`) — it is the backing probe for `GET /api/v1/health`
(`routers/health.py:27`), which returns `503` when it fails (`:42`).

---

## 8. What is static and what is dynamic

| URL | Served by | Nature |
|---|---|---|
| `/` | nginx, from `/srv/viporder/site` | `index.html`, no templating |
| `/static/css/style.css` | nginx, 30-day immutable cache | plain CSS |
| `/static/js/app.js` | nginx | page chrome; no API call |
| `/static/js/register.js` | nginx | the form controller; **calls** `POST /api/v1/registrations` |
| `/static/js/analytics.js` | nginx | attribution + events; dormant by default |
| `/robots.txt`, `/sitemap.xml`, `/favicon.ico` | nginx, 1-day cache | static |
| `/api/v1/health` | app | JSON |
| `/api/v1/registrations` | app | JSON |
| `/api/docs` | app | Swagger UI, **404 in production** (`main.py:65`) |

The three scripts are loaded `defer` in the order analytics → register → app
(`index.html:92-94`). Only `register.js` performs the registration request
(`static/js/register.js:612`); `analytics.js` merely mentions the endpoint in a
comment describing the payload shape (`static/js/analytics.js:346`).

Static responses are cached by nginx with an explicit `Cache-Control` and each
such `location` re-includes the security-headers snippet — the reason is the
`add_header` inheritance trap documented in `deploy/nginx/README.md:36-58` and
measured in `docs/SECURITY.md` §5.

---

## 9. What is not verified

This section is the honest limit of the document above.

1. **PostgreSQL was never executed.** Every test in the suite runs against a
   fresh SQLite file under pytest's `tmp_path` (`backend/tests/conftest.py:5-9`,
   `:111`). The PostgreSQL side of the partial index is verified **only** as
   compiled DDL text (§7.3) and as a dialect branch in the model
   (`models.py:191`). No PostgreSQL server was started, no migration was applied
   to one, and no query was run against one.
2. **No live provider call was ever made.** `KHAIBAO9610_MODE` defaults to `mock`
   (`config.py:82`) and the live adapter refuses to construct without both
   switches (`khaibao9610.py:91-102`). The adapter's behaviour is exercised only
   against `httpx.MockTransport` (`backend/tests/test_khaibao9610_provider.py:48-52`).
   The upstream contract itself is **unverified** — the module says so in its
   header (`khaibao9610.py:1-9`).
3. **The rate limiter is in-process only** (`backend/app/middleware.py:156-157`).
   Both deployment paths run a single worker, so the limit is currently not
   multiplied — but that is a configuration choice, not a property of the
   limiter. Raising `--workers` without moving the counters to a shared store
   would multiply the effective limit. The docstring states the limitation and
   sketches the Redis path (`middleware.py:130-138`).
4. **nginx was never run against the real host.** The configuration parses and
   the header behaviour was reproduced locally with nginx 1.31.6 (the same
   version `deploy/nginx/README.md:53` cites) — see `docs/SECURITY.md` §5.4 — but
   TLS, OCSP stapling, HTTP/2 and the `/api/` proxy path were not exercised
   against a live backend. `deploy/nginx/README.md:92-94` says the same, and
   points at `deploy/post-deploy-check.sh` for the live checks.
5. **The single-worker deployment was not exercised end to end.** Reading
   `deploy/systemd/viporder-web.service:51` together with the in-process data
   structure is what makes §1.3(a) hold; it was not measured by running the unit
   and counting rejections. `deploy/Dockerfile:62-68` was not built or run, and
   the compose `expose`/upstream arrangement was not verified.
6. **The middleware order was measured with `TestClient`, and separately with one
   real uvicorn process.** The stack read out in §3.1 comes from
   `app.user_middleware` on a `create_app()` instance, and the 413/429 interaction
   from that same in-process client. The proxy-header behaviour in §1.3(b) came
   from a single real uvicorn started with the unit file's flags. The 413/429
   interaction was **not** re-measured through that real uvicorn.
7. **The 328-test suite passes at this revision.** `python -m pytest -q` reported
   `328 passed, 1 warning` on each of the runs made while preparing this document
   (wall time varied: 4.79 s, 4.86 s), run
   with a venv carrying the pinned versions from
   `backend/requirements-dev.txt`. A passing suite is not a status claim and is
   recorded elsewhere. This document describes mechanism, not readiness.
