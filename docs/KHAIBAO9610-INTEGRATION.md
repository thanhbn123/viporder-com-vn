# KHAIBAO9610 registration integration

> ## Status: `BLOCKED_EXTERNAL` — provider mode is `MOCK`
>
> The real production registration contract has **not been supplied**.
> `KHAIBAO9610_MODE=mock` is the shipped default in every configuration file.
>
> **No file in this repository states an authoritative endpoint.** Exactly one
> live URL value exists anywhere in the tree: `https://apiviporder.com/frontend/v1`
> at `deploy/env.production.example:88`, also the settings default at
> `backend/app/config.py:39,83`, and a test fixture at
> `backend/tests/test_khaibao9610_provider.py:42,246`. That value is **an
> observation, not a contract**: it was read from a public frontend bundle
> during reconnaissance, and no provider has ever confirmed it. Every mention of
> it in this document carries that label.
>
> The site may be released and used, but real registration must be described as
> MOCK — never as working — until the checklist in §6 is answered.
>
> Tracking issue: **#4 — [KHAIBAO9610] Provide production registration API contract**

---

## 1. Why this boundary exists

The customer-code authority is an external system that VIPORDER does not
control. Its contract is unknown, its availability is unknown, and it may reject
or duplicate customers in ways nobody has documented.

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

---

## 3. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `KHAIBAO9610_MODE` | `mock` | `mock` or `http` |
| `KHAIBAO9610_ENABLE_REAL_CALLS` | `no` | **Second switch.** Real calls need this *and* `MODE=http` |
| `KHAIBAO9610_BASE_URL` | *(empty in `.env.example`; a URL in the in-code default and in the production template)* | Base URL of the registration API. The value present in this repository is **an observation, not a contract** — see §6, item 1 |
| `KHAIBAO9610_TIMEOUT_SECONDS` | `10` | Hard per-call timeout |
| `KHAIBAO9610_USER_AGENT` | browser-like | Required by the candidate upstream (see §5) |
| `MOCK_PROVIDER_BEHAVIOUR` | `success` | `success`/`duplicate`/`invalid`/`unavailable`/`timeout`/`error` |

Why two switches: a single `MODE=http` can be set by a typo, a copied config or
a stale `.env` on a server nobody is watching. Requiring a second, deliberately
named flag makes "we started sending real customer data to an unverified
endpoint" a thing someone had to *mean*. The guard itself is described in §7.

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
server-side behaviour or a business decision. See §6.

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

---

## 5. How the adapter is written today

`backend/app/providers/khaibao9610.py` implements the **candidate** contract
from §4.1 behind the `RegistrationProvider` interface. It is deliberately inert:

* it refuses to construct unless `KHAIBAO9610_MODE=http` **and**
  `KHAIBAO9610_ENABLE_REAL_CALLS=yes`;
* it never logs the request body, because the body carries a password;
* timeouts and connection errors become `UNAVAILABLE` with `retryable=True`,
  never an exception that could lose a lead;
* accent-insensitive matching is used for the duplicate message, because the
  upstream text is Vietnamese and may arrive with different diacritic
  normalisation.

When the real contract arrives, `provider` is the **only** file that should
change. `service.py`, the API, the lead model and the tests all speak in terms
of `ProviderStatus`, not in terms of HTTP.

---

## 6. Provider handoff checklist — the 20 things we still need

This is the list to take to the provider. Each item is a question, not a
statement: **we are not telling them what their API does, we are asking.** Where
this document seems to know something, it is either the owner's prior work or an
observation of a *public client* (§4) — neither is their contract.

Items 1–9 are the outbound call. Items 10–15 are the response shapes that decide
what we tell the customer. Items 16–20 are operational limits and requirements.

**How to answer.** "Unknown", "we do not support that" and "not applicable" are
complete and useful answers. One real example response is worth more than a
paragraph describing it. **Never paste a credential, token or password into this
document or into issue #4** — this repository is public and a leaked secret
cannot be un-published. Write down only *where* the secret is kept.

**How to read the one URL in this repository.**
`https://apiviporder.com/frontend/v1` appears in this tree
(`deploy/env.production.example:88`, `backend/app/config.py:39,83`,
`backend/tests/test_khaibao9610_provider.py:42,246`). It is **an observation,
not a contract**: read from a public frontend bundle during reconnaissance,
never confirmed by the provider. It must not be quoted to the provider as "our
endpoint", and it is not the answer to item 1.

### Item 1 — Exact registration endpoint

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

**Current assumption.** NONE — do not guess. The URL value in this repository is
**an observation, not a contract** (see the note above), so it cannot be treated
as the answer to this question.

### Item 2 — HTTP method

**What we need.** The HTTP method for the create-customer call.

**Why it matters.** `POST` is hard-coded (`backend/app/providers/khaibao9610.py:189`) and nothing
verifies it at runtime. A provider answering `405` would fall through the
catch-all at `backend/app/providers/khaibao9610.py:260-266` and be recorded as
`INVALID`: the customer would be told their *details* were rejected while the
lead is marked `FAILED` and cannot be retried
(`backend/app/services/registration.py:426-437`). A wrong method would look like
a data-quality problem for as long as nobody reads the raw status code.

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:188-194`.

**Current assumption.** ASSUMPTION — `POST`. Basis: the observed public client
called `en.post("register", ...)` (§4.2 — an observation of that client, not a
contract). Not confirmed by the provider.

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

**What we need.** The exact JSON field name, JSON type and required/optional
status of every field on the create-customer call — using the provider's own
names, not ours.

**Why it matters.** The body is built from fixed keys — `name`, `phone`, `email`,
`password`, `confirmPassword`, `acceptTerms`
(`backend/app/providers/khaibao9610.py:179-186`) — and those names come from an
unverified candidate contract that the module itself warns about
(`backend/app/providers/khaibao9610.py:1-9,20-24`). One wrong name (for example
if they want `full_name` rather than `name`) means a mandatory field arrives
absent or null, the call is rejected, and the rejection surfaces as a generic
`INVALID` (`backend/app/services/registration.py:426-437`) with nothing pointing
at the field. Two further specifics: `acceptTerms` is hard-coded `true`
(`backend/app/providers/khaibao9610.py:185`), so if the provider expects a real
consent value we are sending an assertion we cannot audit; and `province` and
`service_interest` exist on our request object but are
**never sent** (`backend/app/providers/base.py:40-41` versus `backend/app/providers/khaibao9610.py:179-186`).

**Where it lands in the code.** `backend/app/providers/khaibao9610.py:179-186` (the body);
`backend/app/providers/base.py:36-41` (our request shape);
`backend/app/schemas.py:76-83` (what the site collects).

**Current assumption.** ASSUMPTION — the body is exactly
`{name, phone, email, password, confirmPassword, acceptTerms}` and all six are
required. Basis: the owner's prior automation plus the observed public form,
which submitted all six; the adapter's own docstring labels this unverified
(`backend/app/providers/khaibao9610.py:1-9`). Which fields are required
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

**Current assumption.** ASSUMPTION — 2xx means success, and the body carries the
code and id somewhere. Both halves are unverified. The observed public client
read only `message` from this response and then redirected to login (§4.2, §4.3)
— it asserted nothing about the code, so it cannot settle this.

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

**Current assumption.** NONE — do not guess. Do not turn "the client ignores it"
into a field name. The candidate key names in the code are guesses and are
labelled as such (`backend/app/providers/khaibao9610.py:144-146` —
"best-effort").

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

**Current assumption.** NONE — do not guess. The key list in the adapter is a
best-effort guess and must not be mistaken for knowledge.

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
| `KHAIBAO9610_MODE=http` | `mock` | `backend/app/config.py:82`, `deploy/env.production.example:80` |
| `KHAIBAO9610_ENABLE_REAL_CALLS=yes` | `no` | `backend/app/config.py:85`, `deploy/env.production.example:86` |

Either one alone is refused, loudly, at construction time: the adapter raises
`ProviderConfigurationError` before it can send anything
(`backend/app/providers/khaibao9610.py:91-102`;
`backend/app/providers/base.py:80-85`). The provider factory only reaches the
live adapter when the mode is exactly `http`
(`backend/app/providers/factory.py:30-34`), and the mode is a `Literal` in
settings, so a typo fails at startup instead of silently selecting something
else (`backend/app/config.py:82`).

Why two switches: a single `MODE=http` can be set by a typo, a copied config, or
a stale `.env` on a server nobody is watching. A second, deliberately named flag
makes "we started sending real customer data to an unverified endpoint" a thing
someone had to *mean* (`backend/app/config.py:7-10`;
`deploy/env.production.example:82-85`).

**The live adapter has never made a call.** Nothing in this repository has ever
reached a registration endpoint of the provider. The shipped mode is `mock` in
every configuration file (`backend/app/config.py:82`,
`deploy/env.production.example:80`, `.env.example:31-33`), the real-call switch
defaults to off (`backend/app/config.py:85`) and is `no` in the production
template (`deploy/env.production.example:86`). Everything in §4 that reads like
a contract is either the owner's prior work or an observation of a *public
client* — none of it is a response this code received. The provider's own test
file reaches it only through a stub HTTP transport, never the network
(`backend/tests/test_khaibao9610_provider.py:48-52`), and
`docs/SECURITY.md:857-859` records the same fact independently: *"No live call
has ever been made, so none of the mapping … has been observed against the real
upstream."*

Scope of that statement, stated plainly: it rests on (a) the shipped
configuration in this repository, (b) the absence of any recorded live call, and
(c) that independent note in `docs/SECURITY.md`. It is **not** a claim about
what a running server's environment contains — verifying that means reading the
environment on that server, out of band. Do not report "never called" as a
measured fact about a deployment on the strength of this document alone.

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
`backend/app/config.py:89-91`; `.env.example:51-53`).

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

Only after §6 is answered and a staging endpoint exists.

1. **Record the answers** in this document, replacing §4.2. Keep the evidence.
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
* `KHAIBAO9610_MODE=http` enabled without §6 answered.
* A registration that returns success while the provider created nothing —
  this is why the response reports the provider's own status rather than assuming
  it.
* Retrying a non-idempotent call without checking (§6 item 18) — that is how
  duplicate customers are created.
