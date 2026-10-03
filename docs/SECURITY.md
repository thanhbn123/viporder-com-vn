# Security posture — VIPORDER.COM.VN

**Revision documented:** the tree at `origin/develop` `4d026def`
(this document is committed on top of it, without changing it)

> Values in the examples below are placeholders. Real tokens, lead IDs and
> customer codes are never reproduced in this repository.

This document describes the security mechanisms that exist at that revision, and
— with equal weight — their limits. Every non-obvious claim carries a `file:line`
anchor.

**Update for `f626644` (`docs/phone-claim`, "reserve the phone before calling the
provider").** That revision added the phone claim — one attempt in flight per
phone, enforced by a partial unique index rather than by application code — and
this document was updated for it: §11.1 now names the new column, §12 carries a
new residual risk 16 and an extended risk 12, §13 records the corrections, and
§14's PostgreSQL item was corrected because the claim cannot be tested on SQLite
at all. The mechanism itself is described in `docs/REGISTRATION-FLOW.md` §3.4.
Anchors outside those sections were not re-verified for this update and still
belong to the revision above, so a reader following one of them should expect to
land near, not on, the quoted line.

§2.3 describes a control that **was broken and is now fixed**; it records both the
measurement that proved the fix and what was wrong before, because a control that
claimed "works for any handler" while leaving tracebacks untouched is worth
remembering. §12 lists the residual risks that remain.

This is not a security assessment and it claims no status. No penetration test,
threat model or security review was performed.

---

## 1. Input validation

### 1.1 Where it happens

Server-side, before any business logic: FastAPI deserialises the request body into
`RegistrationCreate` (`backend/app/routers/registrations.py:22`,
`backend/app/schemas.py:73-123`), so a validation failure never reaches
`RegistrationService` and never creates a row or contacts the provider.

`extra="ignore"` is set on both the request model and `Attribution`
(`schemas.py:74`, `:36`), so unknown keys are dropped rather than rejected.

| Field | Rule | Rejection | Anchor |
|---|---|---|---|
| `full_name` | required; 2–120 chars after trim | `422` | `schemas.py:76`, `:92-98` |
| `phone` | required; must normalise to a Vietnamese number | `422` | `schemas.py:100-106`, `phone.py:29-67` |
| `password` | required; 8–200 chars | `422` | `schemas.py:108-116` |
| `email` | optional; blank/whitespace → `None`; otherwise `EmailStr` | `422` if non-blank and malformed | `schemas.py:79`, `:85-90` |
| `province` | optional; max 120 | `422` | `schemas.py:80` |
| `service_interest` | optional; one of `transport`, `official_import`, `customs`, `order` | `422` | `schemas.py:26`, `:81` |
| `consent` | required; must be exactly `True` | `422` | `schemas.py:82`, `:118-123` |
| `attribution.*` | optional; each ≤300 chars | `422` | `schemas.py:38-44` |
| `attribution.landing_page` | if present, absolute `http`/`https` URL with a netloc | `422` | `schemas.py:62-70` |
| `Idempotency-Key` header | optional; ≤128 chars | `400` if longer | `services/registration.py:495-507` |
| request body size | ≤ `MAX_REQUEST_BYTES` (default 65536) | `413` | `middleware.py:67-127`, `config.py:104` |

Phone validation is deliberately strict rather than merely syntactic: separators
are stripped, `+`/`00` prefixes handled, exactly 9 national digits required, and
the first national digit must be one of `235789` (`phone.py:14-22`, `:64-65`). The
comment states the intent — rejecting `00 84…` and `84 0…` mistakes "instead of
silently storing a phone nobody can dial" (`phone.py:19-21`).

The client mirrors some of this for UX (`static/js/register.js:211-232`) but is
explicit that it is not the boundary: "Client-side validation is UX only — the
server always validates again" (`static/js/register.js:23`).

### 1.2 What is deliberately not echoed back

Pydantic echoes the offending input in its error list. The handler strips it, in
both representations:

- `validation_fields()` keeps only the field name and message, dropping `input`
  and `ctx` — with the reason stated inline: for a password field, echoing the
  input "would put the plaintext password into the HTTP response *and* into the
  access log" (`backend/app/errors.py:82-94`).
- `sanitised_errors()` does the same for the FastAPI-shaped `detail` list, keeping
  only `type`, `loc`, `msg` (`errors.py:97-108`).

Asserted by `backend/tests/test_validation.py:44-49` (a rejected password does not
come back in the body) and `backend/tests/test_password_leakage.py:129-138` (three
different `422` paths, password absent from `response.text` and from
`str(response.json())`).

The generic 500 never carries detail either: `_internal_error_handler` logs the
traceback server-side and returns a flat message (`backend/app/main.py:132-148`).
`test_security.py:346-360` plants an exception whose message contains a fake
secret and a fake filesystem path and asserts neither appears.

### 1.3 Request-id injection

`X-Request-Id` is echoed back and used as a log correlation id, so an unvalidated
value would be a log-injection primitive. `_clean_request_id()` accepts a client
value only if it is ≤64 chars and drawn from `[A-Za-z0-9-_.]`; otherwise a fresh
32-hex id is generated (`backend/app/middleware.py:281-294`, `:216-217`). Asserted
at `test_security.py:79-85` with a `<script>` payload.

### 1.4 The API documentation surface

`/api/docs` and `/api/openapi.json` enumerate every route, parameter, schema and
error code — a map for an attacker, and nothing the public form needs. They are
gated: off when `APP_ENV=production`, on everywhere else, with `ENABLE_API_DOCS`
as an explicit operator override in either direction
(`backend/app/config.py:131-143`, `backend/app/main.py:60-66`). ReDoc is never
served (`main.py:67`). Measured:

```
APP_ENV=production  /api/docs              -> 404
APP_ENV=production  /api/openapi.json      -> 404
APP_ENV=production  /api/v1/health         -> 200
APP_ENV=development /api/docs              -> 200
```

Asserted in `backend/tests/test_api_docs.py:15-38`, including
`APP_ENV="  PRODUCTION  "` being matched case-insensitively and trimmed.

---

## 2. Password handling

The rule is that a registration password is never persisted, logged or traced.
Four mechanisms support it. **§2.3 documents a gap that existed in the third of
them and has since been closed** — with the measurement showing it is closed, and
a note on what was wrong.

### 2.1 Structural prevention — the password cannot be stored

- **No column.** The `leads` table has no password column, no shadow column and no
  payload blob — stated in the model docstring (`models.py:7-9`) and asserted
  structurally at `test_password_leakage.py:152-158`.
- **No repository method can take one.** The docstring names the absence of "any
  method that takes or returns a password, and no generic 'update anything'
  escape hatch" (`repositories/base.py:5-8`).
- **`SecretStr`.** `RegistrationCreate.password` is a `pydantic.SecretStr`
  (`schemas.py:78`), so `repr()`, `str()`, `model_dump()` and tracebacks render
  `**********` (`schemas.py:1-8`). The single unwrap is at the provider call
  (`services/registration.py:245`).
- **`repr=False` on the transport object.** `RegistrationRequest.password` is
  `field(repr=False)` so an f-string cannot print it
  (`providers/base.py:43`), and `redact_request()` exists to produce a log-safe
  view that substitutes a placeholder (`providers/base.py:88-94`).
- **The one hash that is stored excludes it.** `request_fingerprint` is a SHA-256
  over the canonical body with the password deliberately left out
  (`services/registration.py:519-522`): "a hash of a password is still
  password-derived material, and storing it would create an offline-cracking
  target". Asserted at `test_idempotency.py:233-241`.
- **Raw-bytes proof.** After a successful registration the SQLite file *and its
  WAL/SHM/journal sidecars* are read as raw bytes and searched for the password,
  with a sanity check that the same blob really does contain the phone number and
  `REGISTERED` (`test_password_leakage.py:44-66`). The same is done for a
  `PENDING` lead (`:69-78`).

### 2.2 Redaction — the layers, precisely

`backend/app/logging_filters.py` installs a global record factory plus a filter
and a formatter. The module docstring now states the design correctly: redaction
"happens at the **record**, in the log-record factory", and `RedactingFormatter`
is "a second, independent layer … belt-and-braces, not the primary control"
(`logging_filters.py:6-27`).

| Layer | Class / function | Operates on | Installed by |
|---|---|---|---|
| Record factory (global) | `install_record_factory()` | every `LogRecord` at creation | `logging_filters.py:222-241` |
| Record filter | `PasswordRedactionFilter` | a record, when a handler/logger runs its filters | `logging_filters.py:146-158` |
| Render formatter | `RedactingFormatter` | the fully formatted string | `logging_filters.py:244-248` |

`scrub_record()` (`logging_filters.py:176-216`) rewrites:

- `record.exc_info` — rendered through `logging.Formatter().formatException()`
  by `_render_exception()` (`:166-173`), scrubbed, stored in `record.exc_text`,
  and `exc_info` set to `None` (`:185-188`). The docstring explains why this is
  first: a traceback is "a leak channel in its own right: an exception raised
  while handling a registration carries the request (including the plaintext
  password) in its message. Only handlers that use *our* formatter would be
  protected by formatter-level scrubbing, and any handler may format the record
  itself" (`:179-184`).
- `record.exc_text` when set directly (`:189-190`), and `record.stack_info`
  (`:192-193`).
- `record.msg` when it is a `dict`: values scrubbed recursively (`:195-196`).
- `record.args` when a dict, tuple or scalar: values scrubbed (`:198-204`).
- when `args` are present, the record is rendered once, scrubbed, then **frozen
  with `args = ()`** (`:206-213`). That freeze is deliberate: rewriting the
  template `"password=%s"` to `"password=<redacted>"` while leaving the argument in
  place makes `%`-formatting raise, and "a logging error handler is a
  surprisingly good way to leak the very value you were hiding" (`:149-153`).

**What is scrubbed in the text itself** (`scrub_text()`, `:118-129`): two things.
(1) literal secrets registered at runtime via `register_secret()`, replaced
longest-first so a secret containing another is not left as a recognisable
fragment (`:111-115`); (2) a regex for `password=…` / `"password": "…"` fragments
(`:69-72`). The registration path registers the incoming password before building
the provider request (`services/registration.py:249`), so an unrelated log line
containing it is scrubbed too. The cache is bounded to the 64 most recent values
and ignores strings shorter than 6 characters (`:47`, `:79`, `:87-90`).

Keys whose *value* is a secret regardless of appearance include `password`,
`passwd`, `pwd`, `pass`, `confirm_password`, `secret`, `token`, `api_key`,
`authorization` (`:50-66`).

### 2.3 The traceback gap — measured closed

The previous revision of this document recorded that `scrub_record()` never
inspected `record.exc_info`, so tracebacks were scrubbed **only** by
`RedactingFormatter`, and any handler not using it would emit an exception message
containing the password in clear. That was true at `fcecb00` and is **no longer
true**.

**What the code does now.** `scrub_record()` renders the traceback, scrubs it and
clears `exc_info` (`logging_filters.py:185-188`), so the record itself carries no
readable password before any handler sees it. Because that happens in the global
record factory (`:222-241`), it applies regardless of handler order, handler
ownership, or formatter choice.

**Re-measured, one scenario per process** (so no probe can contaminate another),
with `configure_logging()` having installed the factory — confirmed `True`. Each
line logs an exception whose message contains the literal password, through a
handler with a **plain** `logging.Formatter`:

```
pre_handler            leak=False
    |> RuntimeError: upstream rejected account with password <redacted>
basicconfig_after      leak=False
    |> RuntimeError: upstream rejected account with password <redacted>
nonroot_plain          leak=False
    |> RuntimeError: upstream rejected account with password <redacted>
caplog_like            leak=False
    |> RuntimeError: upstream rejected account with password <redacted>
no_register_equals     leak=False
    |> RuntimeError: upstream rejected account with password=<redacted>
no_register_space      leak=True
    |> RuntimeError: upstream rejected account with password CANARY-PASSWORD-NOT-A-REAL-SECRET
no_configure_logging   leak=True
    |> RuntimeError: upstream rejected account with password CANARY-PASSWORD-NOT-A-REAL-SECRET
```

Reading the seven scenarios:

| Scenario | Meaning | Result |
|---|---|---|
| `pre_handler` | a plain handler installed on the root logger **before** `create_app()` | **no leak** — the ordering dependency is gone |
| `basicconfig_after` | `logging.basicConfig(force=True)` with its own plain formatter | **no leak** |
| `nonroot_plain` | a non-root logger, plain formatter, **no filter attached**, `propagate=False` | **no leak** — nothing was configured for it and it still cannot see the password |
| `caplog_like` | a handler added to root after the app, with no filter | **no leak** |
| `no_register_equals` | the secret is **not** in the cache, but appears as `password=<value>` | **no leak** — the key/value regex catches it |
| `no_register_space` | the secret is **not** in the cache, written `password <value>` with a space | **leak** — see §2.4 |
| `no_configure_logging` | `create_app()` never ran, so the factory was never installed | **leak** — see the boundary below |

The same behaviour is pinned by tests with positive controls, so a passing
assertion cannot be vacuous — each asserts that the traceback really did reach the
handler and that `<redacted>` appeared:

- `test_provider_failure_traceback_does_not_leak_the_password` — via `caplog`,
  with a provider that raises with the password in its message
  (`backend/tests/test_password_leakage.py:163-190`).
- `test_exception_traceback_is_scrubbed_before_any_handler_formats_it` — unit
  level: asserts `exc_info is None`, `exc_info_scrubbed is True`, and that a plain
  `logging.Formatter().format(record)` contains no password (`:193-217`).
- `test_password_does_not_leak_through_a_handler_registered_before_the_app`
  (`:220-249`) and `test_basicconfig_handler_is_covered_too` (`:252-274`).

**How this was wrong before — worth remembering.** The previous version of the
control scrubbed `record.msg` and `record.args` and nothing else, while its
docstring claimed it "works for any handler — including `caplog` and third-party
handlers that never see our formatter" (`logging_filters.py`, `fcecb00`
revision). That claim was true for the *message* and false for the *traceback*:
the one channel that actually carries a credential out of a provider call was the
one left unscrubbed, and the layer that would have caught it was the layer the
docstring dismissed as secondary. The falsifying shape was an exception whose
message contained the password — which is now the shape the tests use
(`test_password_leakage.py:163-171`). The lesson recorded here is narrow and
practical: a redaction control is only as good as the *set of fields it enumerates*,
and "any handler" was never the axis that mattered.

### 2.4 What still limits the control

Three boundaries remain, and they are real rather than theoretical:

1. **The literal-secret cache holds only the 64 most recent values and ignores
   strings shorter than 6 characters** (`logging_filters.py:47`, `:79`, `:87-90`).
   The test file states the consequence itself: "a password that some code formats
   into a message with no `password` marker, after it has fallen out of that cache
   (more than 64 newer passwords), would not be scrubbed. Closing that would mean
   keeping every password for the process lifetime, which is worse."
   (`test_password_leakage.py:88-93`). The `no_register_space` scenario above is
   exactly this case: a secret absent from the cache and written without a `:`
   or `=`, so neither mechanism matches. The bound is enforced by
   `test_password_leakage.py:230-243`.
2. **Scrubbing happens only if `configure_logging()` has run**, which happens
   inside `create_app()` (`backend/app/main.py:51`). The `no_configure_logging`
   scenario shows the consequence: if the service is imported and used without
   `create_app()`, no record factory, no filters, no scrubbing. `create_app` is
   the only supported entry point and the ASGI target
   (`backend/app/main.py:151`), so this is a boundary of the design rather than a
   live hole — but it is a boundary.
3. **The key/value regex requires `:` or `=`** between the key and the value
   (`logging_filters.py:69-72`). A password mentioned in prose relies entirely on
   the literal cache, i.e. on limit 1.

### 2.5 The retry consequence

Because nothing stores the password, an operator retry must supply it again
(`services/registration.py:153-159`, `:167-169`). This is a deliberate trade
recorded in `backend/README.md:186-193`, and it is a functional limitation rather
than a security one — but it is the direct cost of the constraint, so it belongs
here.

---

## 3. Request size limit and rate limiting

### 3.1 Size limit

`RequestSizeLimitMiddleware` applies to `POST`/`PUT`/`PATCH` only
(`middleware.py:75-77`). It checks the declared `Content-Length` first and rejects
immediately if it exceeds the limit (`:79-86`); if the header is missing or
unparseable it counts bytes as they stream in and rejects the moment the
accumulated body exceeds the limit (`:88-98`). Either path returns `413` with
`{"error": {"code": "PAYLOAD_TOO_LARGE", ...}}` (`:112-127`).

It is written as a raw ASGI middleware rather than `BaseHTTPMiddleware`
specifically so it can reject before the body is buffered, and so it can replay
the body it already read instead of losing it (`middleware.py:1-8`, `:100-110`).

Default `65536` bytes, minimum enforced by pydantic at `1024`
(`config.py:104`). nginx independently caps request bodies at `64k`
(`deploy/nginx/viporder.com.vn.conf:97`), so the limit is enforced at both tiers.

### 3.2 Rate limiting

Sliding window, per client IP, `POST /api/v1/registrations` only
(`middleware.py:174-182`, `:25`). Defaults: 10 attempts per 600 seconds
(`config.py:105-107`). On rejection it returns `429` with a `Retry-After` header of
whole seconds, always ≥1 (`middleware.py:186-199`, `:172`); measured as
`Retry-After: 600` on a fresh bucket.

The client IP comes from `client_ip()`: `X-Forwarded-For` is consulted **only**
when `TRUST_PROXY_HEADERS=yes`, otherwise `scope["client"]` is used
(`middleware.py:47-64`). See §5.3 — in the real deployment uvicorn has already
rewritten `scope["client"]`, which changes what that means in practice.

The limiter runs **before** the body-size check (§3 of `docs/ARCHITECTURE.md`), so
an oversized body still consumes an attempt.

### 3.3 The honest limitation — bounded by configuration, not removed

**The limiter is in-process and therefore not shared across workers.** Its entire
state is a `dict[str, deque[float]]` plus a `threading.Lock` on the middleware
instance (`middleware.py:156-157`). The docstring says so and sketches the fix:
replace `self._hits` with a Redis sorted set per key (`ZADD` now /
`ZREMRANGEBYSCORE` older than the window / `ZCARD`), which makes the limit shared
"and the middleware boundary does not change" (`middleware.py:130-138`).

**Both deployment paths now run a single uvicorn worker, and that is what keeps
the limit honest** — `--workers 1` at `deploy/systemd/viporder-web.service:51`
(rationale at `:31-39`) and at `deploy/Dockerfile:68` (rationale at `:53-57`).
With N workers each process holds its own counter, so the effective limit for one
client IP becomes N × `RATE_LIMIT_ATTEMPTS`; the unit file records the trade as
"a correct limit matters more than a second worker" and says to raise the count
only after moving the limiter to a shared store. **The limitation is therefore
suppressed by configuration, not removed** — the limiter is still per-process, and
the guarantee disappears the moment `--workers` is raised without a shared store.

Two further notes: the lock is a `threading.Lock`, not an async lock, which is
correct for the synchronous critical section but assumes in-process shared memory;
and the window is keyed on `time.monotonic()` (`middleware.py:185`), so it is
immune to wall-clock changes but does not survive a restart — a restart clears
every counter.

---

## 4. Security headers the application sets

`SecurityHeadersMiddleware` stamps six headers on every HTTP response
(`middleware.py:243-254`):

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Content-Security-Policy` | see below |
| `Permissions-Policy` | `accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

The CSP is default-deny, chosen because the API serves JSON only: "nothing needs
inline script, remote frames, plugins or form submissions"
(`middleware.py:27-39`):

```
default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none';
object-src 'none'; img-src 'self' data:; style-src 'self'; script-src 'self';
connect-src 'self'
```

**HSTS is conditional.** `Strict-Transport-Security: max-age=31536000;
includeSubDomains` is emitted only when the request is HTTPS — either
`scope["scheme"] == "https"`, or `X-Forwarded-Proto: https` **when**
`TRUST_PROXY_HEADERS=yes` (`middleware.py:256-262`, `:252-253`). Emitting it from a
plain-HTTP dev server would pin the browser to a scheme that is not served, which
is why the guard exists (`middleware.py:229-234`). Asserted both ways at
`test_security.py:53-66`.

In the real deployment the second branch is reached through uvicorn rather than
through the app's own flag — see §5.3.

Headers are applied with `MutableHeaders.setdefault` (`middleware.py:271-276`), so
a response that sets its own value wins. No route in this revision sets one.

That the headers survive the `413` and `429` paths follows from the middleware
order — security headers are outside both — and is asserted at
`test_security.py:43-50`.

---

## 5. The nginx tier and the `add_header` inheritance trap

### 5.1 What the tier does

nginx terminates TLS, serves the static site from `/srv/viporder/site`, and
proxies `/api/` to the application on loopback. The server block sets the TLS
protocol floor and stapling (`deploy/nginx/viporder.com.vn.conf:82-87`), caps
request bodies at `64k` (`:97`), redirects `www` and plain HTTP to the canonical
host (`:51`, `:67`), and includes the shared security-headers snippet at server
level (`:94`) so locations that override nothing still inherit all six headers.

`deploy/nginx/viporder-security-headers.conf:29-38` sends:

| Header | nginx value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000` — **`includeSubDomains` is deliberately NOT sent yet**; see §5.2 |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=(), payment=()` |
| `Content-Security-Policy` | permissive — see below |

All six use `always`, so they are sent on error responses too.

**The static-site CSP deliberately differs from the API's.** It allows
`'unsafe-inline'` for script and style and names the analytics domains, because
the (currently dormant) analytics layer needs them; the file says "Tighten once
GTM/GA4/Meta Pixel are actually enabled"
(`deploy/nginx/viporder-security-headers.conf:35-37`). The API's CSP stays
default-deny (§4). So "the site's CSP" is not a single value: the HTML document
and the JSON API are governed by different policies. That is a design decision,
not drift — but it means the strict policy in §4 protects only the API responses.

The analytics layer those domains are reserved for is **dormant by default**:
`VIPORDER_TRACKING.enabled = false` and each vendor flag is `false`
(`static/js/analytics.js:72-76`), with the third-party loaders skipped while
disabled (`:601`). So the permissive CSP is currently permissive for code that
does not run.

### 5.2 The trap

Nginx does **not** merge `add_header` across configuration levels. Quoted from
`deploy/nginx/README.md:38-45`:

> **Nginx `add_header` does not merge across configuration levels.** The documented
> rule is:
>
> > These directives are inherited from the previous configuration level **if and
> > only if there are no `add_header` directives defined on the current level**.
>
> So a single `add_header Cache-Control …` inside a `location` block silently
> cancels **every** server-level `add_header` for that location.

The homepage is served by `location = /`, which needs its own `Cache-Control` —
so before the include was added, the HTML document shipped with **no CSP, no
HSTS, no `X-Frame-Options`, no `X-Content-Type-Options`, no `Referrer-Policy` and
no `Permissions-Policy`**, while `curl -I` against any path that did inherit
looked perfectly correct (`deploy/nginx/README.md:47-51`).

`deploy/nginx/README.md:53-58` reports the measurement (real nginx 1.31.6, same
config, only the include differing): `/` and `/static/style.css` return **0 / 6**
security headers without the include and **6 / 6** with it.

**Independently reproduced here**, not transcribed — nginx 1.31.6, a minimal
`server` block including the real snippet at server level, and a `location = /`
plus a `location ~* ^/static/…$` that each declare their own `Cache-Control`,
run twice with the only difference being the include:

```
=== A) location declares add_header WITHOUT the include (the trap) ===
  no_include         /                  -> HTTP/1.1 200 OK | security headers 0/6 | Cache-Control x1
  no_include         /static/style.css  -> HTTP/1.1 200 OK | security headers 0/6 | Cache-Control x1

=== B) same location WITH the include (the fix) ===
  with_include       /                  -> HTTP/1.1 200 OK | security headers 6/6 | Cache-Control x1
  with_include       /static/style.css  -> HTTP/1.1 200 OK | security headers 6/6 | Cache-Control x1
```

The `Cache-Control x1` column also confirms rule 3 below: exactly one
`Cache-Control` per response, so `expires` is not being combined with an explicit
`Cache-Control`.

The mitigation is structural, not documentary. All six headers live in one
snippet and every `location` that declares its own `add_header` also includes it
(`deploy/nginx/viporder.com.vn.conf:137-156`; the note at `:134-136` warns against
removing the includes). The rules as written
(`deploy/nginx/README.md:62-71`) are:

1. Never set a security header directly in a `location` block — two copies drift.
2. Any `location` that declares its own `add_header` must also include the snippet.
3. Do not use `expires` together with an explicit `Cache-Control`.

Rule enforcement is a **program, not a comment**. `tools/check_nginx_config.py`
parses the site config's `location` blocks and fails if a location declares
`add_header` without the include, or sets one of the six security headers directly
(`tools/check_nginx_config.py:111-148`), and it verifies the snippet actually
defines all six (`:105-109`). It was negative-controlled against the exact bug
before being trusted (`deploy/nginx/README.md:79-82`). It runs in CI
(`.github/workflows/ci.yml:89`) and passes at this revision:

```
  line 134: location = / — declares Cache-Control and includes the snippet (ok)
  line 140: location ~* ^/static/.*\.(css|js|svg|png|jpg|jpeg|webp|ico|woff2?)$ — declares Cache-Control and includes the snippet (ok)
  line 149: location ~* ^/(robots\.txt|sitemap\.xml|favicon\.ico)$ — declares Cache-Control and includes the snippet (ok)

nginx-config: 9 location block(s), 0 error(s)
scope: add_header/include placement in deploy/nginx/*.conf only — this does NOT
validate nginx syntax; run `nginx -t` for that.
```

The checker states its own scope, which is the right habit: it verifies placement
only and explicitly does **not** validate nginx syntax.

### 5.3 Forwarded headers: which layer is actually trusted

The application's `TRUST_PROXY_HEADERS` defaults to `no` (`config.py:108`), and
that reads like the switch that decides whether `X-Forwarded-*` is believed. In
the deployed configuration it is **not** the operative control, because uvicorn is
started with `--proxy-headers` (`deploy/systemd/viporder-web.service:52`) and
rewrites `scope["client"]` and `scope["scheme"]` *before* the application sees the
request. It believes those headers only from a peer in `--forwarded-allow-ips`
(uvicorn's default is `'127.0.0.1,::1'`), and the unit file now pins that to
`127.0.0.1` explicitly (`deploy/systemd/viporder-web.service:53`; rationale at
`:40-47`). The app binds loopback only, so nginx is the only peer that can set
those headers — and it sets them from `$proxy_add_x_forwarded_for` and `$scheme`
(`deploy/nginx/proxy_params_viporder:7-8`).

Measured against a real uvicorn started with the unit file's flags and the
app-level `TRUST_PROXY_HEADERS=no`:

```
=== A) HSTS with TRUST_PROXY_HEADERS=no, but uvicorn --proxy-headers ===
  plain (no XFP):          0 HSTS header(s)
  X-Forwarded-Proto:https: 1 HSTS header(s)

=== B) rate limiter keying ===
  1st, XFF 203.0.113.9  -> 201
  2nd, XFF 203.0.113.9  -> 429   (same forwarded client -> limited)
  3rd, XFF 198.51.100.4 -> 409   (different client -> past the limiter, then DUPLICATE_PHONE)
```

So HSTS **is** emitted over the proxy even with the app flag off, and the rate
limiter **does** key on the real client IP. The trust boundary is therefore:
only a loopback peer may assert a client identity, and only nginx is a loopback
peer, and the unit file now says so explicitly rather than leaning on uvicorn's
default. That is sound, but it still rests on facts outside the application (the
uvicorn flags and the bind address), and the application enforces none of them.
If the app were exposed directly while running `--proxy-headers` with a wide
allow-list, `X-Forwarded-For` would become client-controlled and the limiter could
be bypassed by rotating the header — which is precisely why the compose path's
`--forwarded-allow-ips *` is tied to its port staying unpublished
(`deploy/Dockerfile:58-61`). §12 records this.

The previous revision of this document claimed the opposite — that with
`TRUST_PROXY_HEADERS=no` a proxy would collapse every client into one bucket and
suppress HSTS. That was wrong for this deployment, because it reasoned about the
application's flag without accounting for uvicorn rewriting the scope first. It is
corrected here, and the correction is recorded in §13.

### 5.4 What is verified, and what is not

Verified locally: the configuration parses; all six headers are present on `/`, on
a static asset and on `robots.txt`; exactly one `Cache-Control` per response; the
checker passes in CI (`deploy/nginx/README.md:88-90`, plus the independent
reproduction in §5.2).

**Not verified:** behaviour on the production host, TLS/OCSP stapling with a real
certificate, HTTP/2 negotiation, and the `/api/` proxy path against a running
backend (`deploy/nginx/README.md:92-94`). `deploy/post-deploy-check.sh` exists for
those live checks and was not run.

---

## 5.2 Strict-Transport-Security — `includeSubDomains` is withheld, on purpose

The header ships as `max-age=31536000` **without** `includeSubDomains`. That is a
staged decision, not an oversight.

**Measured 2026-10-02:** `viporder.com.vn`, `www.viporder.com.vn` and
`khachhang.viporder.com.vn` all resolve to **103.159.50.70**, and the apex
certificate's SANs do not cover `khachhang`. The cutover therefore replaces the
server software on the host that also serves the **live customer portal**.

**Why the stakes are asymmetric.** `includeSubDomains` binds every subdomain to
HTTPS for a year, and the browser caches it. If the portal's certificate does not
line up at cutover, returning customers get a TLS error they **cannot click
through** — and rolling the server back does not release them, because the header
that causes the failure is already on their machine.

**The staged plan:**
1. serve `max-age=31536000` alone; complete the cutover; confirm every subdomain is
   on valid HTTPS and intends to remain there;
2. then add `includeSubDomains` in a **separate, deliberate** change.

The header is worth having. It is not worth having **before** the evidence.

## 6. CSRF — the architecture, and the actual reasoning

There is no CSRF token anywhere in this revision, and that is a reasoned position,
not an omission.

**1. There is no ambient authority to forge.** CSRF works by making a victim's
browser attach credentials the attacker cannot read. Verified: the backend has no
cookie handling, no session middleware and no JWT/oauth machinery at all —

```
$ grep -rniE "set_cookie|set-cookie|\bcookie|SessionMiddleware|itsdangerous|jwt|oauth" backend/app/
  (no matches)
```

No route returns `Set-Cookie`, and no route reads a cookie or an `Authorization`
header. The only credential-like inputs are `X-Tracking-Token` and `X-Admin-Token`,
and both are **explicit headers, not ambient browser state** — a cross-site form
submission cannot set a custom header. The client sends
`credentials: "same-origin"` (`static/js/register.js:620`), which attaches
credentials only for same-origin requests and is therefore not a CSRF vector.

**2. The endpoint is anonymous by design.** `POST /api/v1/registrations` is a
public registration endpoint; anyone may call it. A successful CSRF "attack"
would create a lead — which the attacker could equally do by calling the API
directly. There is no privileged action to trigger on a victim's behalf, because
there is no victim identity.

**3. The real request is not a CORS-simple request.** The client sends
`Content-Type: application/json` plus a custom `Idempotency-Key` header
(`static/js/register.js:614-618`). Neither is CORS-safelisted, so a cross-origin
`fetch` triggers a preflight, which fails because CORS is off by default (§8). A
classic HTML form cannot produce that request at all.

### 6.1 Residual risk

Stated honestly, because "CSRF is not needed" is too strong a summary:

- **The defence against a cross-site form post is the content-type parse, not a
  token.** The form itself carries `method="post"` and a real `action`
  (`index.html:461`), which a cross-site attacker could also post to. Measured,
  that produces a `422`, not a registration:

  ```
  form-encoded POST -> 422 {'error': {'code': 'VALIDATION_ERROR',
    'fields': {'body': 'Input should be a valid dictionary or object to extract fields from'}}}
  ```

  So the encoding is what stops it. If a future revision accepts a
  form-encoded body — for example to make the no-JS fallback work — that
  protection disappears and a cross-site form post becomes a real lead-creation
  primitive. Point 2 still bounds the impact to "creates a lead", and the rate
  limiter would be the only defence left.
- **CSRF is not the right frame once any session is added.** If a cookie-based
  session is ever introduced, this whole section stops applying and a token
  becomes mandatory. Nothing today enforces that link — it relies on whoever adds
  the session remembering.
- **The `Idempotency-Key` header is not a CSRF defence.** It happens to force a
  preflight, but nothing verifies its presence, and a missing key is accepted
  (`services/registration.py:496-500`).

---

## 7. Open redirect prevention

The customer-portal destination is a **server-side constant**, not configuration
and not request-derived (`backend/app/config.py:20-24`):

```python
# The customer portal is a fixed, server-side destination. It is intentionally
# NOT configurable and NEVER derived from user input: an attacker-supplied
# redirect target is the classic open-redirect bug.
LOGIN_URL = "https://khachhang.viporder.com.vn"
```

It is exposed as a read-only property (`config.py:145-147`), and the service
returns it via `self.settings.login_url` with the comment "Server-side constant.
Never built from request input." (`services/registration.py:92-95`).

Because it is not a settings field at all, **no environment variable can move it**
— asserted by `test_config.py:122-126`, which sets `LOGIN_URL=https://evil.example`
and asserts the response is still the real portal.

The response bodies are built from that constant (`services/registration.py:401`,
`:413`), so every `201`/`202` carries the fixed value. Asserted with three hostile
inputs — an absolute evil host, a protocol-relative `//evil.example`, and a
look-alike subdomain — at `test_security.py:331-343`.

**There is now a second, independent layer on the client.** Whatever `login_url`
the API returns is used only if it parses to `https:` and exactly
`khachhang.viporder.com.vn`; otherwise the hard-coded constant is used
(`static/js/register.js:322-335`), and the link is assigned with `setAttribute`
rather than `innerHTML` (`:341`). So even a compromised or buggy API response
cannot redirect an existing customer off the approved host. The file frames it as
a business rule (`:320-321`).

Nothing in the application issues an HTTP redirect at all; there is no
`RedirectResponse` in the codebase. The homepage's links to the portal are static
`href`s in `index.html` (`:121`, `:173`, `:264`, `:566`, `:577`, `:646`),
additionally constrained by `tools/check_site.py`, which fails the build if a
login-styled control does not resolve to the portal host
(`tools/check_site.py:323-343`).

---

## 8. CORS

**Off by default.** `CORS_ALLOW_ORIGINS` is empty, and the middleware is only
added when the parsed list is non-empty (`backend/app/config.py:110`,
`backend/app/main.py:98-110`). So the service is same-origin only, and asserted at
`test_security.py:310-312` — an `Origin: https://evil.example` request gets no
`Access-Control-Allow-Origin` header.

The shipped client is same-origin by construction: `API_URL` is the relative path
`/api/v1/registrations` (`static/js/register.js:28`) and the request uses
`credentials: "same-origin"` (`:620`). So CORS is not needed for the registration
form to work in the intended deployment.

**Enabling it requires** setting `CORS_ALLOW_ORIGINS` to a comma-separated list;
values are stripped and empty entries dropped (`config.py:122-124`). What the code
then does:

- `allow_methods=["GET", "POST", "OPTIONS"]` and
  `allow_headers=["Content-Type", "Idempotency-Key", "X-Request-Id"]`
  (`main.py:108-109`) — a narrow allowlist, so the admin and tracking headers are
  **not** exposed cross-origin.
- A wildcard `*` never gets credentials: `allow_all = "*" in origins` forces
  `allow_credentials=not allow_all` (`main.py:103-107`). The comment states the
  reason — the combination is rejected by browsers and is a well-known footgun
  (`main.py:100-102`). Asserted at `test_security.py:321-325`, which checks
  `Access-Control-Allow-Credentials` is absent when the origin is `*`.

---

## 9. Secret management

- **`.env.example` only, with every value empty.** 19 keys documented; a test
  asserts the documented key set matches exactly and that **every** value string
  is empty (`backend/tests/test_config.py:42-51`). An empty value means "unset",
  because `env_ignore_empty=True` (`config.py:63`), so copying the template
  verbatim is harmless — asserted key-by-key at `test_config.py:60-78`.
- **No credential in the repository.** `tools/check_repo_hygiene.py` scans every
  tracked file for secret-shaped strings — private-key blocks, GitHub/AWS/OpenAI/
  Slack/Google tokens, JWTs, and a generic `api_key = "…"` pattern — with
  placeholder values excluded so a template does not trip it
  (`tools/check_repo_hygiene.py:55-65`, `:68-71`). It also fails on forbidden
  paths including `.env`, `*.db`, `var/`, `*.key`, `*.pem` and `*.csv`
  (`:33-50`), and on any tracked file over 1 MiB (`:28`, `:116-119`).
- **The hygiene checker states its own scope**, which is the right habit:
  "tracked-path patterns, file size > 1 MiB, and secret-shaped strings in text
  files only — **binary content is NOT scanned**"
  (`tools/check_repo_hygiene.py:145-146`). It reports 0 errors over the tracked
  tree at this revision.
- **No secret ever gets a default value.** The config docstring states the rule:
  "an unset secret must disable the feature that needs it, never fall back to
  something guessable" (`config.py:3-6`). `ADMIN_API_TOKEN` defaults to `""` and
  an empty token **disables the route** rather than opening it
  (`config.py:93-95`, `:126-128`; `routers/admin.py:49-51`).
- **gitleaks in CI**, as its own job (`.github/workflows/ci.yml`, `secret-scan`).
  The one allowlisted finding is pinned by **fingerprint**, not by path:
  `def20a7…:backend/tests/test_password_leakage.py:generic-api-key:32`
  (`.gitleaksignore`). The file explains the choice: "Deliberately NOT used: a path
  allowlist. Allowlisting the whole file would silently blind the scanner to a real
  secret added to it later." The canary line also carries an inline
  `# gitleaks:allow` (`test_password_leakage.py:38`).
- **Config is centralised.** Verified: no `os.environ` / `getenv` reads anywhere in
  `backend/app/` outside pydantic-settings, so there is one place where an
  environment value becomes application behaviour.
- **Vendor identifiers are not credentials.** The analytics config carries a GTM
  container id, a GA4 measurement id and a Meta Pixel id, all inert while the
  layer is disabled (`static/js/analytics.js:72-76`). These are public by design
  and are not treated as secrets; the layer is dormant.

### 9.1 Limits

- The hygiene scan is text-only, as it says. A secret in a binary blob would not
  be caught by it (gitleaks covers more, but neither is proof of absence).
- `.gitignore` lists `.env` and `.env.*` with an exception for `.env.example`
  (`.gitignore:2-4`). That prevents accidental staging; it does not prevent a
  forced add. `test_config.py:129-131` asserts no `.env` exists in the working
  tree — a point-in-time check, not a control.
- **No secret-rotation mechanism, no vault, and no KMS integration exists.** All
  configuration is process environment, loaded from a single `EnvironmentFile`
  (`deploy/systemd/viporder-web.service:25`). `deploy/env.production.example`
  documents the production keys with empty values. Nothing in this revision
  manages the lifecycle of the upstream credential, because the live adapter has
  none (it sends a customer password, not a service credential — see §10).

---

## 10. External call policy

Two policies: what the call does, and whether it is allowed to happen at all.

### 10.1 Timeout and absence of automatic retry

- **Timeout.** `KHAIBAO9610_TIMEOUT_SECONDS` defaults to `10.0` and is constrained
  to `1.0 … 30.0` by pydantic (`config.py:84`), re-checked at adapter construction
  (`providers/khaibao9610.py:103-108`). It is passed both to the `httpx.Client`
  (`:122`) and per request (`:193`).
- **No automatic retry.** `self.provider.register(request)` is called at exactly
  one site (`services/registration.py:259`) and there is no loop, backoff or retry
  library around it. The only retry in the system is the explicit operator/admin
  route (`routers/admin.py:59`). A timeout therefore produces one attempt and a
  `202`, never a hidden second attempt — which matters because the upstream call
  is not idempotent from the customer's perspective.
- **Transport failures are mapped, not raised.** `httpx.TimeoutException` and
  `httpx.HTTPError` are each caught and become `UNAVAILABLE` with
  `retryable=True` and a distinguishing `error_code`
  (`khaibao9610.py:195-220`).
- **No request body is ever logged**, and the reason is stated: an upstream error
  body can echo request fields (`khaibao9610.py:222-224`, `:29-31`). The success
  body is parsed defensively — `_safe_json()` swallows any parse failure
  (`:269-273`) — and is never logged whole.
- **The proxy tier has its own timeouts**: `proxy_connect_timeout 5s`,
  `proxy_send_timeout 30s`, `proxy_read_timeout 30s`, with
  `proxy_intercept_errors on` (`deploy/nginx/proxy_params_viporder:12-17`).

### 10.2 The two-switch guard

Reaching a real customer system requires **both** switches, and the code that
enforces it is in the adapter's constructor
(`backend/app/providers/khaibao9610.py:91-102`):

```python
        if mode != "http":
            raise ProviderConfigurationError(
                "ViporderFrontendProvider requires KHAIBAO9610_MODE=http "
                f"(got {mode!r}). The default mode is 'mock' on purpose: the "
                "real upstream contract has not been supplied."
            )
        if not enable_real_calls:
            raise ProviderConfigurationError(
                "Refusing to construct the live provider: real calls are "
                "disabled. Set KHAIBAO9610_ENABLE_REAL_CALLS=yes together with "
                "KHAIBAO9610_MODE=http to make real registrations."
            )
```

The defaults make the safe state the default one: `khaibao9610_mode` defaults to
`"mock"` and `khaibao9610_enable_real_calls` to `False`
(`config.py:82`, `:85`). Three further guards run after those two — a
timeout range check (`:103-108`), a non-empty base URL (`:109-110`) and a
non-empty User-Agent (`:111-115`), the last because Cloudflare fronting the
upstream rejects non-browser clients with Error 1010 (`config.py:31-36`).

The failure mode is a **loud refusal at construction**, not silent degradation.
The class docstring states the intent: "Raised rather than silently degrading:
reaching a real customer system must be a deliberate, two-key decision"
(`providers/base.py:80-85`), and the adapter's header adds "a half-configured live
adapter that 'mostly works' is how test data ends up in production"
(`khaibao9610.py:16-18`).

The guard is exercised in four ways: refuses without the real-call opt-in
(`test_khaibao9610_provider.py:58-65`), refuses in mock mode (`:68-75`), refuses
an empty User-Agent (`:78-85`), refuses an out-of-range timeout (`:88-97`), and
the factory propagates the refusal (`:107-114`) while returning the mock by
default (`:117-122`).

### 10.3 What is not established

- **The upstream contract is unverified.** The module header says so in its first
  line: "KHAIBAO9610 provider — CANDIDATE CONTRACT, NOT VERIFIED"
  (`khaibao9610.py:1-9`). The endpoint path `/register`, the body shape and the
  duplicate phrasings are reconstructions from prior work, not observations.
  `DUPLICATE_PATTERN` in particular is a heuristic on Vietnamese and English error
  text (`:53-56`) — a false positive would mark a lead `FAILED` and return `409`
  to a customer who is not actually registered.
- **No live call has ever been made**, so none of the mapping in `:221-263` has
  been observed against the real upstream. Every case is tested through
  `httpx.MockTransport` (`test_khaibao9610_provider.py:48-52`).
- **There is no circuit breaker and no upstream rate limiting.** If the provider
  starts failing, every request still makes a full 10-second attempt. The
  size/rate limits (§3) protect this service, not the upstream.
- **No egress allowlist.** The base URL is configuration (`config.py:83`), so
  changing it changes where customer passwords are sent. There is no restriction
  on the destination beyond the two switches.
- **nginx rate-limits too, and the two layers are not coordinated.** `limit_req`
  is configured in two zones: `viporder_reg` at `10r/m` with `burst=5 nodelay` on
  the registration location, and `viporder_api` at `120r/m` with `burst=40 nodelay`
  on `/api/`, both returning `429`
  (`deploy/nginx/viporder.com.vn.conf:25-26`, `:116-117`, `:126-127`). nginx is the
  outer bound because it sees every request, including ones that never reach the
  application; the application limiter (§3.3) applies underneath. Neither layer is
  aware of the other, so a client can be rejected by either, and the two windows
  are different lengths.

---

## 11. Data protection

### 11.1 What the lead table stores

The `leads` table (§7.1 of `docs/ARCHITECTURE.md`) stores: name, canonical and
display phone, optional email, optional province, optional service interest, seven
attribution fields, provider-issued external ids, an idempotency key, a
request fingerprint, consent timestamp and version, a tracking token, attempt
count, last error code and message, and two timestamps.

Since `f626644` it also stores **`in_flight_at`**: `DateTime(timezone=True)`,
nullable, set when an attempt begins and cleared when that attempt ends
(`models.py:176`; the partial unique index over it is `:216-222`, and §3.4 of
`docs/REGISTRATION-FLOW.md` describes the mechanism). It carries no personal data
of its own — it is a timestamp for the attempt the row already represents, and the
row's `created_at`/`updated_at` already say as much about timing. It is named here
because "what the lead table stores" is a question this section answers
exhaustively, and a column that appears in a schema dump should not be a
surprise.

**It does not store the password** — no column, and the absence is asserted
structurally (`test_password_leakage.py:152-158`, `models.py:7-9`). The stored
fingerprint is a hash of the body with the password excluded (§2.1), so the table
contains no password-derived material either.

**Consent is stored as evidence.** `consent_given_at` and `consent_version`
(`models.py:134-137`) record *when* the customer agreed and *to which wording*,
with the version a server-side constant (`config.py:45`). The model comment makes
the distinction that matters for a personal-data request months later:
enforcement is not evidence (`models.py:127-133`). Asserted in
`test_consent.py:22-50`.

Three storage-side protections worth naming:

- **The tracking token is high-entropy and compared in constant time.**
  `secrets.token_urlsafe(32)` (`services/registration.py:238`), compared with
  `secrets.compare_digest` (`:510-513`).
- **Unknown lead and wrong token return the same `404`**, so the status route
  cannot be used to enumerate lead ids. The comment says so: "the response must
  not confirm that a lead id exists" (`services/registration.py:146-148`).
- **The fingerprint is deterministic but not reversible**, and it deliberately
  omits the password so it cannot serve as a cracking target
  (`services/registration.py:519-522`).

### 11.2 No customer code on a duplicate — proven

Knowing a phone number must not be enough to learn a customer's code. The
provider-duplicate branch keeps the row, marks it `FAILED`, and returns a generic
message — the comment names the reason (`services/registration.py:320-322`):

```python
        if result.status is ProviderStatus.DUPLICATE:
            # The lead row is kept and marked FAILED. We deliberately do not
            # return the existing customer code: knowing a phone number must not
            # be enough to learn a customer's code.
```

Proven, not asserted: `test_duplicate.py:57-70` registers a customer, captures the
returned `external_customer_code` and `lead_id`, then makes a duplicate attempt and
asserts that the code, the lead id, **and the literal string
`external_customer_code`** are all absent from the response text — and that no
second row was created. The provider-duplicate path is covered separately at
`:114-126`.

The same discipline applies to the new `409` path: an
`Idempotency-Key`-reuse refusal leaks nothing about the earlier attempt, and
`test_idempotency.py:138-146` asserts that neither customer's code, lead id, nor
the response-shape string appears — and that the second attempt created no row.

The status endpoint is likewise non-identifying. Its body is `lead_id`,
`registration_status`, `external_customer_code`, `created_at`, `updated_at`
(`services/registration.py:482-489`) — no phone, no email, no name, which the
route docstring states (`routers/registrations.py:48`).

### 11.3 Gaps in data protection

- **No retention or deletion policy is implemented.** Nothing expires, anonymises
  or deletes a lead. `created_at` is indexed (`models.py:183`) so a future sweep
  would be cheap, but no such sweep exists. Personal data accumulates
  indefinitely.
- **The tracking token is returned in the `202` body** (`:371`) and is the only
  thing guarding the status route. Whoever holds it can read that lead's status.
- **PII minimization is partial.** `phone_display` stores a second copy of the
  phone number in a human format (`models.py:94`); `last_error_message` can hold
  up to 500 characters of upstream text, which is truncated but not otherwise
  sanitised (`sqlalchemy_repo.py:146-149`) and which the provider layer warns can
  echo request fields (`khaibao9610.py:222-224`). The provider layer avoids
  copying the body verbatim, but the column is a place where upstream text enters
  the database.
- **The fingerprint is a correlatable value.** It is deterministic over the
  customer's details, so two leads with identical bodies (same name, phone, email,
  province, service, consent and attribution) share a fingerprint. That is exactly
  what makes it work as a replay key, and it is not exposed by any response — but
  it is derived from personal data and stored in the clear.
- **No encryption at rest is configured by the application.** That is a deployment
  concern; `docs/DEPLOYMENT.md` describes the filesystem layout, and the dev
  default is a plain SQLite file (`config.py:74`), git-ignored so it cannot be
  committed (`.gitignore:13-19`).
- **`__repr__` is hardened but it is not the only path.** `Lead.__repr__` prints
  only id, status and display phone (`models.py:195-199`). SQLAlchemy's `echo=True`
  engine option would log SQL statements including bound parameter values —
  `echo` defaults to `False` (`db.py:60`) and is never set from configuration, so
  it cannot be enabled by an environment variable. That is worth knowing rather
  than assuming.

---

## 12. Residual risks

| # | Risk | Why it is accepted for now | What would close it |
|---|---|---|---|
| 1 | Literal-secret cache holds only the 64 most recent passwords, ignores values under 6 chars, and the key/value regex needs `:` or `=` — a password outside all three is not scrubbed (§2.4) | Unbounded retention of customer passwords in process memory is worse; the realistic path (an exception message from the provider) is now covered | Structured redaction-by-key at every call site, or a keyed store with an explicit eviction policy |
| 2 | Scrubbing depends on `configure_logging()` having run, i.e. on `create_app()` (§2.4) | `create_app` is the only supported entry point and the ASGI target | Scrub defensively at the handler level too, independent of startup |
| 3 | The limiter is per-process, so raising `--workers` above 1 would multiply the effective limit by the worker count (§3.3). Both paths currently run `--workers 1` | The single worker is deliberate and documented in the unit file, so the risk is dormant rather than absent — it returns the moment the worker count is raised | Redis sorted set per key, as sketched in the middleware docstring (`middleware.py:130-138`) — the middleware boundary does not change |
| 4 | Rate-limit counters are lost on restart, and a `413` consumes an attempt (§3 of `ARCHITECTURE.md`, §3.2) | Restarts are infrequent; consuming an attempt on a rejected oversized body is the stricter behaviour | A shared store, and a decision recorded either way |
| 5 | `X-Forwarded-For` is believed because uvicorn runs `--proxy-headers`; the `--forwarded-allow-ips` list is the operative control, **not** the app's `TRUST_PROXY_HEADERS=no` (§5.3) | Both paths now pin it explicitly — `127.0.0.1` for systemd, `*` for compose where the port is unpublished so the only peer is the nginx container | Keep the allow-list pinned whenever the bind address changes; the compose `*` holds only while the port stays unpublished |
| 6 | Two rate limiters with different scopes and windows: nginx per IP (10 r/m registrations, 120 r/m `/api/`) and the application per IP per worker (§3.4) | Defence in depth is deliberate; the layers are additive rather than coordinated, and nginx is the outer bound | Record the intended relationship between the two limits, and confirm both reject with `429` in a way the client handles |
| 7 | No CSRF token; the defence rests on the JSON content-type parse plus the absence of ambient credentials (§6) | No cookies, no session, no privileged action; the endpoint is anonymous by design | A CSRF token the moment any cookie-based session is introduced; and revisit if a form-encoded body is ever accepted |
| 8 | Untagged anonymous clients can create unlimited leads across workers/restarts IP-rotating (§3.3) | The limiter plus body limit bound the blast radius per IP; there is no anti-automation layer | Shared limiter (3), plus a CAPTCHA or proof-of-work on the public form |
| 9 | Duplicate detection upstream is a text heuristic; a false positive returns `409` to a genuinely new customer (§10.3) | The real contract has not been supplied, so no better signal exists; the lead row is still retained and marked `FAILED` | A machine-readable duplicate code from the provider, per issue #4 |
| 10 | A **legacy** `FAILED` row with no stored reply relies on a derived fallback: the cause is inferred from `last_error_code`, and an unrecognised cause returns a generic `409 REGISTRATION_FAILED` rather than the original status (`REGISTRATION-FLOW.md` §8.3) | Only rows written before migration `0003_stored_response` are affected, and this service never writes such a row; the fallback never claims success or pending | The fallback is a migration-window concern only — it disappears once no pre-`0003` row remains. Tightening it further means losing the ability to answer those rows at all |
| 11 | A JavaScript-disabled submission posts form-encoded and receives a raw JSON `422` page (`REGISTRATION-FLOW.md` §2.4) | Nothing advertises a working no-JS registration; the form is JS-driven | Either accept form-encoded bodies (which weakens §6) or make the fallback a static explanation page |
| 12 | `_is_idempotency_conflict()`, `_is_phone_conflict()` and `_is_phone_uniqueness()` match driver error **text**, so they are tied to SQLite/psycopg wording (`sqlalchemy_repo.py:212-214`, `:217-229`, `:232-246`) | The design does not depend on the text identifying *which* index: at INSERT the row is always `PENDING`, so a phone-uniqueness failure there can only be the in-flight claim (`sqlalchemy_repo.py:88-95`), and the docstring says the two phone indexes are deliberately not told apart because SQLite does not name them (`:232-240`). That disambiguation was exercised on SQLite only when this row was written; the PostgreSQL-only concurrency job now runs the same insert path against psycopg, where the index *is* named (`.github/workflows/ci.yml:132-151`, `:166-171`) — a reading of the job, not a run made here | Inspect the constraint name structurally where the driver supplies it, and keep the context-based fallback for SQLite; or branch per dialect explicitly instead of matching substrings |
| 13 | No retention or deletion of personal data (§11.3) | No policy has been agreed, and there is no production data yet | A retention window and a deletion path, on the indexed `created_at` |
| 14 | `last_error_message` stores up to 500 characters of upstream-provided text (§11.3) | It is the only diagnostic available for a `PENDING` lead, and the adapter avoids copying the body wholesale | A whitelist of upstream error codes mapped to local messages |
| 15 | The static-site CSP allows `'unsafe-inline'` and third-party analytics domains, while the API's CSP is default-deny (§5.1) | The analytics layer is dormant and its domains are pre-declared so enabling it is a one-line change | Tighten the nginx CSP when the vendor scripts are actually enabled, or enable them with a nonce/hash instead of `'unsafe-inline'` |
| 16 | **A process killed between the INSERT and the terminal update leaves a phone claimed** until `PHONE_CLAIM_TTL_SECONDS` elapses; during that window the customer is told `REGISTRATION_IN_PROGRESS` — see `docs/REGISTRATION-FLOW.md` §3.4. The TTL is a tradeoff: too short and a slow provider lets two attempts through; too long and a crash locks a phone out for longer. It must exceed `KHAIBAO9610_TIMEOUT_SECONDS` | Both alternatives are worse. Without the claim the provider is called once per concurrent attempt, and a duplicate customer created upstream is a thing nothing on this side records or can explain. Without a TTL the abandoned claim blocks the phone **forever**. The window is bounded by configuration rather than by an operator acting: the TTL floors at 30 s against a 10 s provider timeout (`config.py:113`, `:84`), and reclamation runs before every INSERT (`services/registration.py:135-139`), so the exposure is one transient refusal — the lead row itself is already committed (`docs/REGISTRATION-FLOW.md` §5). **Re-read against the `0005` predicate and unchanged by it:** the single rule also covers registered rows, which narrows when a claim can be *taken*, and says nothing about a claim nobody will *release*. **Corrected since the previous revision:** the "6 calls became 1" measurement this row used to cite is **withdrawn** — it was timing-dependent, CI caught the window it hid, and the replacement is deterministic (`backend/tests/test_repository.py:335`). The TTL path is also no longer untested: five tests cover `release_stale_claims` (`test_repository.py:264`, `:281`, `:297`, `:304`, `:361`), and writing them found a real `TypeError` on SQLite that appeared only when a row actually held a claim (`sqlalchemy_repo.py:74-88`) | Make the reservation reapable on death rather than aged out — a PostgreSQL session-scoped advisory lock, or a lease with a heartbeat, either of which releases the moment the owner dies instead of after a fixed interval; failing that, a per-attempt owner id and a scheduled reaper instead of an inline age test. Either way it still needs the test this risk has none of: kill a process mid-attempt and assert the phone is refused until the TTL and accepted after it |

Risks 1–2 are recorded because they are the honest remainder of a control that is
now correct; risks 3 and 5 are the two places where the shipped process
configuration differs from the assumption the application code was written under.
Risk 10 is no longer the status/body contradiction it once was — that was fixed
in `4d026def` (PR #13) and the history is kept in `docs/REGISTRATION-FLOW.md`
§8.3; what remains is the derived fallback for pre-migration rows.

**Risk 16 is new in `f626644` and is the price of the phone claim.** It is listed
rather than accepted silently because it is the one place where this service
deliberately refuses a customer who has done nothing wrong: a crash can hand a
customer `REGISTRATION_IN_PROGRESS` for up to the TTL, and the refusal is truthful
about *state* but not about *cause*. The alternative — no claim — was measured,
and it was worse. Risk 12 grew with the same revision: the claim added two more
driver-text matchers, and the SQLite caveat in `sqlalchemy_repo.py:232-240` is now
the clearest statement in the codebase of why that matching is fragile.

---

## 13. Corrections to the previous revision of this document

The previous version documented `fcecb00`. Three of its claims are now wrong or
stale and are corrected above. They are listed here rather than silently removed,
because a reader of the earlier version needs to know which parts changed.

| Previous claim | Status now | Why it changed |
|---|---|---|
| "⚠️ Known gap: tracebacks are not scrubbed at the record level. `PasswordRedactionFilter` and `scrub_record()` never inspect `record.exc_info`… any handler that does not use `RedactingFormatter` writes the password out in clear" | **FIXED** — §2.3 now documents the fix, the seven re-run scenarios, and keeps a short "how this was wrong before" note | `scrub_record()` now renders `exc_info` through `scrub_text` into `exc_text` and sets `exc_info = None` (`logging_filters.py:185-188`), so redaction happens on the record and handler order no longer matters. The false docstring claim was also replaced (`logging_filters.py:6-27`). |
| "with `TRUST_PROXY_HEADERS=no` … the rate limiter will key every request on the proxy's own address, collapsing all clients into one bucket — and HSTS will not be emitted even over HTTPS" | **WRONG** — corrected in §5.3 | It reasoned about the application's flag without accounting for uvicorn's `--proxy-headers`, which rewrites `scope["client"]` and `scope["scheme"]` before the app sees the request. Measured against a real uvicorn: the limiter keys on the forwarded client IP and HSTS *is* emitted. |
| "**There is no nginx configuration in this revision.** `deploy/` does not exist … Whatever headers a production reverse proxy sends are therefore not verifiable from `fcecb00`" | **STALE** — §5 documents the nginx tier as current truth | `deploy/nginx/*` and `tools/check_nginx_config.py` now exist, the checker runs in CI, and the 0/6 vs 6/6 measurement was independently reproduced here rather than transcribed. |

Two further updates are not corrections: the API-doc surface is now gated off in
production (§1.4), and the deployment was changed to a single uvicorn worker with
an explicit `--forwarded-allow-ips`, which is what §3.3 and §5.3 now describe. A
third item *is* a correction of my own error: the previous version claimed nginx
did not rate-limit the API path. It does, and had already done so at the revision
the claim was written against — §10.3 now documents both zones, and §12 records it
as risk 6.

**Corrections made when this document was updated for `f626644`** (the phone
claim). These are listed separately because they belong to a different revision
than the three above, and one of them was already wrong before it — but it
contradicted the new mechanism directly, so leaving it would have made this
document argue against the code it describes.

| Previous claim | Status now | Why it changed |
|---|---|---|
| "**No PostgreSQL run.** Everything was exercised on SQLite under pytest's `tmp_path` … The partial-index race, the `IntegrityError` text matching (risk 12) and every CHECK constraint have been verified on SQLite only" (§14, item 2) | **partly STALE** — corrected in item 2, and risk 12 updated | The `postgres:16` CI job landed in `7cfbde2` (PR #19) and the concurrency tests in `72d991d` (PR #23), both *after* the `4d026def` this document was written against; `f626644` then built the phone claim on top of exactly those tests. The partial-index race and the phone-claim guarantee cannot be measured on SQLite at all (`test_concurrency.py:19-22`), so the old sentence now understates the coverage while the new risk 16 needs it stated correctly. **Still true:** no PostgreSQL server was started while updating this document, so nothing here is a run I made. |
| "The `leads` table … stores: name … attempt count, last error code and message, and two timestamps" (§11.1) | **incomplete, not wrong** — extended in place | `f626644` added `in_flight_at` (`models.py:176`). §11.1 answers "what does the table store" exhaustively, so the new column is named there with its purpose, and with the judgement that it carries no personal data of its own. |

Risk 16 is the only *new* residual recorded for this revision, and it is a
consequence of the claim rather than a defect found in it; §12 says why it is
accepted instead of fixed.

---

## 14. What is not verified

1. **No penetration test, no security review, no threat model** was performed for
   this document. It is a reading of the code and of commands whose output is
   quoted inline. No vulnerability scanner was run against a live system.
2. **No PostgreSQL run *here*.** The suite's baseline is SQLite under pytest's
   `tmp_path` (`backend/tests/conftest.py:5-9`, `:111`), and that is where every
   CHECK constraint and the `IntegrityError` text matching of risk 12 were
   verified. It is no longer the whole story for the phone claim, whose tests
   *cannot* run on SQLite: `backend/tests/test_concurrency.py` is gated on
   `TEST_DATABASE_URL` (`:19-22`, `:39-42`) because "SQLite's locking would make
   the result an artefact of the test harness", and CI runs it, together with
   `test_postgres.py`, against a `postgres:16` service
   (`.github/workflows/ci.yml:132-151`, `:163-171`). No PostgreSQL server was
   started while updating this document, so the mechanism in risk 16 still rests on
   the revision's own CI runs rather than on a PostgreSQL run made here. **What
   this revision did run:** `python -m pytest -q` on SQLite reported `378 passed,
   12 skipped` — the skips being exactly the PostgreSQL-gated tests — and the six
   claim and reclaim tests in `test_repository.py` were run directly and pass. See
   §13.
3. **No live provider call was ever made** (§10.3). The upstream contract, its
   duplicate phrasing, and its error bodies are unverified.
4. **nginx was run locally but never against the production host.** The 0/6 vs
   6/6 measurement in §5.2 used a minimal synthetic `server` block, not
   `deploy/nginx/viporder.com.vn.conf` as a whole — the real config references
   `/etc/letsencrypt/live/...` certificates and a `/srv/viporder/site` root that
   do not exist here, so it cannot be started as-is. TLS, OCSP stapling, HTTP/2 and
   the `/api/` proxy path against a running backend are unverified
   (`deploy/nginx/README.md:92-94`).
5. **No deployment was exercised**, so nothing here is verified under real network
   conditions, real TLS, or a live worker model. The single-worker guarantee
   (risk 3) is a reading of `deploy/systemd/viporder-web.service:51` together with
   the in-process data structure — it was **not** measured by running the unit and
   counting rejections.
6. **The §2.3 redaction results came from direct `logging` probes, not from a
   leak through the running application.** Seven isolated processes each install a
   plain-formatter handler, log a record whose exception message contains the
   secret, and read the handler's output. That demonstrates the control's
   behaviour; it does not demonstrate an exploitable path in the current code, and
   §2.3 says so.
7. **The browser was never exercised.** No browser loaded `index.html` in the
   course of this work; no cookie behaviour, no CORS preflight, no CSP violation,
   no clickjacking defence and no `register.js` execution was observed in a real
   user agent. The client-side claims in §6, §7 and §11 are readings of
   `static/js/register.js`.
8. **The forwarded-header measurement (§5.3) used one uvicorn process started
   manually**, not the systemd unit, and the third request in probe B hit the
   duplicate-phone rule rather than completing a second registration. It shows the
   limiter keyed on the forwarded IP; it does not show a full two-worker run.
9. **The 328-test suite passes at this revision.** `python -m pytest -q` reported
   `328 passed, 1 warning` on each of the runs made while preparing this document
   (wall time varied: 4.79 s, 4.86 s), run
   with a venv carrying the pinned versions from
   `backend/requirements-dev.txt`. A passing suite is evidence that the described
   mechanisms behave as described under test; it is not a security status and this
   document does not treat it as one.
