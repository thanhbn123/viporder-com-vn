# VIPORDER.COM.VN — backend

Registration API for **viporder.com.vn**. It validates a new-customer
registration, **writes the lead to the database first**, and only then asks the
registration provider to create the customer account.

> **A lead is never lost because the provider was unavailable.**
>
> The lead row is committed with status `PENDING` before the provider is
> contacted. A timeout, a 500, a connection reset or an adapter that raises
> leaves that row in place with a truthful `last_error_code` and returns `202`,
> never a 5xx. `GET /api/v1/registrations/{lead_id}` (with the tracking token)
> and `POST /api/v1/admin/registrations/{lead_id}/retry` exist so the lead can
> always be recovered.

---

## Provider status: **MOCK**

`KHAIBAO9610_MODE` defaults to `mock`. The real production registration
contract **has not been supplied**, so:

* no guessed endpoint is a live default;
* the live adapter (`app/providers/khaibao9610.py`) is labelled a **candidate,
  unverified** contract and refuses to be constructed unless **both**
  `KHAIBAO9610_MODE=http` **and** `KHAIBAO9610_ENABLE_REAL_CALLS=yes` are set.

---

## Run

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Development: create the schema and serve.
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
# -> http://127.0.0.1:8000/api/v1/health
```

`AUTO_CREATE_SCHEMA` (default `yes`) lets the application create tables itself
on startup, so `uvicorn app.main:app` works with no setup step. The Alembic
migration is written to be a no-op when the tables already exist, so the two
paths never fight. In a real deployment set `AUTO_CREATE_SCHEMA=no` and let
Alembic own the schema.

The default database is `sqlite:///./var/viporder.db`. `var/` is git-ignored —
a lead database must never be committable, and this repository is public.

## Test

```bash
cd backend
.venv/bin/python -m pytest -q
```

The suite is fully offline: no test opens a socket. The live provider is
exercised only through `httpx.MockTransport`, and every database is a fresh
SQLite file under pytest's `tmp_path`.

```bash
cd backend
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # formatting
.venv/bin/bandit -r app         # security static analysis
.venv/bin/python -m pip_audit -r requirements.txt
```

---

## API

### `GET /api/v1/health`

```json
{
  "status": "ok",
  "service": "viporder-web",
  "version": "0.2.0",
  "checks": { "database": "ok", "provider": { "mode": "mock", "status": "ok" } }
}
```

`503` with `"status": "error"` when the database cannot be reached.

### `POST /api/v1/registrations`

Headers: `Idempotency-Key` (optional), `X-Request-Id` (optional, echoed back).

```json
{
  "full_name": "Nguyễn Văn A",
  "phone": "0912345678",
  "password": "secret-at-least-8",
  "email": "a@example.com",
  "province": "Bắc Ninh",
  "service_interest": "transport",
  "consent": true,
  "attribution": {
    "utm_source": "facebook", "utm_medium": "cpc", "utm_campaign": "g01",
    "utm_content": "ad1", "utm_term": "nhaphang",
    "landing_page": "https://viporder.com.vn/?utm_source=facebook",
    "referrer": "https://facebook.com/"
  }
}
```

| Field | Rule |
|---|---|
| `full_name` | required, 2..120 characters after trim |
| `phone` | required, normalised server-side to `+84XXXXXXXXX` |
| `password` | required, min 8 characters. **Forwarded to the provider, never stored.** |
| `email` | optional, validated if present |
| `province` | optional, max 120 |
| `service_interest` | optional; exactly `transport`, `official_import`, `customs`, `order` |
| `consent` | required, must be `true` — no consent, no account |
| `attribution` | optional; every sub-field optional, each max 300 characters; `landing_page` must be an absolute http(s) URL |

Responses:

| Status | When | Body |
|---|---|---|
| `201` | provider confirmed the account | `lead_id`, `registration_status=REGISTERED`, `external_customer_id`, `external_customer_code`, `message`, `login_url` |
| `202` | provider unavailable, lead kept | as above but `registration_status=PENDING`, null external fields, plus `tracking_token` |
| `409` | a REGISTERED lead already exists for that phone | `{"error":{"code":"DUPLICATE_PHONE", ...}}` — **never the existing customer code** |
| `409` | the `Idempotency-Key` was already used for a *different* body | `{"error":{"code":"IDEMPOTENCY_KEY_REUSED", ...}}` — **never the earlier customer's data** |
| `422` | validation failure | `{"error":{"code":"VALIDATION_ERROR","message":...,"fields":{...}}}` |
| `400` | malformed JSON, bad `Idempotency-Key` | `{"error":{"code":"INVALID_REQUEST", ...}}` |
| `413` | body over `MAX_REQUEST_BYTES` | `{"error":{"code":"PAYLOAD_TOO_LARGE", ...}}` |
| `429` | rate limited | `{"error":{"code":"RATE_LIMITED", ...}}` + `Retry-After` |
| `500` | unexpected internal error | `{"error":{"code":"INTERNAL_ERROR", ...}}` — never a stack trace |

`login_url` is a **server-side constant** (`https://khachhang.viporder.com.vn`).
It is never built from request input, so there is no open redirect.

Duplicate rule: a phone that already has a `REGISTERED` lead is a duplicate.
A phone whose earlier lead is `PENDING`/`FAILED` may try again and gets a **new
row** — two different customers' data is never merged.

Idempotency: an `Idempotency-Key` names one *request*, and a request is the key
**plus its body**. On replay the stored SHA-256 fingerprint of the canonical body
is compared:

* same key, same body → the identical stored response, nothing re-sent;
* same key, **different** body → `409 IDEMPOTENCY_KEY_REUSED`, and nothing about
  the earlier registration is returned.

That second case is not hypothetical. The front end keeps one key per form
session and clears it only after a completed registration, so *submit → error →
correct the phone number → submit again* arrives as the same key with a different
body. Replaying blindly there would answer customer B with customer A's lead id
and customer code.

The password is deliberately **not** part of the fingerprint: a hash of a
password is still password-derived material, and storing one would create an
offline-cracking target for no benefit.

### Front-end integration contract

The public form is already shipped, and it is stricter than the brief in one
place and looser in another. These four behaviours are load-bearing and are
each covered by `tests/test_frontend_contract.py`:

1. **`"email": ""` is normalised to `None` before validation.** The public form
   has no email field, so the key arrives empty on every submission. Validating
   it with `EmailStr` would return `422` for *every* registration and look like
   a broken form on day one. An empty or whitespace-only string means "no
   email"; only a non-empty value is email-validated.
2. **`service_interest` may be absent entirely.** When the customer picks
   nothing the front end omits the key (because `""` is not a member of the
   enum). Absent → `None`, registration proceeds.
3. **`202` is not a conversion.** The front end fires `viporder_lead_success`
   only on `201`, and a separate `viporder_lead_pending` on `202`. The
   provider-unavailable path therefore stays `202` + `tracking_token` — an
   unconfirmed lead must never be counted as a conversion, so `202` is
   deliberately not softened to `201`.
4. **`Idempotency-Key` is a UUID, reused across retries of the same form
   session.** A repeat of the *same body* returns the identical stored response
   body (including the same `tracking_token` on the `202` path) and creates no
   second lead. A repeat with a *changed body* — which is what happens when a
   customer corrects a mistyped phone number and resubmits — is refused with
   `409 IDEMPOTENCY_KEY_REUSED` instead of handing the second customer the first
   customer's data.

### `GET /api/v1/registrations/{lead_id}`

Requires `X-Tracking-Token` to match the stored token, otherwise a flat `404`
(the route never confirms a lead id exists).

```json
{"lead_id":"...","registration_status":"REGISTERED","external_customer_code":"TT00123",
 "created_at":"2026-02-14T09:12:00Z","updated_at":"2026-02-14T09:12:00Z"}
```

Never returns the password, phone or email.

### `POST /api/v1/admin/registrations/{lead_id}/retry`

Requires `X-Admin-Token` equal to `ADMIN_API_TOKEN`. **If `ADMIN_API_TOKEN` is
unset the route returns `404`** — disabled means absent, not merely
unauthorised. A wrong or missing token also gets `404`, compared in constant
time.

Retries a `PENDING`/`FAILED` lead. The body may carry `{"password": "..."}`.

> **Known limitation, stated plainly.** The password is not stored anywhere, so
> the operator must supply it again to complete a retry. Without it the route
> returns `200` with `"retried": false` and an explanatory message, and the lead
> stays `PENDING` — recoverable, but not completed. The alternative (storing the
> password, or inventing one) would either break the hard constraint or create
> an account the customer cannot sign in to.

---

## Configuration

Every key is documented, with empty values, in [`../.env.example`](../.env.example).
An empty environment variable means "unset" and the default applies.

| Key | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | environment label |
| `SERVICE_NAME` | `viporder-web` | reported by health |
| `APP_VERSION` | `0.2.0` | reported by health |
| `DATABASE_URL` | `sqlite:///./var/viporder.db` | SQLite for dev; `postgresql+psycopg://...` for production |
| `AUTO_CREATE_SCHEMA` | `yes` | let the app create tables at startup |
| `KHAIBAO9610_MODE` | `mock` | `mock` or `http` |
| `KHAIBAO9610_BASE_URL` | `https://apiviporder.com/frontend/v1` | candidate, unverified |
| `KHAIBAO9610_TIMEOUT_SECONDS` | `10` | allowed 1..30 |
| `KHAIBAO9610_ENABLE_REAL_CALLS` | `no` | second key required for live traffic |
| `KHAIBAO9610_USER_AGENT` | browser UA | Cloudflare rejects non-browser clients (Error 1010) |
| `MOCK_PROVIDER_BEHAVIOUR` | `success` | `success`/`duplicate`/`invalid`/`unavailable`/`timeout`/`error` |
| `ADMIN_API_TOKEN` | *(empty)* | empty disables the admin route (404) |
| `ENABLE_API_DOCS` | *(empty)* | empty = on outside production, off in production |
| `MAX_REQUEST_BYTES` | `65536` | request size limit → 413 |
| `RATE_LIMIT_ENABLED` | `yes` | |
| `RATE_LIMIT_ATTEMPTS` | `10` | per IP, per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `600` | sliding window |
| `TRUST_PROXY_HEADERS` | `no` | `yes` makes `X-Forwarded-For`/`-Proto` authoritative |
| `CORS_ALLOW_ORIGINS` | *(empty)* | empty = CORS off (same-origin only) |

### Switching provider mode

```bash
# Default: everything runs in-process, nothing leaves the machine.
KHAIBAO9610_MODE=mock .venv/bin/uvicorn app.main:app

# Per-request behaviour, useful for manual QA. The phone suffix wins over
# MOCK_PROVIDER_BEHAVIOUR, so one running server can answer differently per call.
#   ...0000 -> duplicate   ...0001 -> invalid    ...0002 -> unavailable
#   ...0003 -> timeout     ...0004 -> error      anything else -> success
MOCK_PROVIDER_BEHAVIOUR=success .venv/bin/uvicorn app.main:app

# Live traffic — TWO keys, both required, or the adapter refuses to build.
KHAIBAO9610_MODE=http \
KHAIBAO9610_ENABLE_REAL_CALLS=yes \
.venv/bin/uvicorn app.main:app
```

Nothing else switches to live. In particular, setting only
`KHAIBAO9610_ENABLE_REAL_CALLS=yes` while the mode stays `mock` changes nothing,
and setting only the mode raises a configuration error at startup.

---

## Security

* **Passwords are never persisted, logged, or traced.** There is no password
  column, no shadow copy, no request-body log line. `RegistrationCreate.password`
  is a pydantic `SecretStr`, so even `repr()` and `model_dump()` cannot print it.
  Redaction happens in the **log-record factory**, at record creation, before any
  handler runs — so it holds for handlers this application does not own (a test
  capture handler, an APM agent, a `logging.basicConfig()` stream a library
  installed first) and no formatter can be bypassed. Message, `%`-arguments,
  `exc_info`/`exc_text` tracebacks and `stack_info` are all scrubbed; tracebacks
  are rendered and scrubbed into `exc_text` with `exc_info` cleared, because an
  exception raised inside the provider carries the password in its message.
  `tests/test_password_leakage.py` submits a registration and searches the raw
  SQLite **file bytes**, all captured **log records**, a **foreign handler
  registered before the app**, and the **response body**; each search has a
  positive control proving it is not passing on an empty haystack.
* API docs (`/docs`, `/openapi.json`) are **off when `APP_ENV=production`** and on
  elsewhere; `ENABLE_API_DOCS` overrides either way. `/redoc` is never served.
* Request size limit (64 KiB default) → `413`.
* Rate limit (10 attempts / 10 minutes / IP) → `429` with `Retry-After`.
  In-memory and per-process; **the path to Redis** is to swap the sliding-window
  store for a sorted set per key (`ZADD` / `ZREMRANGEBYSCORE` / `ZCARD`). The
  middleware boundary does not change.
* Security headers on every response: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, a default-deny `Content-Security-Policy`,
  `Permissions-Policy`, `Cross-Origin-Resource-Policy`, and
  `Strict-Transport-Security` **only** over HTTPS.
* `X-Forwarded-For` is trusted only when `TRUST_PROXY_HEADERS=yes`.
* No stack traces to clients; the traceback is logged server-side with the
  request's correlation id (`X-Request-Id`).
* CORS is off by default; when enabled, a `*` origin never gets credentials.

## Data protection

`consent: true` is required by the schema — that is *enforcement*. The **evidence**
is stored on every lead:

| Column | Meaning |
|---|---|
| `consent_given_at` | timezone-aware UTC instant the customer agreed |
| `consent_version` | which wording they agreed to (`CONSENT_VERSION` in `config.py`) |

Bump `CONSENT_VERSION` whenever the on-page wording changes: the version is what
answers a legal question about one specific customer. Both columns are nullable
in the database (so they can be added to an existing file without inventing a
server default for rows that predate them) and both are always written by the
service — `tests/test_consent.py` asserts that for every row, not just one.

---

## Layout

```
backend/
  app/
    config.py             environment-backed settings
    db.py                 engine/session factory (SQLite + PostgreSQL)
    models.py             SQLAlchemy 2.x Lead model and indexes
    phone.py              Vietnamese phone normalisation
    schemas.py            pydantic request/response models
    errors.py             the {"error": {...}} envelope
    logging_filters.py    password redaction
    middleware.py         size limit, rate limit, security headers, request id
    dependencies.py       FastAPI dependency wiring
    main.py               create_app()
    repositories/         LeadRepository protocol + SQLAlchemy implementation
    providers/            base, mock, khaibao9610, factory
    routers/              health, registrations, admin
    services/             registration orchestration
  alembic/                migration environment; 0001_create_leads,
                          0002_consent_and_fingerprint
  tests/                  offline pytest suite
```

### Database

Table `leads`, with indexes on `phone`, `registration_status`, `created_at`;
unique on `idempotency_key` (nullable) and on `tracking_token`; and a **partial**
unique index enforcing at most one `REGISTER_LEAD` + `REGISTERED` row per phone
(`sqlite_where` / `postgresql_where`, so the same rule holds on both engines).

That rule is a database constraint, not just application logic: two concurrent
registrations for the same phone cannot both win.

`request_fingerprint`, `consent_given_at` and `consent_version` were added by
`0002_consent_and_fingerprint`. Both migrations are guarded rather than
unconditional, so `alembic upgrade head` is safe whether or not
`AUTO_CREATE_SCHEMA` already produced the tables — and `0001` now **verifies**
the shape of an existing `leads` table instead of assuming any table with that
name is its own. A differently-shaped or older `leads` fails loudly with an
explanatory message rather than being silently stamped at head.
