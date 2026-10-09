# KHAIBAO9610 integration — registration and tracking

> ## Status: endpoints OWNER-SUPPLIED since 2026-10-02 · success contract MEASURED 2026-10-09 — registration still `BLOCKED_EXTERNAL`
>
> On **2026-10-02 the owner supplied the production endpoint information**, and
> the implementation landed on `feat/g04b-integration`. The base URL and the
> three paths are no longer inferences from a public bundle: they are
> **owner-supplied and authoritative**. What changed, and why the same URL now
> has a different status, is recorded in §4.6.
>
> **The single most likely production bug: the two tracking endpoints return
> different envelopes.** `GET /warehouse-imports/{keyword}` answers with a **bare
> object**; `GET /package-sealings/{keyword}` answers with **`{"data": {...}}`**.
> A parser that assumes one shape fails on the other. Both shapes, both failure
> bodies, and the one place that unwraps are in §4.7.
>
> What the supply does **not** settle:
>
> * **TWO live registration `POST`s have been made, both owner-authorised, one
>   each.** The first — **2026-10-07 05:53:38 UTC**, through our application
>   (§10.6) — lost the provider's raw body and predates PR #63 by 7m45s, so it is
>   stale evidence about our code. The second — **2026-10-09 16:27:24 UTC**,
>   posted **directly** to the provider by `tools/test_live_registration.py`
>   (§10.8) — kept its evidence, and it answers the register schema: **200**, two
>   keys, **no customer code, no id, no token**. A customer code **has** now been
>   observed, but **not** in the register response: it is **`customer.code`**, read
>   back through one `POST /login` and one `GET /auth/profile` (measured value
>   `TT5233`, `customer.id` `6109`).
> * **OWNER DECISION 2026-10-09 — option B.** The backend does **not** read the code
>   back. The measured success body (`200`, `status == "success"`) is now reported
>   as `SUCCESS` with **no code**: the lead becomes `REGISTERED` with
>   `external_customer_code = NULL`, and the customer is told to sign in to the
>   portal to see the code — exactly what the provider's own portal does. Any other
>   2xx that identifies nobody is still `UNUSABLE_RESPONSE` (§10.8, "option B").
>   A third live `POST` needs new owner authorisation.
>   `KHAIBAO9610_MODE=mock` is still the shipped default in every configuration
>   file (`backend/app/config.py:107`, `deploy/env.production.example:80`,
>   `.env.example:33`). The live adapter refuses to exist unless the mode is `http`
>   **and at least one** of the two capability switches is set
>   (`backend/app/providers/khaibao9610.py:183-190`).
> * **Five provider facts remain UNKNOWN** — the duplicate and validation response
>   schemas, the rate limit, the provider's expected timeouts, and whether
>   production requires authentication or an IP allowlist. They are listed in §4.9
>   (where item 1 is now answered), and every corresponding parser is defensive
>   because of them.
> * **Live tracking CAN now be switched on by itself**, because the read and
>   write switches are separate: `KHAIBAO9610_MODE=http` with
>   `KHAIBAO9610_ENABLE_REAL_CALLS=yes` and
>   `KHAIBAO9610_ENABLE_REAL_REGISTRATION=no` reaches the two lookups and refuses
>   `register()` at the call site (§7). This sentence said the opposite until the
>   two capabilities were split, and it is corrected rather than deleted: the
>   shared switch is exactly what let a tracking test create a real account.
>
> `https://apiviporder.com/frontend/v1` is **owner-supplied and authoritative**
> as of 2026-10-02 (`backend/app/config.py:39,108`;
> `deploy/env.production.example:88`). It was previously labelled "an
> observation, not a contract" because it had been read out of a public frontend
> bundle during reconnaissance (§4.2). The owner has now supplied it directly as
> the production frontend API base. The value did not change; the authority
> behind it did, and that is the whole of the change.
>
> The site may be released and used, but real registration must still be
> described as MOCK — never as working — until the checklist in §6 is answered
> and §9's steps are done with real evidence (§11).
>
> Tracking issue: **#4 — [KHAIBAO9610] Provide production registration API contract**

---

## 1. Why this boundary exists

The customer-code authority is an external system that VIPORDER does not
control. Its **endpoints** are now known — the owner supplied them on 2026-10-02
(§4.6) — but its **responses, limits and authentication requirements are not**,
and it may reject or duplicate customers in ways nobody has documented (§4.9).

Three rules follow, and they shape the whole design:

1. **A lead is never lost because the provider was unavailable.** The lead is
   persisted *before* the provider is called.
2. **The provider is replaceable without touching business logic.** All provider
   specifics live behind one interface.
3. **An unverified external call never happens by accident.** Two independent
   switches must both be set.

---

## 2. The sequence

```
browser
  └─ POST /api/v1/registrations
       ├─ validate (server-side)
       ├─ normalise phone
       ├─ persist lead ................ registration_status = PENDING
       ├─ call RegistrationProvider.register()
       │     ├─ SUCCESS     → REGISTERED + external_customer_id/code
       │     ├─ DUPLICATE   → FAILED, reason DUPLICATE (no code returned to caller)
       │     ├─ INVALID     → FAILED, reason INVALID (provider rejected the data)
       │     └─ UNAVAILABLE → stays PENDING (retryable), lead retained
       └─ respond
            ├─ 201 REGISTERED
            ├─ 202 PENDING   (+ tracking_token)
            ├─ 409 duplicate
            └─ 422 validation
```

The critical property is the **last** branch: if the provider times out, throws
or returns 5xx, the row already exists. A human can retry it later via
`POST /api/v1/admin/registrations/{lead_id}/retry` rather than the customer
having to register again.

### The tracking lookups

Two public lookups (no sign-in), keyword in the path, sharing one response
contract (`backend/app/routers/tracking.py:6-12`):

```
browser
  └─ GET /api/tracking/warehouse-imports/{keyword}
     (or /api/tracking/package-sealings/{keyword})
       ├─ sanitise_keyword() — regex only, NO network call on a bad keyword
       ├─ resolve the provider's finder with getattr (there is none in mock mode)
       ├─ provider GETs https://apiviporder.com/frontend/v1/...
       └─ respond
            ├─ 200 {"result": "found", "search_type": …, "data": {…}}
            ├─ 400 INVALID_KEYWORD
            ├─ 404 NOT_FOUND           (provider answered 404)
            ├─ 502 PROVIDER_ERROR      (provider answered unusably)
            └─ 503 PROVIDER_UNAVAILABLE (no provider / timeout / 429)
```

Measured against the merged local backend with the **mock** provider — the
shipped default — so these are observed, not inferred:

* a hostile keyword → **400 `INVALID_KEYWORD`**, Vietnamese, **no provider call**
  (`backend/app/tracking.py:115-135`, reached before any network use at
  `backend/app/routers/tracking.py:141-148`);
* an empty keyword → **400 `INVALID_KEYWORD`**;
* a valid keyword → **503 `PROVIDER_UNAVAILABLE`** ("chức năng tra cứu chưa được
  bật"), because the mock provider implements no tracking method at all
  (`backend/app/providers/factory.py:30-31`;
  `backend/app/routers/tracking.py:150-158`; pinned by
  `backend/tests/test_tracking.py:849-862`).

The single sentence that matters for the 200 path: the two upstream endpoints do
not agree on their envelope, and §4.7 is where that is spelled out.

---

## 3. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `KHAIBAO9610_MODE` | `mock` | `mock` or `http` (`backend/app/config.py:107`) |
| `KHAIBAO9610_ENABLE_REAL_CALLS` | `no` | **Second switch.** Real calls need this *and* `MODE=http` (`backend/app/config.py:110`) |

> ### The two switches are now THREE capabilities, and one of them is writes
>
> **Corrected 2026-10-02.** `KHAIBAO9610_ENABLE_REAL_CALLS=yes` used to permit
> **both** tracking lookups **and** customer registration. That made the permitted
> action (live read-only GETs) and the forbidden one (an uncontrolled registration
> POST) *the same action* — and during staging acceptance it produced exactly that:
> an unauthorised `POST /register` against the provider's production API, which
> created a customer account.
>
> Registration writes now need their **own** switch:
>
> | Capability | Switch |
> |---|---|
> | tracking lookups (reads) | `KHAIBAO9610_ENABLE_REAL_CALLS=yes` |
> | customer registration (writes) | **`KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes`** |
>
> `KHAIBAO9610_MODE=http` is still required for either. The write switch defaults to
> **off** and setting the read switch does **not** set it.
>
> `/api/v1/health` reports both as `checks.provider.capabilities`, so an operator can
> see whether customer creation is live without reading the environment file.

| `KHAIBAO9610_BASE_URL` | `https://apiviporder.com/frontend/v1` | Base URL of the frontend API. **Owner-supplied and authoritative** since 2026-10-02 (`backend/app/config.py:39,108`; §4.6) |
| `KHAIBAO9610_REGISTER_PATH` | `/register` | Registration path template (`backend/app/config.py:51,114`) |
| `KHAIBAO9610_WAREHOUSE_IMPORT_PATH` | `/warehouse-imports/{keyword}` | Warehouse-import lookup; **bare-object** envelope (`backend/app/config.py:52,115`) |
| `KHAIBAO9610_PACKAGE_SEALING_PATH` | `/package-sealings/{keyword}` | Package-sealing lookup; **wrapped** envelope (`backend/app/config.py:53,116`) |
| `KHAIBAO9610_TIMEOUT_SECONDS` | `10` | Hard per-call timeout, clamped to 1–30 s (`backend/app/config.py:109`; `backend/app/providers/khaibao9610.py:117-122`) |
| `KHAIBAO9610_USER_AGENT` | browser-like | Required by the upstream behind Cloudflare (`backend/app/config.py:31-36,111`) |
| `KHAIBAO9610_MAX_RESPONSE_BYTES` | `2097152` | Cap on a tracking response body (`backend/app/config.py:59,117-119`) |
| `MOCK_PROVIDER_BEHAVIOUR` | `success` | `success`/`duplicate`/`invalid`/`unavailable`/`timeout`/`error` (`backend/app/config.py:122-124`) |

The three path templates are validated rather than trusted: each must start with
`/`, and the two tracking templates must contain **exactly one** `{keyword}`
(`backend/app/config.py:171-206`). Without that, a relative path concatenates
onto the base URL and produces a 404 an operator would read as "the tracking code
does not exist" (`backend/app/config.py:178-185`) — two very different things to
tell a customer. The provider re-checks the same rules at construction, because
it is also built directly in tests (`backend/app/providers/khaibao9610.py:130-152`).

The tracking lookups are **not** separately switchable: they are methods on the
live adapter, so the same two keys above are what arm them (§7).

Why two switches: a single `MODE=http` can be set by a typo, a copied config or
a stale `.env` on a server nobody is watching. Requiring a second, deliberately
named flag makes "we started sending real customer data to a provider endpoint"
a thing someone had to *mean*. The guard itself is described in §7.

---

## 4. What is known, and how strongly

Everything in this section comes from the owner's own prior integration work,
not from documentation. It is labelled by confidence, and **none of it has been
verified against the live system from this repository.**

### 4.1 Where the earlier beliefs came from

> Superseded by §4.2 for everything the public client can settle. Kept because
> this is the source of the *login-as-the-customer* idea, and knowing where a
> belief came from is how you tell later whether it still applies.

Observed in the owner's own prior automation:

Source: a customer-code flow the owner runs today. Every row is an observation
from that automation — **not a contract**, and not confirmed by the provider:

| Aspect | Observed |
|---|---|
| Register | `POST {base}/register` |
| Base | `https://apiviporder.com/frontend/v1` (**observation, not a contract**) |
| Request body | `{name, phone, email, password, confirmPassword, acceptTerms}` |
| Login | `POST {base}/login` with `{account, password}` |
| Profile | `GET {base}/auth/profile` with `Authorization: Bearer <token>` |
| Token shape | `access_token` / `token` / `data.access_token` / `data.token` |
| Customer code | matches `TT\d+`, read from the profile response |
| Duplicate signal | HTTP **422** whose message matches `ton tai\|already\|taken\|da duoc su dung\|da dang ky\|da co\|exist` (accent-stripped, lowercased) |
| Transport gotcha | Cloudflare **Error 1010** rejects non-browser clients → a real browser `User-Agent` is required |
| Separate admin API | `https://apiviporder.com/api/v1` (Laravel + JWT), used with a read-only account (**observation, not a contract**) |

### 4.2 Read from the public client bundle — 2026-10-01 (observation, not a contract)

On 2026-10-01 the public customer portal's own JavaScript bundle was fetched and
read. This is the code the real registration form runs, so it is a **measurement
of the shipped client**, not documentation and not a guess.

It is also **not the provider's contract.** It tells us what one public client
sends and expects. It cannot tell us what the provider requires, permits or
returns to a server-to-server caller — which is what §6 asks for. Treat every
row below as an observation with that limit.

**Status update, 2026-10-02.** The base URL below is no longer *only* an
observation of this bundle: the owner has since supplied the same value as
authoritative (§4.6). This section is kept as the record of *what the public
bundle said on 2026-10-01*, which is what it was for and still is. Where the two
sources would ever disagree, §4.6 wins — a bundle is evidence about a client, an
owner's statement is evidence about the API.

Method (read-only; no account was created and no POST was ever sent):

```
GET https://khachhang.viporder.com.vn/                      -> 200, 3,061 bytes
GET https://khachhang.viporder.com.vn/assets/index.e00575c6.js -> 200, 2,091,266 bytes
```

**Read verbatim from the bundle** — an observation of a public client, not the
provider's contract:

| Fact | Value |
|---|---|
| API base | `baseURL: "https://apiviporder.com/frontend/v1/"` (**observation, not a contract**) |
| Registration call | `await en.post("register", payload)` |
| Endpoint this client calls | `POST https://apiviporder.com/frontend/v1/register` (**observation, not a contract**) |
| Request body | exactly `{ name, phone, email, password, confirmPassword, acceptTerms }` |
| Login | `en.post("login", ...)` |
| Profile | `en.get("auth/profile")` |
| Other client calls | `auth/logout`, `auth/profile-update`, `password/email`, `password/reset`, `/customers`, `/warehouse-imports`, `/package-sealings`, `/dashboard/*` |

Client-side rules the portal itself enforces (useful for matching validation,
not for trusting it):

* `password !== confirmPassword` → *"Mật khẩu xác nhận không khớp!"*
* phone must match `/^[0-9]{10,11}$/` → *"Số điện thoại không hợp lệ!"*
* password strength: length ≥ 8, lower + upper, digit, symbol → weak/medium/strong

### 4.3 The blocking question — what the public client settles

The register call site, verbatim (identifiers shortened):

```js
const { data: u } = await en.post("register", l);
ii.success(u.message || "Đăng ký thành công! Vui lòng đăng nhập.");
t.push("/login");
```

**The client reads only `u.message` from the registration response and then
redirects to `/login`.** It never reads a customer code — and `customer_code` /
`customerCode` appear **zero** times anywhere in the bundle.

So, **for this one public client**: the registration call it makes does not hand
back the customer code. The code is obtained only after authenticating, from the
profile/customer endpoints. This is evidence about the client. It is not proof
about the API — a server-side field the client ignores remains possible, which
is why §6 still asks (items 9 and 10).

This changes the question from "we don't know" to a definite constraint:

> A public site on `viporder.com.vn` that collects the customer's **own**
> password cannot then authenticate as that customer to read their code. The
> register endpoint alone is therefore **not sufficient** to complete PATH A.

The three options, and the decision is the owner's:

1. **The provider adds (or reveals) an endpoint that returns the code from the
   registration call itself.** Cleanest, and it is the only option that unblocks
   real integration through the provider. Answering items 9 and 10 in §6 settles
   whether this already exists.
2. **VIPORDER registers the customer, then the customer logs in at
   `khachhang.viporder.com.vn` and reads their own code there.** No credential
   handling on our side. The new site's success screen would then say "your account
   is created — sign in to see your customer code", which is truthful and needs
   nothing new from the provider.
3. **The site logs in as the customer**, which requires storing or minting a
   password. **Rejected on this project's own rules**: it would put a credential
   that unlocks a third-party account into our lead store, and a shared default
   password on a public form would expose every customer to anyone who knows a
   phone number.

**Option 2 is available today and needs nothing external.** It was not obvious
before this measurement, because the client code had not been read.

### 4.4 Still unknown — genuinely provider-only

Everything below cannot be obtained by reading a public client, because it is
server-side behaviour or a business decision. See §6, and §4.9 for the same list
after the 2026-10-02 supply — **the endpoints are no longer in it**, but
everything about what comes back still is.

* whether the register **response** carries the code in a field this client
  simply ignores (possible — the client only reads `message`); this is the one
  fact that would move option 1 from "ask" to "verify"
* duplicate-customer response shape
* validation-failure response shape
* rate limits
* timeout and retry safety (is the call idempotent?)
* whether a staging endpoint and credentials exist
* the **consent basis**: is VIPORDER permitted to register customers on their
  behalf from `viporder.com.vn`?
* IP allow-listing requirements

### 4.5 A note on how §4.3 was previously described

An earlier revision of this document called the login-as-the-customer flow *"the
observed flow"*. It was observed in the owner's own prior automation, but it is
**not** what the customer portal does — the portal redirects to login and lets
the customer see their own code. The distinction matters, because it changes the
recommendation: the portal's own behaviour (option 2) is both safe and available
now, whereas copying the automation's behaviour would import a credential risk
the portal itself does not have.

### 4.6 Owner-supplied — 2026-10-02 (AUTHORITATIVE)

On 2026-10-02 the owner supplied the production endpoint information directly.
This is the first time the endpoint shape has come from the party that owns it
rather than from reconnaissance, so these values are **authoritative**, not
observations. They answer §6's items 1, 2 and 5, and they include two tracking
endpoints the original 20-item checklist never asked about (§6 items 21–22). The
history of the questions is kept in §6 rather than deleted.

| Supplied fact | Value | Where it lives in the code |
|---|---|---|
| Base URL | `https://apiviporder.com/frontend/v1` | `backend/app/config.py:39,108`; `deploy/env.production.example:88` |
| Registration | `POST {base}/register` | `backend/app/config.py:51,114`; `backend/app/providers/khaibao9610.py:356,375-380` |
| Warehouse lookup | `GET {base}/warehouse-imports/{keyword}` | `backend/app/config.py:52,115`; `backend/app/providers/khaibao9610.py:339-344` |
| Package lookup | `GET {base}/package-sealings/{keyword}` | `backend/app/config.py:53,116`; `backend/app/providers/khaibao9610.py:346-352` |
| Registration request fields | `name, phone, email, password, confirmPassword, acceptTerms` | `backend/app/providers/khaibao9610.py:365-372` |

**Status change, stated explicitly.** Before 2026-10-02 this document said *"no
file in this repository states an authoritative endpoint"* and labelled
`https://apiviporder.com/frontend/v1` as *"an observation, not a contract"*
because it had been read from a public frontend bundle (§4.2). The owner has now
supplied it as the production frontend API base. The string is unchanged; the
source of authority changed, and that is why the label changed with it. An
observation of a public client told us what one browser sends; an owner-supplied
endpoint tells us what the provider's API is.

Two limits on the supply, both important:

* It settles **where** to call and **what field names to send**. It does **not**
  settle what comes back — see §4.9 for what is still unknown.
* The field list is the request contract's *names*. Which of those fields the
  provider treats as required, and what it does with an absent or `null` one, was
  not supplied (§6 item 5).

### 4.7 The two upstream envelopes differ (MEASURED, 2026-10-02)

This is the most important measured fact in this document, because a parser that
assumes one shape fails on the other, and it fails by returning a body the code
then reads as all-null rather than by raising.

Measured by read-only `GET` against `https://apiviporder.com/frontend/v1` on
2026-10-02:

| Endpoint | Success body | Failure body |
|---|---|---|
| `GET /warehouse-imports/{keyword}` | a **BARE object** — no wrapper key | HTTP **404** `{"error":"Mã vận đơn không tồn tại"}` |
| `GET /package-sealings/{keyword}` | **`{"data": { ... }}`** — WRAPPED | HTTP **404** `{"error":"Mã đóng bao không tồn tại hoặc bạn không có quyền truy cập"}` |

Supporting measurements of the same moment:

* **The working keyword is the FULL tracking code.** `KY4001103376087` → 404;
  `KY4001103376087-2-4-|s` → 200. The pipe is part of a real code, so it is in
  the accepted character set (`backend/app/tracking.py:85-99`) and is
  percent-encoded on the wire (`backend/app/providers/khaibao9610.py:228-234`;
  measured end-to-end URL at `backend/tests/test_tracking.py:727-736`).
* **Keyword search needs no authentication — OBSERVED.** `GET /warehouse-imports`
  (the bare list) returned **401 `{"error":"Unauthenticated."}`** while the
  keyword form returned 200 with no credentials
  (`backend/app/tracking.py:20-25`). Observed at measurement time, not a
  guarantee: a 401 from the keyword endpoint is treated as a contract change
  (502), never as "the code does not exist"
  (`backend/app/providers/khaibao9610.py:246-255`).
* **Hostile keywords all 404'd upstream** without reaching anything — traversal,
  NUL, encoded script, SQL metacharacters, 300 characters
  (`backend/app/tracking.py:27-30`). We still validate on our side: a third
  party's input handling is not a control we own.

**Where the asymmetry is handled, and where it is deliberately not.**

* The provider returns the **RAW parsed body** from both lookups and unwraps
  nothing: `find_warehouse_import` (`backend/app/providers/khaibao9610.py:339-344`)
  and `find_package_sealing` (`:346-352`) both delegate to one `_tracking_get`
  (`:209-280`), and the module says why in as many words at `:329-337` —
  *"Unwrapping here would mean guessing which shape arrived, and guessing is how
  the two get conflated."*
* Unwrapping happens in exactly one place, `backend/app/tracking.py`, and only
  for package-sealings: `normalize_package_sealing` reads `envelope["data"]`
  (`backend/app/tracking.py:281`) while `normalize_warehouse_import` reads the
  bare object directly (`backend/app/tracking.py:245`).
* Both projections are whitelists that tolerate any shape, so the *wrong*
  envelope does not raise — it produces nulls
  (`backend/app/tracking.py:66-72,231-233`;
  `backend/tests/test_tracking.py:307-312`).
* The route never inspects the body's shape
  (`backend/app/routers/tracking.py:17-19,173-182`).

The two measured bodies are kept verbatim as test fixtures — including the fields
we deliberately drop — at `backend/tests/test_tracking.py:45-129`, and the
projection's key list is pinned against them at
`backend/tests/test_tracking.py:425-441`.

### 4.8 Wire names vs provider names — the bug this already caused

There are **two different naming schemes** in this one flow, and the gap between
them is not cosmetic: it has already produced a real defect.

| Meaning | Our API (snake_case) | Form control `name` | Provider contract (camelCase) |
|---|---|---|---|
| Customer name | `full_name` (`backend/app/schemas.py:78`) | `full_name` (`index.html:605`) | `name` (`backend/app/providers/khaibao9610.py:366`) |
| Password confirmation | `confirm_password` (`backend/app/schemas.py:93`) | `confirmPassword` (`index.html:656`) | `confirmPassword` (`backend/app/providers/khaibao9610.py:370`) |
| Terms acceptance | `accept_terms` (`backend/app/schemas.py:120`) | `acceptTerms` (`index.html:701`) | `acceptTerms` (`backend/app/providers/khaibao9610.py:371`) |

**The defect, as it happened.** The registration form's `name` attributes for two
of these three fields are the *provider's* spelling, not ours. A client that sent
the form's own names therefore sent `confirmPassword` and `acceptTerms` to
`POST /api/v1/registrations`, whose schema declares `confirm_password` and
`accept_terms` and whose `extra="ignore"` **silently drops any key it does not
recognise** (`backend/app/schemas.py:76`). Both keys vanished; both required
fields arrived missing (`backend/app/schemas.py:93,120`); every live registration
would have answered **422** while looking correct on both sides. The `full_name`
control is the reverse trap — it matches *our* API, while the provider expects
`name` — so a reader who concluded "the form names are the provider names" would
be wrong in the other direction as well.

It was fixed at the source (commit `cd7f6b6`):

* the client sends **our** snake_case names — `confirm_password` and
  `accept_terms` at `static/js/register.js:648-649`, built in `buildPayload()`
  at `static/js/register.js:628-661`;
* the adapter renames them to the provider's camelCase, which is where that
  translation belongs — `backend/app/providers/khaibao9610.py:365-372`;
* `FIELD_ALIASES` maps a server-side `422` back to the right DOM control, so a
  naming error surfaces on the field instead of nowhere
  (`static/js/register.js:129-141`).

The form still uses `confirmPassword`/`acceptTerms` as its `name` attributes
because those are the controls it collects; the controller reads them by DOM name
(`static/js/register.js:257-263`) and emits the API names. **One translation
point, at the boundary, is the rule.**

**A second document is stale on exactly this point.**
`docs/REGISTRATION-FLOW.md` §2.1/§2.2 still tables the pre-G04B form — it has no
`confirmPassword` control, no `acceptTerms` control and no email input — and its
line anchors were written against an earlier revision, which its own §9 warns
about. Measured example: its `index.html:473` anchor for the `full_name` control
now lands in marketing copy, while the control is at `index.html:605`. For the
current request shape, read this section, not that one.

### 4.9 Still UNKNOWN after 2026-10-02

The owner supplied endpoints and field names. Everything below is **still
unknown**, is asserted nowhere in this document, and is the reason the code parses
defensively rather than optimistically. Each item says which guard exists
*because* the answer is missing.

1. **Registration success response — exact schema. MEASURED 2026-10-09, see
   §10.8.** The provider answers `POST {base}/register` with **HTTP 200**,
   `application/json`, **96 bytes** and exactly two keys —
   `{"status": "success", "message": "Đăng ký tài khoản thành công"}`. There is
   **no customer code, no customer id and no token** in it. The adapter still
   treats any 2xx as reaching the provider
   (`backend/app/providers/khaibao9610.py:415-424`) and still finds no identifier
   in that body, which is why such a registration is classified
   `UNUSABLE_RESPONSE` and the lead stays `PENDING`. What remains unknown is not
   the schema but the *non-success* schemas — items 2 and 3 below.
2. **Duplicate-registration response — exact status and body. STILL UNKNOWN.**
   The adapter recognises a duplicate only on HTTP **409 or 422** *and* a body
   that accent-insensitively matches a phrase list
   (`backend/app/providers/khaibao9610.py:63-66,426-432`). Any other status or
   wording falls through to `INVALID`. Defensive by construction: it matches a
   pattern instead of asserting a status.
3. **Validation-error response — exact status and body. STILL UNKNOWN.** Every
   non-2xx that is not a recognised duplicate, not `429` and below `500` becomes
   the one generic `INVALID` (`backend/app/providers/khaibao9610.py:446-452`),
   and the upstream body is read only for the duplicate phrase match — never
   parsed for field detail (`:410-413,426`). Defensive in the sense that it
   cannot crash on an unknown body; unhelpful in the sense that field detail is
   discarded.
4. **Rate limit. STILL UNKNOWN.** No requests/second, burst, `Retry-After` or
   escalation figure has been supplied. The adapter maps `429` to a retryable
   `UNAVAILABLE` (`backend/app/providers/khaibao9610.py:242-245,434-444`) without
   honouring any header, because none was supplied to honour.
5. **Provider timeout expectations. STILL UNKNOWN.** Their normal and worst-case
   latency, and the timeout they recommend, were not supplied. Our 10 s is our
   own default, clamped 1–30 s (`backend/app/config.py:109`;
   `backend/app/providers/khaibao9610.py:117-122`), and the timeout becomes a
   read/connect pair at call time (`backend/app/providers/khaibao9610.py:197-207`).
6. **Whether production requires authentication or an IP allowlist. STILL
   UNKNOWN.** The keyword GETs were observed to need no credentials (§4.7), but
   that is an observation of a *read* at one moment, not an answer about
   production registration. There is **no provider credential setting and no
   allowlist setting** anywhere in `backend/app/config.py` or in the
   `KHAIBAO9610` block of `deploy/env.production.example:76-106`, so a required
   one could not be configured today without a code change. The adapter sends no
   credential (`backend/app/providers/khaibao9610.py:182-195`), which is correct
   only if the answer turns out to be "none".

Until each of these is answered, every corresponding parser stays defensive, and
no success shape is written down as though it were known.

---

## 5. The adapter surface, as shipped

`backend/app/providers/khaibao9610.py` holds one live adapter,
`ViporderFrontendProvider` (`:86`), and it now carries both halves of the
integration. The three public methods are:

| Method | Purpose | Measured/known contract | Anchor |
|---|---|---|---|
| `register(request)` | Create a customer account | `POST {base}/register`; body `{name, phone, email, password, confirmPassword, acceptTerms}`; response schema still UNKNOWN (§4.9) | `backend/app/providers/khaibao9610.py:356-452` |
| `find_warehouse_import(keyword)` | Look up a vận đơn | `GET {base}/warehouse-imports/{keyword}`; **bare object**; `None` on 404 | `backend/app/providers/khaibao9610.py:339-344` |
| `find_package_sealing(keyword)` | Look up a mã đóng bao | `GET {base}/package-sealings/{keyword}`; **`{"data":{...}}`**; `None` on 404 | `backend/app/providers/khaibao9610.py:346-352` |

Naming note: the work item that produced this called the registration method
`register_customer`. **No such symbol exists** — `grep -rn register_customer`
returns nothing. The shipped name is `register`
(`backend/app/providers/khaibao9610.py:356`), matching the
`RegistrationProvider` protocol (`backend/app/providers/base.py:91-96`). This
document records the shipped name.

What the adapter shares between the two halves:

* one `_tracking_get` for both lookups, which returns the **raw parsed body**
  (`backend/app/providers/khaibao9610.py:209-280`) and treats HTTP 404 as a
  *result* — `None`, the measured "this code does not exist" answer — not an
  error (`:240-241`);
* percent-encoding of the keyword with `safe=""`, so `/`, `?`, `#`, `&` and the
  real codes' `|` cannot add a path segment, start a query or open a fragment
  (`:228-234`);
* no retries on the lookups, on purpose: a retried GET is only safe if
  idempotency is proven, and the measurement did not test repeat behaviour
  (`:221-226`);
* a chunked read capped at `KHAIBAO9610_MAX_RESPONSE_BYTES`, so an oversized
  reply cannot exhaust a worker's memory (`:282-296`);
* the two real-call switches, refused loudly at construction
  (`:105-116`).

Route paths, for the avoidance of doubt — these are **ours**, and they are not
the provider's paths:

| Ours (public) | Provider's (upstream) |
|---|---|
| `POST /api/v1/registrations` (`backend/app/routers/registrations.py:12`) | `POST https://apiviporder.com/frontend/v1/register` |
| `GET /api/tracking/warehouse-imports/{keyword:path}` (`backend/app/routers/tracking.py:48,77`) | `GET …/frontend/v1/warehouse-imports/{keyword}` |
| `GET /api/tracking/package-sealings/{keyword:path}` (`backend/app/routers/tracking.py:48,103`) | `GET …/frontend/v1/package-sealings/{keyword}` |

The browser uses our paths and nothing else
(`static/js/tracking-search.js:54-55,141-142`). The `{keyword:path}` converter on
the two lookup routes is deliberate: a hostile keyword like `../../etc/passwd`
has its slashes decoded back by the ASGI server before routing, and with a
single-segment converter the route would not match at all — returning a routing
**404** that is indistinguishable from "this tracking code does not exist". The
path converter lets the whole value reach the validator so it can be refused
explicitly with the documented **400** (`backend/app/routers/tracking.py:81-91`).

Registration remains deliberately inert: it never logs the request body, because
the body carries a password and its confirmation
(`backend/app/providers/khaibao9610.py:34-36,357-364`); timeouts and connection
errors become `UNAVAILABLE` with `retryable=True`, never an exception that could
lose a lead (`:381-408`); and duplicate matching is accent-insensitive because
the upstream text is Vietnamese (`:63-83`). `service.py`, the API, the lead model
and the tests all speak in terms of `ProviderStatus`, not in terms of HTTP, so a
provider change stays inside this file.

---

## 6. Provider handoff checklist — the questions, and where each stands

This is the list to take to the provider. Each item is a question, not a
statement: **we are not telling them what their API does, we are asking.** Where
this document seems to know something, it is the owner's prior work, an
observation of a *public client* (§4.2), or — since 2026-10-02 — something the
owner supplied directly (§4.6).

Items 1–9 are the outbound call. Items 10–15 are the response shapes that decide
what we tell the customer. Items 16–20 are operational limits and requirements.
Items 21–22 were **added on 2026-10-02**, when the two tracking lookups became
known; the original checklist of 20 predates them and never asked.

**Where each item stands (2026-10-02, rows 9–11 updated 2026-10-09).** Nothing was
removed: every question below is kept exactly as it was asked, and this table
records the outcome. A status of **SUPPLIED** means the owner answered it directly;
**MEASURED** means we observed it ourselves on a live call and the section named
beside it carries the numbers; **STILL UNKNOWN** means it has not been answered, so
the corresponding code stays defensive (§4.9).

| # | Item | Status |
|---|---|---|
| 1 | Exact registration endpoint | **SUPPLIED** — `POST {base}/register` (§4.6) |
| 2 | HTTP method | **SUPPLIED** — `POST` for register, `GET` for both lookups (§4.6) |
| 3 | Authentication scheme | **STILL UNKNOWN** (§4.9 item 6) |
| 4 | Request headers | **STILL UNKNOWN** |
| 5 | Required fields | **SUPPLIED — field names only** (`name, phone, email, password, confirmPassword, acceptTerms`); which are required, and `null`/absent handling, **STILL UNKNOWN** (§4.6, §4.9) |
| 6 | Optional fields | **STILL UNKNOWN** |
| 7 | Phone format | **STILL UNKNOWN** |
| 8 | Password rules | **STILL UNKNOWN** |
| 9 | Success response example | **MEASURED 2026-10-09** — `200`, `{"status":"success","message":"…"}`, 2 keys, no code (§10.8) |
| 10 | Customer code field | **MEASURED 2026-10-09** — **not in the register body**; it is `customer.code` from `POST /login` and `GET /auth/profile` (§10.8) |
| 11 | Customer id field | **MEASURED 2026-10-09** — `customer.id`, a small integer, in the same two bodies (§10.8) |
| 12 | Duplicate-account response | **STILL UNKNOWN** (§4.9 item 2) |
| 13 | Validation error response | **STILL UNKNOWN** (§4.9 item 3) |
| 14 | Auth error response | **STILL UNKNOWN**, grouped with item 3 |
| 15 | Provider unavailable response | **STILL UNKNOWN** |
| 16 | Rate limit | **STILL UNKNOWN** (§4.9 item 4) |
| 17 | Timeout recommendation | **STILL UNKNOWN** (§4.9 item 5) |
| 18 | Idempotency support | **STILL UNKNOWN** |
| 19 | Test/sandbox endpoint | **STILL UNKNOWN** |
| 20 | IP allowlist requirement | **STILL UNKNOWN** (§4.9 item 6) |
| 21 | Warehouse-import lookup endpoint | **ADDED + SUPPLIED** — `GET {base}/warehouse-imports/{keyword}`; success body is a **bare object**, failure is 404 (§4.6, §4.7) |
| 22 | Package-sealing lookup endpoint | **ADDED + SUPPLIED** — `GET {base}/package-sealings/{keyword}`; success body is **`{"data":{...}}`**, failure is 404 (§4.6, §4.7) |

**How to answer.** "Unknown", "we do not support that" and "not applicable" are
complete and useful answers. One real example response is worth more than a
paragraph describing it. **Never paste a credential, token or password into this
document or into issue #4** — this repository is public and a leaked secret
cannot be un-published. Write down only *where* the secret is kept.

**How to read the one URL in this repository.**
`https://apiviporder.com/frontend/v1` appears in this tree
(`deploy/env.production.example:88`, `backend/app/config.py:39,108`,
`backend/tests/test_khaibao9610_provider.py:42,246`). Until 2026-10-02 it was
labelled *"an observation, not a contract"*; it is now **owner-supplied and
authoritative** (§4.6). The change is in where it came from, not in the string.
It is still not a *response* contract: what comes back is §4.9.

**Anchor caveat for this section.** The item bodies below were written before the
G04B merge. `backend/app/config.py` and `backend/app/providers/khaibao9610.py`
have both grown since, so **many** of the `file:line` anchors inside items 3–20
land near — not on — the code they name; follow the symbol. The status table
above, and the status lines and corrected paragraphs in items 1, 2 and 5, are
anchored against the G04B tree and were re-verified.

### Item 1 — Exact registration endpoint

**Status 2026-10-02 — SUPPLIED.** What we asked for was a complete URL; what we
got is the endpoint the owner supplied as authoritative:
`POST https://apiviporder.com/frontend/v1/register` (§4.6). The host comes from
`KHAIBAO9610_BASE_URL` (`backend/app/config.py:39,108`), the path from
`KHAIBAO9610_REGISTER_PATH` (`backend/app/config.py:51,114`), appended in
`backend/app/providers/khaibao9610.py:375-380`. **Not** supplied: whether a
trailing slash matters, and what the endpoint returns (§4.9 item 1). The question
below is kept exactly as it was asked.

**What we need.** The complete URL to call to create a customer account: scheme,
host, path, and whether a trailing slash matters. If the correct path differs
from the value currently in this repository, they must say so explicitly.

**Why it matters.** The adapter hard-appends `/register` to whatever is in
`KHAIBAO9610_BASE_URL` (`backend/app/providers/khaibao9610.py:190`). If the real
path is anything else, every live call gets a 404, which the adapter classifies
as `INVALID` (`backend/app/providers/khaibao9610.py:260-266`) and the service
stores as a `FAILED`, non-retryable lead
(`backend/app/services/registration.py:426-437`). A customer who completed the
form would have no account and no way to retry.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:189-190`; base URL from
`backend/app/config.py:83` and `deploy/env.production.example:88`.

**Current assumption.** NONE — the question is answered by supply, not by
assumption. What stood here until 2026-10-02 was *"the URL value in this
repository is an observation, not a contract, so it cannot be treated as the
answer to this question."* That is no longer true: the owner supplied it as
authoritative (§4.6), and the value in the repository is now the answer. What
remains unconfirmed is the trailing-slash question and the response body
(§4.9 item 1).

### Item 2 — HTTP method

**Status 2026-10-02 — SUPPLIED.** `POST` for the create-customer call, and `GET`
for both tracking lookups (§4.6). Registration uses
`self._client.post(...)` (`backend/app/providers/khaibao9610.py:375-380`); the
lookups use `self._client.stream("GET", ...)`
(`backend/app/providers/khaibao9610.py:236-238`). A provider answering `405` is
still not specially handled — it falls through to the generic rejection
described below (§4.9 item 3).

**What we need.** The HTTP method for the create-customer call.

**Why it matters.** `POST` is hard-coded (`backend/app/providers/khaibao9610.py:189`) and nothing
verifies it at runtime. A provider answering `405` would fall through the
catch-all at `backend/app/providers/khaibao9610.py:260-266` and be recorded as
`INVALID`: the customer would be told their *details* were rejected while the
lead is marked `FAILED` and cannot be retried
(`backend/app/services/registration.py:426-437`). A wrong method would look like
a data-quality problem for as long as nobody reads the raw status code.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:188-194`.

**Current assumption.** `POST` is no longer an assumption — the owner supplied
it as the method for the create-customer call (§4.6), and the code still uses
`self._client.post(...)` (`backend/app/providers/khaibao9610.py:375-380`). What
remains an assumption is only that nothing else about the call differs: a `405`
is still not special-cased and would be recorded as `INVALID`
(`backend/app/providers/khaibao9610.py:446-452`).

### Item 3 — Authentication scheme

**What we need.** Whether the register call needs credentials at all. If it does:
which scheme (none / API key / Basic / OAuth 2 / JWT / mTLS), the exact
placement of the secret (header name, query parameter or body field), how the
credential is issued, and how it is rotated. **The value itself must be handed
over out of band, never in this document.**

**Why it matters.** The adapter sends no credential of any kind: `_headers()`
returns only `User-Agent`, `Accept` and `Content-Type`
(`backend/app/providers/khaibao9610.py:137-142`). There is also **no settings
field and no environment key for a provider secret** anywhere
(`backend/app/config.py:81-86`, `deploy/env.production.example:76-93`), so a
required key cannot even be configured today without adding one — an unplanned
code change on the critical path. A call that needs a credential and carries
none comes back `401`, which is not special-cased, so it is recorded as
`INVALID` (`backend/app/providers/khaibao9610.py:260-266`): the customer is
blamed and the lead is `FAILED` and non-retryable
(`backend/app/services/registration.py:426-437`).

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:137-142` (headers);
`backend/app/providers/khaibao9610.py:91-115` (the constructor requires a base
URL and a User-Agent, has no credential concept); `backend/app/config.py:81-86`.

**Current assumption.** ASSUMPTION — no authentication on the register call.
The evidence is weak: it rests on the adapter having been written that way and
on the observed call being made from a public browser form, which cannot carry a
secret. Nothing from the provider supports it. If it is wrong, this is the first
thing that breaks.

### Item 4 — Request headers

**What we need.** The complete set of HTTP headers the provider requires or
inspects, with exact names and value formats — including any client, tenant or
merchant identifier, any API version header, locale or `Accept-Language`, and
whether `Origin` or `Referer` is checked.

**Why it matters.** The adapter sends exactly three headers
(`backend/app/providers/khaibao9610.py:137-142`). A required fourth header is
invisible today: the call returns 4xx, classified as `INVALID`
(`backend/app/providers/khaibao9610.py:260-266`) and blamed on the customer's
data. Note also that our `User-Agent` is a **workaround, not a provider
requirement** — it is a real browser UA because the upstream was observed to sit
behind Cloudflare, which rejects non-browser clients with Error 1010
(`backend/app/config.py:31-36`;
`backend/app/providers/khaibao9610.py:26-27,111-115`). If the provider actually
wants a specific or identifying UA, ours is the wrong value. We also do not know
whether they expect an idempotency header (item 18) or a versioning header.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:137-142`; UA default
`backend/app/config.py:33-36,86`; `deploy/env.production.example:90`.

**Current assumption.** ASSUMPTION — `User-Agent`, `Accept: application/json` and
`Content-Type: application/json` are sufficient
(`backend/app/providers/khaibao9610.py:137-142`). Basis: the owner's prior
automation, not the provider. The browser UA is an observation about
Cloudflare's edge behaviour, not a documented requirement.

### Item 5 — Required fields

**Status 2026-10-02 — SUPPLIED (field names only).** The provider's field names
are `name`, `phone`, `email`, `password`, `confirmPassword`, `acceptTerms`, sent
verbatim from `backend/app/providers/khaibao9610.py:365-372`. **Still unknown:**
which of them are required upstream, and what the provider does with an absent or
`null` field — so the payload is built defensively and the response parsing
assumes nothing (§4.9 item 1). Our own required set is `full_name`, `phone`,
`password`, `confirm_password`, `consent`, `accept_terms`
(`backend/app/schemas.py:78-120`). The two naming schemes, and the bug their
mismatch already caused, are in §4.8.

**What we need.** The exact JSON field name, JSON type and required/optional
status of every field on the create-customer call — using the provider's own
names, not ours.

**Why it matters.** The body is built from fixed keys — `name`, `phone`, `email`,
`password`, `confirmPassword`, `acceptTerms`
(`backend/app/providers/khaibao9610.py:365-372`) — and those names are the ones
the owner supplied (§4.6), so the naming question is settled. What is *not*
settled is what the provider does with them: a field it wants under a different
name, or treats as required when we send it absent, means the call is rejected
and the rejection surfaces as a generic `INVALID`
(`backend/app/services/registration.py:426-437`) with nothing pointing at the
field. One further specific: `acceptTerms` used to be hard-coded `true`; it is
now the customer's own value, passed through unchanged
(`backend/app/providers/khaibao9610.py:371`, and why, at `:357-364`). `province`
and `service_interest` exist on our request object but are
**never sent** (`backend/app/providers/base.py:29-58` versus `backend/app/providers/khaibao9610.py:365-372`).

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:365-372` (the body);
`backend/app/providers/base.py:29-58` (our request shape);
`backend/app/schemas.py:75-121` (what the site collects).

**Current assumption.** The six field **names** are no longer an assumption — the
owner supplied them (§4.6) and the adapter sends exactly those
(`backend/app/providers/khaibao9610.py:365-372`). What remains an assumption is
that they are the **complete** set and that all six are **required**: that basis
is still the owner's prior automation plus the observed public form, and the
module still labels the response side unverified
(`backend/app/providers/khaibao9610.py:1-12`). Which fields are required
*server-side* is unknown.

### Item 6 — Optional fields

**What we need.** Which fields may be omitted, what the provider does when one is
missing, and whether a JSON `null` is accepted in place of an absent key. Also
whether the provider has fields we are not sending (province, address, tax
identifier, referral source).

**Why it matters.** Our schema makes `email` optional (`backend/app/schemas.py:79`), and the
adapter forwards it unconditionally as `"email": request.email`
(`backend/app/providers/khaibao9610.py:182`) — so when a customer leaves email
blank we send `"email": null`. We do not know whether the provider wants `null`,
an empty string, or the key omitted entirely. If `null` is rejected, an
*optional* field becomes a validation failure the customer cannot fix, because
it surfaces as the one generic `INVALID` message
(`backend/app/services/registration.py:426-437`). Separately, `province` and
`service_interest` are collected by our form (`backend/app/schemas.py:80-81`)
and silently dropped (`backend/app/providers/khaibao9610.py:179-186`); if the
provider has such fields, we are throwing away data the customer gave us.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:179-186`;
`backend/app/schemas.py:79-81`; `backend/app/providers/base.py:39-41`.

**Current assumption.** ASSUMPTION — only the six fields in item 5 exist, and
only `email` is genuinely optional on our side (`backend/app/schemas.py:79`).
Whether the provider accepts `null` for it, or has optional fields of its own,
is unknown — must be supplied.

### Item 7 — Phone format

**What we need.** The exact phone format the provider expects on the wire: local
`0XXXXXXXXX`, national `XXXXXXXXX`, or E.164 `+84XXXXXXXXX`; whether separators
are allowed; and whether the trunk zero must be present or must be absent.

**Why it matters.** This is the most concrete mismatch visible in the current
code. Our API normalises every phone to canonical E.164 `+84XXXXXXXXX`
(`backend/app/phone.py:29-67`, especially `:67`; applied at the request boundary
by `backend/app/schemas.py:100-106`), and the adapter forwards that value
unchanged (`backend/app/providers/khaibao9610.py:181`). The observed public
registration form admitted only `/^[0-9]{10,11}$/` — digits only, no `+`, no
spaces (§4.2). **If** the provider validates the API the way its own form
validates that form, then every live call from this adapter sends a value that
fails that rule: registration would fail for every customer while MOCK keeps
passing. That is an inference from two observations, not a measured provider
behaviour — which is exactly why the format must be confirmed rather than
assumed. It also touches duplicate handling: our own duplicate detection keys on
the canonical form (`backend/app/services/registration.py:123-130`,
`backend/app/phone.py:1-7`), so if the wire format must differ, the stored value
and the sent value diverge and any future reconciliation by phone has to convert
explicitly.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:181`; `backend/app/phone.py:29-67`;
`backend/app/schemas.py:100-106`.

**Current assumption.** NONE — do not guess. The adapter sends `+84XXXXXXXXX`
because that is our internal canonical form, **not** because the provider asked
for it. This is the single most likely cause of a "passes in MOCK, fails live"
registration.

### Item 8 — Password rules

**What we need.** The password policy the provider enforces when creating an
account: minimum and maximum length, required character classes, any
breach/common-password check, and whether they ever reject a password our form
accepted. Also whether `confirmPassword` must be a distinct value or is ignored.

**Why it matters.** Our form enforces length only: `MIN_PASSWORD_LENGTH = 8`
(`backend/app/schemas.py:29,108-116`). It does **not** enforce the
character-class rules the provider's own form displayed (observed: length ≥ 8
plus lower + upper + digit + symbol, §4.2). So a customer can pass our
validation and be rejected upstream — and the rejection arrives as `INVALID`,
showing one fixed sentence, "The registration service rejected these details.
Please check them and try again."
(`backend/app/services/registration.py:69-71,426-437`), with **nothing to
indicate the password is the problem**. That is an unfixable loop for the
customer. The adapter also sends the same value for `password` and
`confirmPassword` (`backend/app/providers/khaibao9610.py:184`), so a
confirm-mismatch error cannot arise from our side; if the provider inspects that
field for anything else, we need to know. One constraint does not change: the
password is required for the call (`backend/app/providers/base.py:43-45`), is
never stored (`backend/app/services/registration.py:190-197,300-304`) and is
never logged (`backend/app/providers/khaibao9610.py:29-31,178`;
`backend/app/providers/base.py:88-94`). The value forwarded is the **customer's
own** password, typed into our form (`backend/app/schemas.py:78`) and passed
through unchanged (`backend/app/providers/khaibao9610.py:183-184`) — we neither
mint nor keep one. Matching a stricter provider policy
means tightening our form, never weakening that promise.

**Where it lands in the code.** `backend/app/schemas.py:29,108-116`; `backend/app/providers/khaibao9610.py:184`;
`backend/app/services/registration.py:426-437`.

**Current assumption.** ASSUMPTION — length ≥ 8 is our only enforced rule
(`backend/app/schemas.py:29`), chosen by us, not derived from the provider.
Their real policy is unknown, and this is exactly the kind of rule that silently
turns real customers away.

### Item 9 — Success response example

**What we need.** One real, complete successful response: the HTTP status and the
full JSON body, plus the definitive success discriminator — the status code, a
boolean field, or a message string.

**Why it matters.** The adapter treats **any** 2xx as a completed registration
(`backend/app/providers/khaibao9610.py:229-238`) and never looks for a
success/failure flag; it only tries to pull ids out of the body
(`backend/app/providers/khaibao9610.py:144-169,269-273`). If the provider
answers `200` with a body that means "queued" or "failed", this adapter records
`REGISTERED` (`backend/app/services/registration.py:348-404`) and reports
success to a customer whose account may not exist — the exact failure §10 exists
to catch. The reverse matters too: if success is signalled by one specific
status, we need it so contract tests assert the real thing instead of our guess.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:229-238,144-169,269-273`;
`backend/app/services/registration.py:348-404`.

**ANSWERED — measured 2026-10-09 (§10.8).** `HTTP 200`, `application/json`,
96 bytes, two keys: `{"status": "success", "message": "Đăng ký tài khoản thành
công"}`. The discriminator is `status == "success"` together with the 200; the body
carries **neither** the code **nor** the id **nor** a token. The first half of the
old assumption held (2xx does mean the provider accepted it); the second half —
"the body carries the code and id somewhere" — is **false**, and the behaviour that
assumption would have produced is exactly what PR #63 replaced with
`UNUSABLE_RESPONSE`.

### Item 10 — Customer code field

**What we need.** The exact JSON path where the customer code (`TT…`) appears in
the register response — or an explicit statement that this response never
carries it, together with the endpoint that does.

**Why it matters.** This decides whether the site can show a code at all. The
adapter looks for the code under guessed names — `customer_code`,
`customerCode`, `code`, `ma_khach_hang` — at the top level and under `data`,
`customer` and `result` (`backend/app/providers/khaibao9610.py:150-160`). If the
real name is outside that list, `external_customer_code` is written as `NULL`
while the lead is still marked `REGISTERED`
(`backend/app/services/registration.py:359-367,476-489`;
`backend/app/models.py:141`): we would tell the customer they are registered and
then be unable to show them the code they came for. One piece of evidence
already exists and it is *about the client only*: the observed public bundle
reads just `message` from this response, and `customer_code` appears zero times
in it (§4.3). That is not proof about the API — a server-side field the client
ignores remains possible.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:150-160,229-238`;
`backend/app/services/registration.py:476-489`; `backend/app/models.py:141`.

**ANSWERED — measured 2026-10-09 (§10.8).** The register response **never carries
it**. The code is `customer.code`, returned by `POST {base}/login` (beside the
access token) and by `GET {base}/auth/profile` (behind that token). Measured value
for the authorized test identity: `TT5233`. `_extract` already reads that path
correctly — measured, in the running container, on the real body — so **no parser
change is needed**; what is missing is a read-back step in `register()`, because
`/register` returns no token to authenticate `/auth/profile` with.

### Item 11 — Customer id field

**What we need.** The exact JSON path of the provider's immutable customer
identifier for the created account (numeric id, uuid or account id), whether it
is stable across later lookups, and whether it is the same value used elsewhere
in their system.

**Why it matters.** The adapter guesses `customer_id`, `customerId`, `id`,
`user_id` (`backend/app/providers/khaibao9610.py:161-166`). This identifier is
the only handle an operator has to reconcile a lead against the provider's
system, and it is what §9 step 7 is checked against. If it is stored as `NULL`
(`backend/app/services/registration.py:359-367`; `backend/app/models.py:140`), a
"success but no provider-side customer" case cannot be investigated or
reconciled at all. There is a second trap: `id` is an extremely common field
name and `_extract` performs no consistency check
(`backend/app/providers/khaibao9610.py:144-169`), so an unrelated `id` anywhere
in the response would be silently stored as the customer id.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:161-168`;
`backend/app/services/registration.py:359-367`; `backend/app/models.py:140`.

**ANSWERED — measured 2026-10-09 (§10.8).** `customer.id`, a small **integer**
(`6109` for the authorized test identity), in the same two bodies as the code and in
the same object, so `_extract` pairs the two outright. Still unknown: whether the
value is stable across later lookups, and whether it is the id used elsewhere in the
provider's system — one call cannot answer that.

### Item 12 — Duplicate-account response

**What we need.** The exact HTTP status **and** a real body example for "this
phone already has an account". Also whether the same status is used for "phone
exists" and "email exists".

**Why it matters.** The adapter recognises a duplicate only when the status is
**409 or 422** *and* the body text matches a phrase list —
`ton tai|already|taken|da duoc su dung|da dang ky|da co|exist`, compared
accent-stripped and lower-cased
(`backend/app/providers/khaibao9610.py:53-56,62-73,240-246`). Two failure modes
follow. (a) If the provider uses a different status — `400`, or `200` with an
error body — the response falls through to `INVALID`
(`backend/app/providers/khaibao9610.py:260-266`), so the customer is told their
details were wrong and the lead becomes `FAILED` and non-retryable
(`backend/app/services/registration.py:426-437`) even though the account they
want already exists. (b) If the provider uses `200`, the call is recorded as
`SUCCESS` (`backend/app/providers/khaibao9610.py:229-238`). Getting this wrong
either strands an existing customer or fabricates a registration.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:53-56,62-73,240-246`;
`backend/app/services/registration.py:406-424` (409 to the caller; the existing
code is deliberately
*not* returned — `backend/app/services/registration.py:407-409`).

**Current assumption.** ASSUMPTION — a duplicate arrives as `409` or `422` with a
message matching that phrase list. Basis: the owner's prior automation observed
HTTP 422 with such a message (§4.1) — an observation from that automation, not
from the provider. **Our mock returning 409 with "This phone number is already
registered." (`backend/app/providers/mock.py:110-116`) is our invention, not
evidence about theirs.** The provider may differ in every part of this.

### Item 13 — Validation error response

**What we need.** The status code and body for a field-level validation failure,
including whether the body names the offending field(s), with the exact wording
for each rule (bad phone, weak password, missing field, invalid email).

**Why it matters.** Every non-2xx that is not a recognised duplicate, not `429`
and below `500` is classified ** `INVALID` **
(`backend/app/providers/khaibao9610.py:260-266`), and the customer is shown one
fixed sentence (`backend/app/services/registration.py:69-71,426-437`). Any
field-level detail in the provider's body is discarded: `body_text` is used
*only* for the duplicate phrase match and is never otherwise parsed (
`backend/app/providers/khaibao9610.py:225-227,240`). So a customer rejected for
a phone format or a weak password gets no actionable message — and if the
provider returns a rich, field-addressed error, we throw away the one thing that
would let the customer fix it.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:225-227,260-266`;
`backend/app/services/registration.py:426-437`.

**Current assumption.** ASSUMPTION — validation failures arrive as any 4xx below
500 that is not a duplicate, and their body need not be parsed
(`backend/app/providers/khaibao9610.py:260-266`). **Our mock returning 422 with
"The provider rejected these details." (`backend/app/providers/mock.py:118-124`)
is our invention, not a description of the provider.**

### Item 14 — Auth error response

**What we need.** The status code and body the provider returns when **our**
credentials are missing, wrong, expired or not entitled — explicitly
distinguished from a rejection of the customer's data.

**Why it matters.** There is **no code path for an auth error at all.** Neither
`401` nor `403` is named anywhere in the adapter, so both fall into the
catch-all at `backend/app/providers/khaibao9610.py:260-266` and are recorded as
`INVALID`; the service then raises `422 PROVIDER_INVALID`
(`backend/app/services/registration.py:426-437`). Three concrete consequences:
(a) the customer is told **their** details were rejected when in fact our
integration is broken — a false statement made to a customer by our own system;
(b) the lead is stored `FAILED`, which is non-retryable, so after we fix the
credential nobody retries those leads and those customers stay unregistered
silently; (c) a provider-side or configuration-side outage is disguised as a
data-quality problem, so it will be found by customers rather than by us. Of the
20 items, this is the one most likely to produce a large invisible backlog.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:248-266` (only `429` and ≥500
become retryable); `backend/app/services/registration.py:426-437` (INVALID →
FAILED, non-retryable) versus `backend/app/services/registration.py:439-461`
(the PENDING/retryable path an auth error should probably take instead). Note
that `ProviderStatus` has no auth member at all
(`backend/app/providers/base.py:21-25`), so answering this question may require
a deliberate adapter-and-enum change — which must **not** be made before the
answer arrives.

**Current assumption.** NONE — do not guess. If the provider has no
authentication at all (item 3), record that here explicitly rather than leaving
it blank.

### Item 15 — Provider unavailable response

**What we need.** The exact status code(s) and body the provider returns during
planned maintenance and during overload; whether a status page or maintenance
window is announced; and whether they would ever return `200` with "temporarily
unavailable" in the body.

**Why it matters.** The adapter's only unavailability signals are transport
failures — timeout → `PROVIDER_TIMEOUT`, connection/TLS/DNS failure →
`PROVIDER_UNREACHABLE` (`backend/app/providers/khaibao9610.py:195-222`) — and
HTTP `429` or `>= 500` (`backend/app/providers/khaibao9610.py:248-258`). Those
are **the only paths** that keep the lead `PENDING` and retryable and hand the
customer a tracking token
(`backend/app/services/registration.py:439-461,492-501`). A provider that
announces maintenance as `200` plus a body flag would be recorded as
`REGISTERED` (`backend/app/providers/khaibao9610.py:229-238`) — the worst
outcome in the system, because we would show success for an account that was
never created. A provider that uses a non-5xx status for maintenance (say `400`)
would be recorded as `INVALID` and the lead made permanently non-retryable
(`backend/app/services/registration.py:426-437`).

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:195-222,248-258`;
`backend/app/services/registration.py:439-461,492-501,637-649`.

**Current assumption.** ASSUMPTION — unavailability is either a transport
failure, `429`, or 5xx, and any 2xx is a real success
(`backend/app/providers/khaibao9610.py:229-238,248-258`). Both halves
unverified. **Our mock modelling unavailability as 503 and a timeout as
`UNAVAILABLE` with no status (`backend/app/providers/mock.py:85-103`) is our
invention, not a description of the provider.**

### Item 16 — Rate limit

**What we need.** Their limits: requests per second, minute and hour; burst
allowance; whether limits are per-IP or per-account; what a `429` looks like
(including whether `Retry-After` is sent); and whether repeated `429` s escalate
to a block.

**Why it matters.** The adapter handles one in-flight rejection: `429` becomes a
retryable `UNAVAILABLE` with `error_code="PROVIDER_RATE_LIMITED"`, and the lead
stays `PENDING` (`backend/app/providers/khaibao9610.py:248-258`;
`backend/app/services/registration.py:439-461`). There is
**no outbound backoff, queue or scheduler** — nothing under `backend/app/` reads
`Retry-After` or re-calls the provider on a timer. (The only `Retry-After` in
the codebase is the *inbound* limiter's own response header,
`backend/app/middleware.py:163-196`.) `docs/SECURITY.md:860-862` records the
same gap independently: *"There is no circuit breaker and no upstream rate
limiting. If the provider starts failing, every request still makes a full
10-second attempt."* Retrying is a human action via
`POST /api/v1/admin/registrations/{lead_id}/retry`
(`backend/app/services/registration.py:190-230`). So if their limit is below our
launch traffic, leads pile up as `PENDING` and stay there until an operator
retries each one by hand.

Their numbers are also what **our** inbound limits should be sized against, and
today there are two of them, neither related to any provider. nginx is the outer
bound: `viporder_reg` at 10 requests/minute with `burst=5` on the registration
location, `viporder_api` at 120 requests/minute with `burst=40` on `/api/`
(`deploy/nginx/viporder.com.vn.conf:25-26,116-117,126-127`, both returning
`429`). Underneath it the application limiter allows 10 requests per 600 seconds
(`backend/app/config.py:105-107`; wired at `backend/app/main.py:87-89`). Both
were chosen for abuse control. If the provider's ceiling is lower than ours, our
own front door will happily admit more registrations than we can complete
upstream — and each one becomes another `PENDING` lead for a human to retry.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:248-258`;
`backend/app/services/registration.py:190-230,439-461`;
`backend/app/config.py:105-107`; `backend/app/main.py:87-89`;
`backend/app/middleware.py:163-196`;
`deploy/nginx/viporder.com.vn.conf:25-26,116-117,126-127`;
`docs/SECURITY.md:860-862`.

**Current assumption.** NONE for their numbers — do not guess. What we *do* assume
is that a `429` means "retry later is safe"
(`backend/app/providers/khaibao9610.py:248-258`), which is itself unverified and
depends on item 18.

### Item 17 — Timeout recommendation

**What we need.** How long a register call normally takes, their worst case, and
the timeout they recommend we use.

**Why it matters.** We have a single hard timeout, currently 10 s, adjustable
only between 1 and 30 s by validation (`backend/app/config.py:84`;
`backend/app/providers/khaibao9610.py:58-59,103-108`). Two couplings matter. (a)
If their honest worst case exceeds 30 s, our ceiling is simply wrong: we would
abandon calls that would have succeeded, leaving leads `PENDING` for no reason.
(b) The **phone claim TTL must comfortably exceed the provider timeout**, or a
slow provider lets a second attempt through and reintroduces exactly the
duplicate the claim exists to prevent — that rationale is written at
`backend/app/config.py:110-112`, with the default at `backend/app/config.py:113`
(120 s). Changing the timeout without changing the TTL opens that hole, so the
two numbers have to move together. Finally, a short timeout is not free: every
timeout is a *possible* duplicate, because the provider may have created the
account after we stopped listening — item 18's problem.

**Where it lands in the code.** `backend/app/config.py:84,108-113`;
`backend/app/providers/khaibao9610.py:58-59,103-108,195-207`;
`deploy/env.production.example:89`.

**Current assumption.** ASSUMPTION — 10 s is enough (`backend/app/config.py:84`;
`deploy/env.production.example:89`). It is a sane default chosen by us, not a
provider figure. The 1–30 s clamp is our own safety choice
(`backend/app/providers/khaibao9610.py:58-59`).

### Item 18 — Idempotency support

**What we need.** Whether their create-customer call is idempotent, and if so by
what mechanism: a client-supplied key (which header or field name?), a natural
key such as the phone number, or a server-side dedupe window. If it is **not**
idempotent, we need that stated explicitly.

**Why it matters.** This decides whether retrying is safe, and we retry from two
places today: the customer re-submitting after a `202`, and the operator route
`POST /api/v1/admin/registrations/{lead_id}/retry`
(`backend/app/services/registration.py:190-230`). Neither sends any idempotency
token upstream — the adapter's `_headers()` has none
(`backend/app/providers/khaibao9610.py:137-142`) and the body has no key field
(`backend/app/providers/khaibao9610.py:179-186`). **Do not confuse this with our
own inbound `Idempotency-Key` ** (`backend/app/routers/registrations.py:14`;
`backend/app/services/registration.py:111-117,234-258,582-594`): that header
deduplicates *our* API for one browser session and is never forwarded. So a
timeout followed by a retry can create a second account upstream while our
database holds a single lead — a duplicate customer only the provider can see.
If their call is not idempotent, the retry route needs an operator-facing guard
(check the provider before retrying), and §10's existing prohibition stands.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:137-142,179-186,195-207` (the
timeout is the dangerous case); `backend/app/services/registration.py:190-230`
(retry), `:111-117,234-258` (our inbound key, unrelated);
`backend/app/routers/registrations.py:14`.

**Current assumption.** NONE — do not guess. Our code *behaves* as though
retrying is safe, by offering a retry route
(`backend/app/services/registration.py:190-230`), but that is an assumption
embedded in behaviour, not a verified fact about the provider. It is precisely
the assumption §10 warns about.

### Item 19 — Test/sandbox endpoint, if any

**What we need.** Whether a test, sandbox or staging base URL exists; whether it
needs separate credentials; whether accounts created there are real rows in
their system that must be cleaned up; and whether a test account can collide
with a real phone number. If there is no sandbox, we need their rules for a
first real call.

**Why it matters.** There is **one** base URL setting and no notion of an
environment (`backend/app/config.py:83`; `deploy/env.production.example:88`),
and the only validation is that it is non-empty
(`backend/app/providers/khaibao9610.py:109-110`). Testing against a sandbox
therefore means temporarily pointing the *production* variable at another host —
the same switch, no separate flag, no guard. No code path marks a lead as a test
lead either. If no sandbox exists, the first real call is a real customer record
in the real system, and the cleanup rules have to be agreed in advance: this is
what §9 step 4 depends on.

**Where it lands in the code.** `backend/app/config.py:83`;
`deploy/env.production.example:88`;
`backend/app/providers/khaibao9610.py:109-110`.

**Current assumption.** NONE — do not guess. Whether a sandbox exists is unknown;
this repository has never had sandbox configuration, so their answer is the only
source.

### Item 20 — IP allowlist requirement, if any

**What we need.** Whether their API requires our production egress IP address(es)
to be registered or allow-listed; how addresses are submitted and how long a
change takes; and whether IPv6 or multiple addresses are supported.

**Why it matters.** There is **no allowlist configuration for the provider
anywhere in this repository's application or deployment configuration** — no
setting in `backend/app/config.py`, no key in the `KHAIBAO9610` block of
`deploy/env.production.example` (lines 76–93), and no `allow` / `deny` directive
in the nginx configuration. `docs/SECURITY.md:863-865` records the same finding
independently as *"No egress allowlist"*. So this requirement, if it exists, is
currently invisible to us and would fail at the network layer. The failure mode
is specific and quiet: a refused connection raises `httpx.HTTPError`, which
becomes `UNAVAILABLE / PROVIDER_UNREACHABLE` and the lead stays `PENDING`
(`backend/app/providers/khaibao9610.py:208-222`;
`backend/app/services/registration.py:439-461`). The site keeps telling
customers "we received your registration", no error reaches any dashboard, and
nothing distinguishes "the provider is down" from "our address was never
allow-listed" — which is the reason `error_code` exists at all
(`backend/app/providers/base.py:56-60`;
`backend/app/services/registration.py:637-649`). There is also a deployment
consequence: a new server, a changed container host or a changed egress address
would break registration with no code change and no failing test.

**Where it lands in the code.** No code path yet for configuring an allowlist. The
observable failure lands at `backend/app/providers/khaibao9610.py:208-222` →
`backend/app/services/registration.py:439-461`. Provider deployment settings are
`deploy/env.production.example:76-93`, which has no such key.

**Current assumption.** NONE — do not guess. Prior work recorded that the upstream
was observed behind Cloudflare and that a real browser `User-Agent` was needed
(`backend/app/config.py:31-32`; `backend/app/providers/khaibao9610.py:26-27`) —
an observation from prior automation about the *edge*, which says nothing about
whether a server-to-server IP allowlist is required.

### Item 21 — Warehouse-import lookup endpoint (ADDED 2026-10-02)

**What we asked, once we knew tracking existed.** The endpoint that answers "where
is this vận đơn", its method, and the shape of a successful and a missing answer.

**What we got.** `GET https://apiviporder.com/frontend/v1/warehouse-imports/{keyword}`,
and a **bare object** on success (§4.6, §4.7). A missing code is HTTP **404**
`{"error":"Mã vận đơn không tồn tại"}`. Keyword search needed no credentials when
observed (§4.7).

**Status.** **SUPPLIED** — path, method and both envelopes. Still UNKNOWN: the
response schema beyond the two envelopes, the rate limit (§4.9 item 4), and
whether production adds authentication or an IP allowlist (§4.9 item 6).

**Where it lands in the code.** `backend/app/config.py:52,115`;
`backend/app/providers/khaibao9610.py:339-344`;
`backend/app/routers/tracking.py:77-100`;
`backend/app/tracking.py:239-269`.

### Item 22 — Package-sealing lookup endpoint (ADDED 2026-10-02)

**What we asked.** As item 21, for "which parcels are in this mã đóng bao".

**What we got.** `GET https://apiviporder.com/frontend/v1/package-sealings/{keyword}`,
and a **wrapped** body, `{"data": {...}}`, on success — *not* the same envelope as
item 21 (§4.6, §4.7). A missing code is HTTP **404**
`{"error":"Mã đóng bao không tồn tại hoặc bạn không có quyền truy cập"}`.

**Status.** **SUPPLIED** — path, method and both envelopes. The one thing worth
repeating: a successful body from this endpoint is **not** interchangeable with a
successful body from item 21, and the code that unwraps it is in one place only
(`backend/app/tracking.py:281`). Still UNKNOWN: the response schema beyond the
envelope, and the same operational limits as item 21.

**Where it lands in the code.** `backend/app/config.py:53,116`;
`backend/app/providers/khaibao9610.py:346-352`;
`backend/app/routers/tracking.py:103-116`;
`backend/app/tracking.py:272-299`.

### Open, but not one of the 20

**Consent basis.** Is VIPORDER permitted to register customers on their behalf
from `viporder.com.vn`? This is a business and legal question rather than an
engineering one (§4.4), so it is kept out of the 20 engineering items — but it
must be answered before any real call, because it decides whether the flow is
allowed at all, not merely whether it works.

---

## 7. The two-switch live guard

Making a real outbound call requires **both** of these to be set:

| Switch | Default | Where |
|---|---|---|
| `KHAIBAO9610_MODE=http` | `mock` | `backend/app/config.py:107`, `deploy/env.production.example:80` |
| `KHAIBAO9610_ENABLE_REAL_CALLS=yes` | `no` | `backend/app/config.py:110`, `deploy/env.production.example:86` |

Either one alone is refused, loudly, at construction time: the adapter raises
`ProviderConfigurationError` before it can send anything
(`backend/app/providers/khaibao9610.py:105-116`;
`backend/app/providers/base.py:103-108`). The provider factory only reaches the
live adapter when the mode is exactly `http`
(`backend/app/providers/factory.py:28-34`), and the mode is a `Literal` in
settings, so a typo fails at startup instead of silently selecting something
else (`backend/app/config.py:107`).

**One adapter, two separately gated halves — there IS a tracking-only switch.**
The two lookups and `register()` are methods on the *same* live adapter
(`backend/app/providers/khaibao9610.py:556,570,588` — measured 2026-10-07), and
the factory has exactly one mode that selects it
(`backend/app/providers/factory.py:28-34`) — but the capabilities are gated
independently inside it: `find_warehouse_import` and `find_package_sealing` refuse
unless `KHAIBAO9610_ENABLE_REAL_CALLS=yes`, and `register()` refuses unless
`KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes`. Setting only the read switch therefore
reaches the provider for tracking and **cannot** create a customer.

This paragraph used to say the opposite — "there is no tracking-only switch ...
Setting the two switches therefore arms real registration writes at the same time
as live tracking; there is no way to enable one without the other" — which was
true of the one-switch design and is exactly the property that let a tracking test
create a real account. It is corrected rather than deleted for that reason.
`/api/v1/health` reports both capabilities separately
(`checks.provider.capabilities.tracking_reads` and `.registration_writes`) so this
is checkable from outside, without reading an env file.

In the default `mock` mode the configured provider implements no tracking method
at all, which is why a valid keyword answers **503** rather than going silent
(`backend/app/providers/mock.py:82`;
`backend/app/routers/tracking.py:150-158`).

Why two switches: a single `MODE=http` can be set by a typo, a copied config, or
a stale `.env` on a server nobody is watching. A second, deliberately named flag
makes "we started sending real customer data to a provider endpoint" a thing
someone had to *mean* (`backend/app/config.py:7-10`;
`deploy/env.production.example:82-85`).

**Exactly one live registration call HAS been made through this adapter** — the
owner-authorised one-shot test of **2026-10-07 05:53:38 UTC** (§10.6), which sent
one `POST` to the provider's `/register` through the application and was accepted
with **201**. This paragraph said "no live registration call has ever been made"
until that test happened; it is corrected rather than deleted, because it is also
the reason the shipped default is still `mock`.

What the one call does **not** establish is the response mapping: the provider's
raw body was not captured and `_extract` found no customer code, so the success,
duplicate and validation schemas are still UNKNOWN (§4.9). The shipped mode is
still `mock` in every configuration file (`backend/app/config.py:107`,
`deploy/env.production.example:80`, `.env.example:31-33`); the read switch
defaults to off (`backend/app/config.py:110`) and so does the write switch
(`backend/app/config.py:122`), and both are `no` in the production template
(`deploy/env.production.example:86`). The provider's own test file reaches it
only through a stub HTTP transport, never the network
(`backend/tests/test_khaibao9610_provider.py:48-52`;
`backend/tests/test_tracking.py:162-174`), and `docs/SECURITY.md:894` records the
state at the revision it documents — before that test existed — as *"No live call
has ever been made, so none of the mapping … has been observed against the real
upstream."*

Registering and tracking are different capabilities with different switches, and
the distinction is load-bearing. Read-only `GET`s *were* made against the live
service on 2026-10-02 during reconnaissance (§4.7); the two tracking lookups have
still never been exercised **through this backend** against the live service
(§11).

Scope of the "never registered" claim, stated plainly: it was true up to
2026-10-07 05:53 UTC and is **false** now. The record of live registrations is
exactly one `POST`, and it is not a claim about what a running server's
environment contains — verifying that means reading the environment on that
server, out of band. Do not report "never called" as a measured fact about a
deployment on the strength of this document alone.

---

## 8. MOCK is not working registration

**MOCK is not working registration.** It registers nobody, it never touches the
network (`backend/app/providers/mock.py:54-57`), and the customer codes it
produces are fabricated inside our own process.

With the shipped default (`KHAIBAO9610_MODE=mock`, `backend/app/config.py:82`)
the provider factory returns `MockRegistrationProvider`
(`backend/app/providers/factory.py:30-31`). On "success" it returns HTTP 201 and
an invented code from an in-process counter:
`external_customer_code = f"TT{sequence:05d}"` and
`external_customer_id = f"mock-customer-{sequence:05d}"`
(`backend/app/providers/mock.py:126-133`; counter at
`backend/app/providers/mock.py:67,76-78`). A customer shown `TT00001` has no
account anywhere, and that code means nothing to anyone outside this process.
`external_customer_id` is invented the same way
(`backend/app/providers/mock.py:129`, stored at
`backend/app/services/registration.py:359-367`, columns at
`backend/app/models.py:140-141`).

**In the shipped mock configuration there is no tracking at all.** The mock
provider implements `register` and nothing else (`backend/app/providers/mock.py:82`),
so both lookup routes answer **503 `PROVIDER_UNAVAILABLE`** ("chức năng tra cứu
chưa được bật") rather than inventing a parcel
(`backend/app/routers/tracking.py:150-158`, pinned at
`backend/tests/test_tracking.py:849-862`). That is deliberate: fabricating a
tracking answer would be worse than admitting there is nobody to ask. The same
503 is what a *valid* keyword gets in the default configuration (§2).

The mock also invents a whole set of responses:

| Behaviour | What the mock returns | Where |
|---|---|---|
| success | 201 + fabricated `TT…` code | `backend/app/providers/mock.py:126-133` |
| duplicate | 409, "already registered" | `backend/app/providers/mock.py:110-116` |
| invalid | 422, "rejected these details" | `backend/app/providers/mock.py:118-124` |
| unavailable | 503, `PROVIDER_UNAVAILABLE` | `backend/app/providers/mock.py:96-103` |
| timeout | `UNAVAILABLE`, no status, `PROVIDER_TIMEOUT` | `backend/app/providers/mock.py:85-94` |
| error | raises; the service keeps the lead `PENDING` | `backend/app/providers/mock.py:105-108`; `backend/app/services/registration.py:323-341` |

Behaviour can be forced per request by phone suffix — `0000` duplicate, `0001`
invalid, `0002` unavailable, `0003` timeout, `0004` raises, anything else
success (`backend/app/providers/mock.py:43-49,72-74`) — or for a whole instance
via `MOCK_PROVIDER_BEHAVIOUR` (`backend/app/providers/mock.py:8-11`;
`backend/app/config.py:122-124`; `.env.example:68-70`).

**These are OUR mock's behaviours, not the provider's.** They are useful evidence
of what we *expect* an upstream to do, and they let us exercise our own failure
handling without a contract. No answer in §6 may be derived from them, and none
of them has been observed from the real provider. Where §6 says "our mock
returns X — our invention", this is the table it refers to.

To state out loud, to anyone who asks whether registration works:

* the site may be shown and used, but a registration made in the default
  configuration created no customer account and no usable customer code;
* `docs/PROJECT-STATUS.md` and issue #4 track this as `BLOCKED_EXTERNAL`;
* the site must never claim that registration produces customer codes until §6 is
  answered and §9 steps 1–7 have been done with real evidence.

---

## 9. Turning the real integration on

Only after the **still-unknown** items of §4.9 are answered and a staging
endpoint exists (§6 item 19). The endpoints themselves are no longer the
blocker — §4.6 settled those.

**What has actually been exercised, as of 2026-10-02.** Read-only `GET`s against
the live service were made during reconnaissance and are recorded in §4.7: the
two envelopes, the two 404 bodies, the full tracking code, and the
unauthenticated keyword form. **No `POST` has ever been made** against the
registration endpoint. Tracking has never been exercised against the live
service *through this backend*, and it cannot be without also enabling real
registration writes, because the two lookups are methods on the same live
adapter that registers (§7). The 200 path for both lookups has therefore only
been exercised against `httpx.MockTransport` and a fake provider
(`backend/tests/test_tracking.py:1-6,162-174`).

1. **Record the answers** in §4.9 (and in the matching §6 item), keeping the
   evidence. Do not overwrite §4.6 or §4.7 — those are measured history, and a
   later answer that contradicts them is a finding, not an edit.
2. **Write the provider against the contract.** No business logic changes.
3. **Add contract tests** using `httpx.MockTransport` for every documented
   response, including the duplicate and validation shapes.
4. **Test against the provider's staging endpoint** with a clearly-fake customer,
   and confirm on their side that the customer was created. Then delete it.
5. **Confirm the failure paths** with the real client: force a timeout, force a
   5xx, and verify the lead row still exists and is marked retryable.
6. **Only then** set `KHAIBAO9610_MODE=http` and
   `KHAIBAO9610_ENABLE_REAL_CALLS=yes` on the server, and update
   `docs/PROJECT-STATUS.md` and issue #4 with the measured result.
7. **Watch the first real registrations** and check that each produced a lead row
   with a matching external code on the provider's side. A registration that
   returns success but creates no provider-side customer is the failure this whole
   design exists to catch.

Until step 7 has been done with real evidence, the site must not claim that
registration produces customer codes.

---

## 10. What must never happen

* A credential in this repository, in an issue, a PR description, or a log.
* A password in the lead store, in logs, in traces, or in analytics.
* `KHAIBAO9610_MODE=http` enabled while any §4.9 item is still UNKNOWN.
* Setting the two switches for *tracking's* sake without meaning to arm real
  registration writes as well (§7).
* A registration that returns success while the provider created nothing —
  this is why the response reports the provider's own status rather than assuming
  it.
* Retrying a non-idempotent call without checking (§6 item 18) — that is how
  duplicate customers are created.

---

## 10.5 Running ONE real registration, when the owner is ready

The registration contract is still UNKNOWN, and the only way to learn it is to
register once against the real API. That is a WRITE on somebody else's production
system — it creates an account a person could try to sign in with — so it is not in
`tests/` and pytest never collects it.

`tools/test_live_registration.py` sends **exactly one POST per invocation**, has no
retry and no batch mode, and refuses unless three things are all true:

1. `ENABLE_LIVE_REGISTRATION_TEST=yes` — an unmistakable explicit flag;
2. every field of a test identity is supplied through the environment;
3. the base URL is not the marketing domain.

It never prints the password: the response is scanned and every occurrence redacted
before anything reaches the terminal. It prints the status, content type, elapsed
time, the response's **key names**, and a sanitized body — enough to write a real
contract from, not enough to leak a credential into scrollback.

**The exact command for the owner to run** (substitute real test-identity values):

```bash
ENABLE_LIVE_REGISTRATION_TEST=yes \
LIVE_REG_NAME='Khách Kiểm Thử' \
LIVE_REG_PHONE='0900000000' \
LIVE_REG_EMAIL='test@example.com' \
LIVE_REG_PASSWORD='<a-real-test-password>' \
LIVE_REG_READ_BACK=yes \
python3 tools/test_live_registration.py
```

**To answer issue #4 in the same run, add `LIVE_REG_READ_BACK=yes`.** After a 2xx
registration the tool then sends exactly one `POST /login` (`{account, password}`,
the same identity) and, only if a token comes back, exactly one
`GET /auth/profile` — the route §10.7 found the customer code behind. Neither
creates anything, neither is retried, both are announced before the registration is
sent, and all three answers land in one evidence file with the password **and the
token** redacted. The terminal ends with `customer-code candidates:` — every value
shaped `TT<digits>` and the key path it sits under. Without this flag, a second run
would only re-measure what §10.6 already measured.

The tool presents the backend's browser `User-Agent` (`DEFAULT_BROWSER_USER_AGENT`,
pinned equal by a test): Cloudflare answers a self-describing one with Error 1010,
which would spend the authorized attempt on a 403 that never reached the API.

**Use a phone number and email that have never registered** — the provider already
holds the 2026-10-02 and 2026-10-07 accounts, so re-using either would measure the
duplicate response instead of the success schema. Run it from a machine that can
reach `apiviporder.com` (the staging host can; a sandboxed CI or agent container
may not).

**Before running it, decide two things:** that the identity is one the owner is
content to have created, and that a duplicate is acceptable if the call is repeated.
The tool cannot know either.

**If it fails, do not simply re-run it.** A transport failure means the request may
or may not have arrived, and a second attempt can create a second account. Check
with the provider first.

Whatever it returns, the result belongs in §4.9 of this document as MEASURED — that
is what moves registration from UNKNOWN, and nothing else will.

## 10.6 OBSERVED LIVE REGISTRATION — owner-authorized one-shot test

**SOURCE: OWNER-AUTHORIZED LIVE TEST**
**DATE/TIME: 2026-10-07 05:53:38 UTC** (lead `created_at`)
**POSTS: exactly one.** Consumed once. No retry, and a second POST requires new
owner authorization.

### Request

`POST https://apiviporder.com/frontend/v1/register`, field names exactly
`name / phone / email / password / confirmPassword / acceptTerms`. Sent through the
application, so the adapter's own wire format is what reached the provider.

### Observed result

| | |
|---|---|
| our HTTP status to the client | **201** |
| content type | `application/json` |
| latency | 0.747 s |
| response bytes | 228 |
| resulting lead | `REGISTER_LEAD \| REGISTERED \| resp_status=201` |
| **`external_customer_id`** | **null** |
| **`external_customer_code`** | **null** |
| customer message | "Đăng ký thành công." |

Sanitised body, exactly as our API returned it:

```json
{"lead_id":"<uuid>","registration_status":"REGISTERED",
 "external_customer_id":null,"external_customer_code":null,
 "message":"Đăng ký thành công.","login_url":"https://khachhang.viporder.com.vn"}
```

### What this establishes, and what it does NOT

**Established:** the provider accepts a registration for these field names and
answers 2xx. The lead is created, reaches `REGISTERED`, stores no password, and the
request is **not** retried.

**NOT established, and it is the important part:** the provider's **raw JSON was not
captured**. `_extract` found no customer id and no customer code anywhere — it
searches the top level plus `data`/`customer`/`result` against
`customer_code | customerCode | code | ma_khach_hang` and
`customer_id | customerId | id | user_id`. So either

* the provider does not return a customer code in the register response, or
* it returns one under a key we do not yet parse.

**We cannot tell which**, because the container was recreated to disarm the write
switch and that discarded the log holding the body. Recorded rather than glossed over.

Consequence, fixed in the same round: a 2xx that identifies nobody is no longer
reported as a plain success. It becomes `UNUSABLE_RESPONSE`, the lead stays `PENDING`
with that error code, the client gets a truthful 202, and a warning is logged.

### READ THE MERGE TIMESTAMPS BEFORE CONCLUDING ANYTHING ABOUT TODAY'S BEHAVIOUR

The `REGISTERED` lead above is **not** what this code does now. Measured with
`gh pr view <n> --json mergedAt`:

| Event | UTC |
|---|---|
| PR #61 merged — compose finally passed the WRITE switch through | `05:52:37` |
| **the owner-authorised POST** | **`05:53:38`** |
| PR #63 merged — an empty 2xx becomes `UNUSABLE_RESPONSE` | `06:01:23` |

The POST ran **61 seconds** after the write switch became usable, and **7 minutes
45 seconds BEFORE** PR #63. So it exercised the **OLD** code, the one that treated an
empty 2xx as `SUCCESS` — which is exactly why the lead row reads `REGISTERED` with
`external_customer_code = null`.

**The same provider body today would return `UNUSABLE_RESPONSE` and would NOT mark
the lead `REGISTERED`.** A reader who skips this table will mistake a behaviour that
was patched out 8 minutes later for the behaviour of the running system. The
observation is still valid evidence about the *provider*; it is **stale** evidence
about *our* code.

### THE PHONE NUMBER FROM THAT TEST IS NOW CLAIMED — measured 2026-10-07 on staging

Read-only query against the staging database (`viporder-db-1`), structure and counts
only, no personal data printed:

```
uq_leads_live_phone  UNIQUE (phone)
  WHERE lead_type = 'REGISTER_LEAD'
    AND (in_flight_at IS NOT NULL OR registration_status = 'REGISTERED')

rows currently matching that predicate : 4
  of which REGISTERED with NO customer code : 2
  including the lead created 2026-10-07 05:53:38Z — the authorised POST
```

**Operational consequence for the NEXT authorised POST:** re-using the same phone
number will be **refused by our own application before the request ever reaches the
provider** — the partial unique index blocks the insert and the retry route answers
"already registered". Either clear that lead row on staging first, or use a different
number. Budgeting one POST and then spending it on a local uniqueness violation would
waste the authorisation.

**Also measured, and worth knowing before anyone goes looking:** of the five leads on
staging, two carry a customer code — but both codes match the **mock** provider's
exact format `^TT[0-9]{5}$` (`backend/app/providers/mock.py`, `f"TT{sequence:05d}"`),
with 19-character non-numeric ids. They are **mock** rows, not provider rows. And the
`leads.response_body` column holds **our own API's** response, not the provider's raw
body — so the raw body really is gone, and `response_body` is not a way to recover it.

### Still UNKNOWN after this test

Registration success **response schema** · duplicate response · validation-error
schema · rate limit · timeout expectation · whether production needs auth or an IP
allowlist. One call answers one question, and this one answered "the call works and
the code does not arrive".

## 10.7 WHERE THE CUSTOMER CODE LIVES — read-only probe, 2026-10-07

**SOURCE: OUR OWN READ-ONLY PROBE. No POST was sent. No write of any kind.**
**Method:** `GET` and `OPTIONS` only. An `OPTIONS` reply of `405` carrying an `Allow:`
header proves a route exists without invoking it.

### What was measured

| Endpoint | Real method(s) | Evidence |
|---|---|---|
| `POST /frontend/v1/register` | POST | the owner-authorized call of 2026-10-07 |
| `POST /frontend/v1/auth/register` | POST | `405` + `Allow: POST` |
| **`POST /login`** | POST | `405` + `Allow: POST` |
| `POST /auth/refresh` | POST | `405` + `Allow: POST` |
| `POST /auth/logout` | POST | `405` + `Allow: POST` |
| **`GET /auth/profile`** | GET, HEAD | `401` `{"error":"Unauthenticated."}` |

`/frontend/v1/auth/profile` answers `401` with a JSON body, so it exists, it is
authenticated, and the provider returns JSON errors as `{"error": "..."}`.

### What this means

The provider runs a **token-authenticated API in the Laravel Sanctum shape**:
`POST /login` yields a token, and **`GET /auth/profile` is where the customer record —
and therefore the customer code — actually lives.**

Our measured register response carries **six keys and no token**:

```
['external_customer_code','external_customer_id','lead_id',
 'login_url','message','registration_status']      token present: NO
```

So the strongest available explanation of the 2026-10-07 result is:

> **`/register` does not return a customer code at all.** The code is obtained from
> `/auth/profile` after logging in — and because `/register` returns no token, the
> current flow has no way to make that call.

This is consistent with everything measured: HTTP 201, `REGISTERED`, and
`external_customer_code` null. It is **not proof** — the only way to confirm the
register body's full schema is one more authorized POST.

### Provenance and limits, stated plainly

* **Measured:** the existence and method of each route above; the `401` shape; the six
  keys in our own register response.
* **NOT measured:** the body of `POST /login`; the body of `GET /auth/profile`; whether
  `/register` returns a token under some other key; whether `/auth/register` differs
  from `/register`.
* **Not attempted, deliberately:** no login, no registration, no write. The `/login`
  route was proved to exist by its `405`, never by calling it. Obtaining a token would
  require credentials we do not hold — the 2026-10-07 password was generated in-process
  and never stored, by design.

### Consequence for the next authorized call — CARRIED OUT, see §10.8

This is exactly what the authorized call of **2026-10-09** did: one
`POST /register`, then one `POST /login`, then one `GET /auth/profile`. **The
hypothesis above was confirmed.** `/register` really does return no code and no
token, and the code really does live at `customer.code` behind the login. §10.8
has the measured statuses, byte counts and bodies; read it rather than the
hypothesis.

## 10.8 THE PROVIDER'S OWN REGISTER BODY, AND WHERE THE CODE LIVES — owner-authorized one-shot test, 2026-10-09

**SOURCE: OWNER-AUTHORIZED LIVE TEST**, run with `tools/test_live_registration.py`.
**DATE/TIME: 2026-10-09 16:27:24 UTC** (`recorded_at` in the evidence file;
`2026-10-09T23:27:24+07:00` local).
**POSTS: exactly one** `POST /register`, plus the two read-back calls the
`LIVE_REG_READ_BACK` flag announces before sending anything — one `POST /login` and
one `GET /auth/profile`, neither of which creates anything. Consumed once; a second
POST requires new owner authorization.

**THIS MEASURES THE PROVIDER, NOT US — and that is the difference from §10.6.**
§10.6 recorded *our own API's* answer to a registration; its six keys (`lead_id`,
`registration_status`, `login_url`, …) are ours, which is why its 201 and its 228
bytes say nothing about the provider. The tool used here posts **directly** to
`https://apiviporder.com/frontend/v1/register`, so everything below is the
provider's own body.

Measured consequence of that directness: **our staging database was not touched.**
`select count(*) from leads` read **8 before and 8 after**, and the pre-existing
lead for the test phone still reads `PENDING`, `attempt_count = 1`,
`last_error_code = PROVIDER_ERROR`, `in_flight_at` NULL,
`updated_at 2026-10-08 06:31:13Z`. **No lead row exists for this registration**, so
nothing in `leads` can be used as evidence about it — the evidence file is the only
record.

### The three calls, as measured

| Call | Status | Content-type | Bytes | Elapsed |
|---|---|---|---|---|
| `POST /frontend/v1/register` | **200** | `application/json` | **96** | 0.623 s |
| `POST /frontend/v1/login` | **200** | `application/json` | 592 | 0.295 s |
| `GET /frontend/v1/auth/profile` | **200** | `application/json` | 223 | 0.231 s |

Staging was serving `e8297746243bb6ac0362f3b429ab22391fc377d3` at the time, with
`registration_writes: disabled` on `/api/v1/health` — the tool does not go through
the application, so the write switch being off neither enabled nor blocked it.

### Item 9 answered — the register success body

**Two keys. No customer code, no customer id, no token.**

```json
{"status": "success", "message": "Đăng ký tài khoản thành công"}
```

`key_names` as recorded: `status`, `message`. The success discriminator is
`status == "success"` **together with** HTTP **200** — not the 201 that §10.6's
our-side figure might lead a reader to expect of the provider.
`password_echoed: false`.

### Items 10 and 11 answered — the code is `customer.code`, behind a login

`POST /login` with the identity just registered returns a token **and** the customer
record; `GET /auth/profile` with that token returns the same record without the
token. Token and token type are redacted by the tool; the test identity's phone and
email are not written down here, following §10.6's practice.

```json
// POST /login — 592 bytes
{"access_token": "<REDACTED:by-key>", "token_type": "<REDACTED:by-key>",
 "expires_in": null,
 "customer": {"id": 6109, "name": "VIPORDER NGHIEM THU", "code": "TT5233",
              "phone": "<masked>", "email": "<masked>", "address": null,
              "created_at": "2026-10-09T16:27:25.000000Z",
              "updated_at": "2026-10-09T16:27:25.000000Z"}}

// GET /auth/profile — 223 bytes
{"customer": {"id": 6109, "name": "VIPORDER NGHIEM THU", "code": "TT5233", …}}
```

| | JSON path | Measured value |
|---|---|---|
| **customer code** | **`customer.code`** — in `/login` and in `/auth/profile` | `TT5233` |
| **customer id** | **`customer.id`** — same two bodies, same object | `6109` (integer) |

`expires_in` came back **null**, so the token's lifetime is not advertised. The
customer record has exactly `id, name, code, phone, email, address, created_at,
updated_at`; `address` was null for a registration that never supplied one.

**`TT5233` matches the mock provider's `^TT[0-9]{5}$` shape, so §10.6's way of
telling mock rows from provider rows no longer works.** The real provider issues
codes in the same shape as `backend/app/providers/mock.py`. Tell them apart by the
**id** instead: the provider's `customer.id` is a small integer, the mock's is a
19-character non-numeric string.

### The parser already reads this correctly — measured, not reasoned

`_extract` was run against all three measured bodies **inside the running staging
container**, on the image built from `e829774`:

```
/register        _extract -> (None, None)
/login           _extract -> ('6109', 'TT5233')
/auth/profile    _extract -> ('6109', 'TT5233')
```

So **no parser change is needed to read the code.** `customer` is already in
`_WRAPPER_KEYS`, `code` in `_AMBIGUOUS_CODE_KEYS` — and `TT5233` survives the
digits-only status filter that exists to stop `{"code": 200}` being handed to a
customer — `id` is in `_ID_KEYS`, and the code and the id arrive in the **same**
object, so they are paired outright rather than through the sibling-wrapper
fallback. `(None, None)` for `/register` is the *correct* answer, not a miss.

The three shapes are pinned in `backend/tests/test_extract_customer_shapes.py`
against a masked fixture, `backend/tests/fixtures/live_registration_20261009.json`,
because the authorization that produced them is spent: if a later change to the
wrapper or key lists stopped finding `customer.code`, there is no second live call
available to notice.

### Option B — what the backend does with this body (owner decision 2026-10-09)

**Superseding the paragraph below.** The owner chose not to add a read-back step.
`register()` now treats a 2xx whose body identifies nobody **and** whose top-level
`status` is exactly `"success"` (trimmed, case-folded) as `SUCCESS` with no code and
no id. The lead is `REGISTERED`, `external_customer_code` is `NULL`, and the page
tells the customer to sign in at `khachhang.viporder.com.vn` with the phone and
password just chosen to see the code. The backend makes **one** call, to
`/register`; it never logs in as the customer. `{"status": "ok"}`, a `message` of
"success", or a bare `code: 200` remain `UNUSABLE_RESPONSE`
(`backend/tests/test_measured_register_success.py`).

Consequence to accept: VIPORDER's own lead row does not learn the customer code.
Staff who need it read it from the provider's system.

### What was missing before option B — kept as the record of the choice

**The backend has no read-back step.** `/register` returns no token, so `register()`
has nothing to authenticate `GET /auth/profile` with; a 2xx that identifies nobody
is classified `UNUSABLE_RESPONSE` and the lead stays `PENDING` — correct, and
truthful to the customer. Obtaining the code in production therefore requires the
registration flow to perform `POST /login` with the password the customer just
chose and then `GET /auth/profile`. That is a change to `register()` and to the
service around it — **not** a parser fix — and it is deliberately **not** made in
the change that records this measurement.

### Scope of this measurement, stated plainly

* **Measured:** the three statuses, content-types, byte counts, latencies and bodies
  above; the JSON path of the code and of the id; `_extract`'s output on each body;
  that `leads` was unchanged (8 rows before and after).
* **NOT measured:** the duplicate-registration response; the validation-error
  response; the auth-error response; the rate limit; the provider's timeout
  expectation; whether `/auth/register` differs from `/register`; the token's
  lifetime; whether the code or the id stay stable on later lookups; and whether
  `/register` behaves identically when sent **through our adapter** rather than by
  this tool. A success test exercises no failure path, and one call cannot establish
  stability.
* **Budget:** `~/.viporder-live-reg/POST_BUDGET_CONSUMED` was created on the staging
  host immediately before the call. The tool was run **once** and not re-run.
* **Evidence on disk, outside this repository:**
  `~/.viporder-live-reg/registration-evidence-20261009T162724Z.json` on the staging
  host — 2636 bytes, mode `600`. The password was generated **on that host**, stored
  at `~/.viporder-live-reg/password` (mode `600`, 20 characters), never printed,
  never committed, and sent nowhere but the provider.

## 11. What is not verified

Stated plainly, because a document that lists only what is known is the one that
gets trusted past its evidence.

* **The registration SUCCESS contract is now proven; the FAILURE contracts are
  not.** After a second owner-authorised one-shot test on **2026-10-09** (§10.8),
  the success path is measured end to end: the provider's own `POST /register`
  body (200, two keys, no code), and the `customer.code` / `customer.id` path in
  `POST /login` and `GET /auth/profile`. What is **still** unmeasured is every
  non-success shape — duplicate registration, validation error, auth error,
  provider-unavailable, rate limit — because a success test exercises none of
  them. The adapter's handling of those stays defensive for exactly that reason
  (`backend/app/providers/khaibao9610.py:1-12,356-452`), and the mock's responses
  are ours, not theirs (§8). Two further gaps, stated so they are not mistaken
  for solved: the 2026-10-09 call went **directly** to the provider rather than
  through our adapter, so the adapter's own wire format on a live success is
  still unexercised; and the backend has **no read-back step**, so the measured
  code is not something the running system can obtain yet. This paragraph said
  "no live registration `POST` has ever been made", then "the contract is still
  unproven"; each was true when written, and keeping either past its evidence
  would be worse than the gap it describes.
* **The live `GET`s were read-only reconnaissance, on 2026-10-02.** The two
  envelopes, the two 404 bodies, the full-code keyword and the 401 on the bare
  list are **measured facts of that moment** (§4.7), not a contract and not a
  guarantee. Upstream behaviour can change without notice, which is why a 401
  from the keyword endpoint is treated as a contract change (502) rather than as
  "the code does not exist" (`backend/app/tracking.py:20-25`;
  `backend/app/providers/khaibao9610.py:246-255`).
* **Tracking has not been exercised against the live API from this backend.** It
  CAN be — `MODE=http` + `ENABLE_REAL_CALLS=yes` + `ENABLE_REAL_REGISTRATION=no`
  is a supported configuration and `register()` refuses in it — but the two
  lookups have only ever been exercised against `httpx.MockTransport` and a fake
  provider (`backend/tests/test_tracking.py:1-6,162-174`). This paragraph said the
  lookups could not be enabled "without enabling real registration writes", which
  was true before the switches were split and is false now
  (`backend/app/providers/khaibao9610.py:556,570,588` — measured 2026-10-07).
* **The five UNKNOWN provider facts of §4.9**: the duplicate-registration
  response, the validation-error response, the rate limit, the provider's expected
  timeouts, and whether production requires authentication or an IP allowlist.
  Each is asserted nowhere in this document, and each corresponding parser is
  defensive because of it. §4.9's sixth item — the registration **success**
  schema — was answered on 2026-10-09 (§10.8) and is no longer in this list.
* **Anchor drift in this document.** Sections updated on 2026-10-02 — §2's
  tracking sequence, §3, §4.6–§4.9, §5, §6's status table and the status lines on
  items 1/2/5, §7, §8's tracking note, §9 and this section — carry anchors
  re-verified against the G04B tree. The bodies of the original §4.1–§4.5, the
  bodies of §6's items, and most of §8 were written before that merge;
  `backend/app/config.py` and `backend/app/providers/khaibao9610.py` have both
  grown since, so `file:line` anchors there land near — not on — the code they
  name. Follow the symbol name, not the line number.
* **`docs/REGISTRATION-FLOW.md` has not been updated for G04B.** Its §2.1/§2.2
  field table still describes the pre-G04B form — no `confirmPassword` control,
  no `acceptTerms` control, no email input — and its line anchors were written
  against an earlier revision, which its own §9 warns about. Measured example:
  its `index.html:473` anchor for the `full_name` control now lands in marketing
  copy, while the control is at `index.html:605`. For the current request
  contract, read §4.8 here.
* **`REGISTRATION-FLOW.md`'s citations were not re-derived.** Measured: 211
  fully-qualified `file:line` references (a regex count; the relative `:NN`
  anchors inside its tables are additional) all resolve to real files and
  in-range lines — but **in-range is not the same as correct**, as the
  `index.html:473` example shows. Re-deriving every one is a separate task, and
  fixing a subset would leave the document looking current while only part of it
  was.
