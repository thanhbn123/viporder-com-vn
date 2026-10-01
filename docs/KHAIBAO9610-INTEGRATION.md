# KHAIBAO9610 registration integration

> ## Status: `BLOCKED_EXTERNAL` — provider mode is `MOCK`
>
> The real production registration contract has **not been supplied**.
> `KHAIBAO9610_MODE=mock` is the shipped default in every configuration file.
>
> **Nothing in this repository hard-codes a guessed live endpoint.** The site may
> be released and used, but real registration must be described as MOCK — never
> as working — until the checklist in §6 is answered.
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
| `KHAIBAO9610_BASE_URL` | *(empty in template)* | Base URL of the registration API |
| `KHAIBAO9610_TIMEOUT_SECONDS` | `10` | Hard per-call timeout |
| `KHAIBAO9610_USER_AGENT` | browser-like | Required by the candidate upstream (see §5) |
| `KHAIBAO9610_API_KEY` | *(empty)* | Only if the provider issues one |
| `MOCK_PROVIDER_BEHAVIOUR` | `success` | `success`/`duplicate`/`invalid`/`unavailable`/`timeout`/`error` |

Why two switches: a single `MODE=http` can be set by a typo, a copied config or
a stale `.env` on a server nobody is watching. Requiring a second, deliberately
named flag makes "we started sending real customer data to an unverified
endpoint" a thing someone had to *mean*.

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

Source: a customer-code flow the owner runs today.

| Aspect | Observed |
|---|---|
| Register | `POST {base}/register` |
| Base | `https://apiviporder.com/frontend/v1` |
| Request body | `{name, phone, email, password, confirmPassword, acceptTerms}` |
| Login | `POST {base}/login` with `{account, password}` |
| Profile | `GET {base}/auth/profile` with `Authorization: Bearer <token>` |
| Token shape | `access_token` / `token` / `data.access_token` / `data.token` |
| Customer code | matches `TT\d+`, read from the profile response |
| Duplicate signal | HTTP **422** whose message matches `ton tai\|already\|taken\|da duoc su dung\|da dang ky\|da co\|exist` (accent-stripped, lowercased) |
| Transport gotcha | Cloudflare **Error 1010** rejects non-browser clients → a real browser `User-Agent` is required |
| Separate admin API | `https://apiviporder.com/api/v1` (Laravel + JWT), used with a read-only account |

### 4.2 Measured directly from the live client — 2026-10-01

On 2026-10-01 the public customer portal's own JavaScript bundle was fetched and
read. This is the code the real registration form runs, so it is a **measurement
of the shipped client**, not documentation and not a guess.

Method (read-only; no account was created and no POST was ever sent):

```
GET https://khachhang.viporder.com.vn/                      -> 200, 3,061 bytes
GET https://khachhang.viporder.com.vn/assets/index.e00575c6.js -> 200, 2,091,266 bytes
```

**Confirmed, verbatim from the bundle:**

| Fact | Value |
|---|---|
| API base | `baseURL: "https://apiviporder.com/frontend/v1/"` |
| Registration call | `await en.post("register", payload)` |
| Effective endpoint | **`POST https://apiviporder.com/frontend/v1/register`** |
| Request body | exactly `{ name, phone, email, password, confirmPassword, acceptTerms }` |
| Login | `en.post("login", ...)` |
| Profile | `en.get("auth/profile")` |
| Other client calls | `auth/logout`, `auth/profile-update`, `password/email`, `password/reset`, `/customers`, `/warehouse-imports`, `/package-sealings`, `/dashboard/*` |

Client-side rules the portal itself enforces (useful for matching validation, not
for trusting it):

* `password !== confirmPassword` → *"Mật khẩu xác nhận không khớp!"*
* phone must match `/^[0-9]{10,11}$/` → *"Số điện thoại không hợp lệ!"*
* password strength: length ≥ 8, lower + upper, digit, symbol → weak/medium/strong

### 4.3 The blocking question — now ANSWERED, and the answer is no

The register call site, verbatim (identifiers shortened):

```js
const { data: u } = await en.post("register", l);
ii.success(u.message || "Đăng ký thành công! Vui lòng đăng nhập.");
t.push("/login");
```

**The client reads only `u.message` from the registration response and then
redirects to `/login`.** It never reads a customer code — and `customer_code` /
`customerCode` appear **zero** times anywhere in the bundle.

So: **`POST /frontend/v1/register` does not hand back the customer code.** The
code is obtained only after authenticating, from the profile/customer endpoints.

This changes the question from "we don't know" to a definite constraint:

> A public site on `viporder.com.vn` that collects the customer's **own**
> password cannot then authenticate as that customer to read their code. The
> register endpoint alone is therefore **not sufficient** to complete PATH A.

The three options, and the decision is the owner's:

1. **The provider adds (or reveals) an endpoint that returns the code from the
   registration call itself.** Cleanest. This is question 5/6 in §6 and is now
   the *only* thing needed to unblock real integration.
2. **VIPORDER registers the customer, then the customer logs in at
   `khachhang.viporder.com.vn` and reads their own code there.** No credential
   handling on our side. The new site's success screen would then say "your
   account is created — sign in to see your customer code", which is truthful
   and needs nothing new from the provider.
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

`backend/app/providers/khaibao9610.py` implements the **candidate** contract from
§4.1 behind the `RegistrationProvider` interface. It is deliberately inert:

* it refuses to construct unless `KHAIBAO9610_MODE=http` **and**
  `KHAIBAO9610_ENABLE_REAL_CALLS=yes`;
* it never logs the request body, because the body carries a password;
* timeouts and connection errors become `UNAVAILABLE` with `retryable=True`,
  never an exception that could lose a lead;
* accent-insensitive matching is used for the duplicate message, because the
  upstream text is Vietnamese and may arrive with different diacritic
  normalisation.

When the real contract arrives, `provider` is the **only** file that should
change. `service.py`, the API, the lead model and the tests all speak in terms of
`ProviderStatus`, not in terms of HTTP.

---

## 6. What we need — the checklist

Please answer every line. **"Unknown" is a valid answer and is far more useful
than a guess.**

| # | Question | Why it matters |
|---|---|---|
| 1 | Full registration endpoint URL | **ANSWERED** by measurement — `POST https://apiviporder.com/frontend/v1/register` (§4.2) |
| 2 | HTTP method | **ANSWERED** — `POST` (§4.2) |
| 3 | Auth mechanism (none / API key / Basic / OAuth / JWT) and where the secret goes | adapter + secret handling |
| 4 | Exact request field names, types, required vs optional | **PARTLY ANSWERED** — body is exactly `{name, phone, email, password, confirmPassword, acceptTerms}`; which are *required server-side* is still unknown (§4.2) |
| 5 | Is a password required? Must the **customer's own** password be forwarded? | **ANSWERED: yes, required** — the endpoint takes `password` + `confirmPassword`. So the customer's own password must be forwarded; it is never stored (§4.2, §4.3) |
| 6 | Does the success response return the customer code **directly**? | **ANSWERED: not for this client** — it reads only `message` and redirects to login; `customer_code` appears zero times in the bundle. A server-side field may still exist that the client ignores — that is the one thing worth confirming (§4.3, §4.4) |
| 7 | Duplicate-customer response: status code + body | `DUPLICATE` mapping |
| 8 | Validation-failure response: status code + body | `INVALID` mapping |
| 9 | Is the call idempotent? Is retrying safe? | retry policy |
| 10 | Rate limits (requests/minute, burst) | limiter + backoff design |
| 11 | IP allow-list required for the production server? | firewall / deployment |
| 12 | Test/staging endpoint + credentials | safe integration testing **before** production |
| 13 | Consent basis: is VIPORDER permitted to register customers on their behalf from `viporder.com.vn`? | legal — a business decision, not an engineering one |

---

## 7. Turning the real integration on

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
   returns success but creates no provider-side customer is the failure this
   whole design exists to catch.

Until step 7 has been done with real evidence, the site must not claim that
registration produces customer codes.

---

## 8. What must never happen

* A credential in this repository, in an issue, a PR description, or a log.
* A password in the lead store, in logs, in traces, or in analytics.
* `KHAIBAO9610_MODE=http` enabled without §6 answered.
* A registration that returns success while the provider created nothing —
  this is why the response reports the provider's own status rather than
  assuming it.
* Retrying a non-idempotent call without checking (§6 question 9) — that is how
  duplicate customers are created.
