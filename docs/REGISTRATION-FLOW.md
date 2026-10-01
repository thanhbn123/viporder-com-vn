# Registration flow — VIPORDER.COM.VN

**Revision documented:** the tree at `origin/develop` `4d026def`
(this document is committed on top of it, without changing it)

> Values in the examples below are placeholders. Real tokens, lead IDs and
> customer codes are never reproduced in this repository.

The end-to-end journey from the browser to the provider and back, described from
the code at that revision. Every non-obvious claim carries a `file:line` anchor.

The single most important property is documented in §5: **the lead row is
committed before the provider is contacted**, so no provider failure mode can
lose a lead. That ordering is visible in the source, not inferred.

§8.3 records a replay defect that was found and measured here, and then fixed in
`4d026def` (PR #13). The defect and its reproduction are kept, because the fact
that no test covered it is the useful part of the record.

---

## 1. The client: `static/js/register.js`

`static/js/register.js` is 673 lines and is the only code that calls the
registration API (`static/js/register.js:1-673`; the form's `action` is the
no-JS fallback, see §2.4). It is loaded `defer` with the other two scripts
(`index.html:92-94`) and is written as an IIFE in `"use strict"` mode that
returns early if the form is absent, so it is safe on any page
(`static/js/register.js:25-26`, `:42-45`).

Its own header lists four guarantees it is responsible for
(`static/js/register.js:8-23`): duplicate-submit protection, honest outcomes per
status, a fixed portal host, and no `innerHTML`. Each is verifiable in the body.

### 1.1 Duplicate-submit protection

Three independent mechanisms:

1. **An in-flight latch.** `inFlight` is set by `setBusy(true)` before the
   request and cleared when the response settles
   (`static/js/register.js:53`, `:345-365`, `:610`, `:627`, `:637`, `:641`), and
   `onSubmit` returns immediately when it is set (`:580-582`).
2. **The button is actually disabled.** `setBusy` sets `submitBtn.disabled` and
   `aria-busy`, swaps the label to `Đang gửi thông tin…`, and adds an `is-busy`
   class to the form (`:345-365`).
3. **A completed form cannot be resubmitted.** On success the form is marked
   `data-completed="true"`, which both disables the button and makes `onSubmit`
   return early; the label becomes `Đã gửi đăng ký`
   (`:453-458`, `:350-351`, `:580-582`).

So a double click produces one request, not two — and even if it did produce two,
the idempotency key would make the second one a replay (§1.2, §7).

### 1.2 The `Idempotency-Key` lifecycle

The key is minted **per submitted phone number**, not merely per form session.
The header states the reasoning and it is the crux of the design
(`static/js/register.js:130-141`):

> "Reusing one key across two DIFFERENT bodies is the dangerous direction: if the
> backend caches responses by key alone, a corrected resubmit would replay the
> first response forever. Concretely, after a 409 'phone taken' a customer who
> fixes their number would keep being told it is taken and could never register
> — a silent, permanent dead end."
>
> "So the key is re-minted whenever the phone being submitted changes, and is kept
> for a retry of exactly the same details."

The mechanics, in `getIdempotencyKey(phone)` (`static/js/register.js:142-175`):

| Step | Behaviour | Anchor |
|---|---|---|
| 1 | If a key exists **and** was minted for a different phone → re-mint first | `:143-146` |
| 2 | If no key in memory, load key + its phone from `sessionStorage` | `:147-155` |
| 3 | Same guard across a page reload: stored key for a different phone → re-mint | `:157-160` |
| 4 | If still none, mint a new id | `:162-164` |
| 5 | Bind the key to the submitted phone and persist both | `:165-173` |

Persistence uses `sessionStorage` under `vo_idem_key_v1` and
`vo_idem_phone_v1` (`:31-33`), so the binding survives a page reload but not a
browser-session end. Every storage access is wrapped in `try/catch` with a
debug-level note, so a browser that blocks storage degrades to in-memory rather
than throwing (`:147-173`).

Minting uses `window.VIPOrderAnalytics.newId()` when available and otherwise a
local UUID-shaped fallback, so the key "never silently becomes undefined"
(`:84-98`).

**Rotation — exactly when the key is and is not reset.** `resetIdempotencyKey()`
clears both the in-memory pair and the storage pair (`:184-195`). It is called
from exactly three places, and the comment at `:177-183` states the rule:

| Event | Reset? | Anchor | Reason |
|---|---|---|---|
| `201` (registered) | **yes** | `:460` | the details are finished with |
| `409` (any) | **yes**, before any rendering | `:499-509` | the submitted details are a *new* attempt |
| phone field edited after an attempt | **yes** | `:660-668` | defence in depth: removes any window for a stale key |
| `202` (pending) | **no** | `:488-489` | "retrying is the same registration attempt, not a new one" |
| network error | **no** | `:529-538` | a retry of the same attempt must reuse the key |
| `429` | **no** | `:499-527` | same |
| `5xx` | **no** | `:499-527` | same |
| `422` | **no** | `:499-527` | same |

The `409` reset is placed deliberately first in `onFailed`, with the comment
"Done before any rendering on purpose — this is the correctness-critical side
effect of the whole function, so it must not be skippable by a failure further
down" (`:505-508`).

**One precision worth recording, because the comment is narrower than the code.**
The comment at `:177-183` justifies the `409` reset with the phone-taken case
("the phone is taken"). But the server returns `409` for three different reasons
— `DUPLICATE_PHONE`, `IDEMPOTENCY_KEY_REUSED` and `REGISTRATION_FAILED`
(`errors.py:26-30`) — and `onFailed` resets on the *status code*, not on the error
code (`:499-509`). So an `IDEMPOTENCY_KEY_REUSED` refusal also rotates the key.
That outcome is consistent
with the server's own wording ("Start a new registration",
`services/registration.py:207-208`) and is arguably the desired behaviour; the
comment simply does not mention it.

### 1.3 What the form sends

`readValues()` reads by `name` attribute (`static/js/register.js:199-209`) and
`buildPayload()` assembles the request body (`:542-561`):

```js
  function buildPayload(values) {
    var payload = {
      full_name: values.full_name,
      phone: normalisePhone(values.phone),
      password: values.password,
      /* The form does not collect an email yet — sent empty so the payload
       * keeps the documented shape. TODO(g02): add an email field. */
      email: "",
      province: values.province,
      consent: true,
      attribution: apiAttribution()
    };
    /* service_interest is a documented enum (transport|official_import|
     * customs|order). An empty string is not a member of it, so the key is
     * omitted entirely when the customer did not choose one. */
    if (values.service_interest) {
      payload.service_interest = values.service_interest;
    }
    return payload;
  }
```

Three deliberate properties, each matching a documented backend accommodation:

1. **`email: ""` is sent deliberately** (`:547-549`). The form has no email
   input, so the key is sent empty rather than omitted, and the backend
   normalises a blank string to `None` — see §3.2.
2. **`service_interest` is omitted entirely when unselected** (`:554-559`). `""`
   is not a member of the enum, so omitting the key is the contract; sending `""`
   would be a `422`.
3. **`consent: true` is sent as a literal** (`:551`), not as the checkbox's value.
   The checkbox is `required` in the markup (`index.html:539`) *and* validated in
   JS (`:228-230`), and the payload hard-codes `true` — so a request can only be
   sent when consent was actually given. The server independently requires it
   (`schemas.py:82`, `:118-123`).

`phone` is passed through `normalisePhone()` (strips spaces, dots, dashes and
brackets) so the key and the body can never drift (`:104-106`, `:545`, `:602`);
final canonicalisation remains server-side.

The request is a same-origin `fetch` with `credentials: "same-origin"` and three
headers (`static/js/register.js:612-620`):

```js
      window.fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "Idempotency-Key": attemptId
        },
        body: JSON.stringify(payload),
        credentials: "same-origin"
      })
```

`API_URL` is the same-origin relative path `/api/v1/registrations`
(`:28`), so no CORS is involved in the intended deployment.

The response is read as text and then parsed, so a non-JSON body cannot throw
(`:621-628`, `:108-118`); a `fetch` rejection or a missing `fetch` becomes the
network-error path (`:605-608`, `:636-639`).

### 1.4 Client-side validation — UX only

`validate()` returns an error map for `full_name` (present, ≥2 chars), `phone`
(Vietnamese shape), `password` (present, ≥8 chars) and `consent`
(`static/js/register.js:211-232`). The phone regex accepts mobile
`03/05/07/08/09` and landline `02x`, optionally written `+84` or `84`
(`:37-40`).

Errors are rendered as a `<span class="field-error">` appended after the control,
with `aria-invalid` and `aria-describedby` set, and focus is moved to the first
invalid field (`:265-284`, `:286-297`). The form sets `novalidate` **from
JavaScript** (`:648-651`) so that a browser with JavaScript disabled still gets
native HTML validation as a fallback.

The file states plainly that this is not the security boundary: "Client-side
validation is UX only — the server always validates again"
(`static/js/register.js:23`).

### 1.5 Honest outcomes

Each status gets its own state, and the network paths explicitly refuse to claim
that nothing happened (`static/js/register.js:14-16`):

| Status | Handler | What the customer sees |
|---|---|---|
| `201` | `onRegistered` (`:431-462`) | success message, the customer code, a portal link; form marked complete |
| `202` | `onPending` (`:464-491`) | "đã ghi nhận … nhưng chưa tạo được mã" — recorded, not yet created |
| `409` | `onFailed` (`:493-527`) | server wording, plus a field error on `phone` |
| `422` | `onFailed` | per-field errors from `error.fields` |
| `429` | `onFailed` | "quá nhiều lần … thử lại sau ít phút" (`:416-418`) |
| `5xx` | `onFailed` | "Thông tin bạn gửi **có thể đã được ghi nhận**" (`:419-427`) |
| network | `onNetworkError` (`:529-538`) | "có thể đã được ghi nhận phía máy chủ — vui lòng kiểm tra … trước khi gửi lại" |

The `202` path reports `leadPending` and the `201` path reports `leadSuccess` as
**separate** analytics events (`:434-444`, `:469-478`) — an unconfirmed lead is
never counted as a conversion. The `5xx` and network wording deliberately says the
lead *may* have been stored, because commit #1 may already have happened (§5).

### 1.6 The portal host is enforced client-side too

Whatever `login_url` the API returns is used only if it parses to
`https:` and exactly `khachhang.viporder.com.vn`; otherwise the hard-coded
constant is used (`static/js/register.js:322-335`), and the link is set with
`setAttribute` rather than `innerHTML` (`:341`). The comment frames it as a
business rule: "an API response may not redirect existing customers anywhere
else" (`:320-321`). This is a second, independent layer on top of the server-side
constant (§7 of `docs/SECURITY.md`).

---

## 2. The form as shipped, and the field-name mapping

### 2.1 What the form declares

`<form class="register-form" id="registerForm" method="post" action="/api/v1/registrations">`
(`index.html:461`).

| Control | `name` | Line |
|---|---|---|
| Họ và tên (text) | `full_name` | `index.html:473` |
| Số điện thoại (tel) | `phone` | `index.html:485` |
| Mật khẩu (password) | `password` | `index.html:498` |
| Tỉnh / Thành phố (text) | `province` | `index.html:511` |
| Nhu cầu (`<select>`) | `service` | `index.html:520` |
| Consent (checkbox) | `consent`, `value="true"` | `index.html:539` |

There is **no email input** (`index.html:469-545`).

### 2.2 Mapping to the API contract

The API request model is `RegistrationCreate` (`backend/app/schemas.py:73-83`).

| HTML form name | API field | How it maps | Anchor |
|---|---|---|---|
| `full_name` | `full_name` | direct | — |
| `phone` | `phone` | direct, normalised in JS then canonically server-side | `register.js:545`, `schemas.py:100-106` |
| `password` | `password` | direct | — |
| `province` | `province` | direct | — |
| `service` | `service_interest` | **renamed by `readValues()`** | `register.js:206` |
| `consent` | `consent` | read as `.checked`, then sent as literal `true` | `register.js:207`, `:551` |
| *(no control)* | `email` | **injected as `""`** by `buildPayload()`, normalised to `None` server-side | `register.js:549`, `schemas.py:85-90` |

The rename is a single line (`static/js/register.js:206`):

```js
    el = field("service"); out.service_interest = el ? String(el.value || "") : "";
```

This is worth stating explicitly because it is the one place where the wire field
name differs from the DOM name, and because it used to be a live defect: before
`register.js` existed, the select's `service` name had no mapping and
`RegistrationCreate`'s `extra="ignore"` (`schemas.py:74`) would have silently
dropped the choice. **That finding is now fixed** — see §9.

### 2.3 Blank-to-`None` normalisation

`RegistrationCreate` normalises blank strings to `None` for three fields before
any type validation runs (`backend/app/schemas.py:85-90`):

```python
    @field_validator("email", "province", "service_interest", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value
```

`mode="before"` is the load-bearing part: the check runs **before** `EmailStr`
sees the value. Without it, `"email": ""` would fail email validation and return
`422` for every registration — which is exactly why this exists, stated in
`backend/README.md:150-154` and in the test docstring at
`backend/tests/test_frontend_contract.py:12-13`. The client's deliberate
`email: ""` (`register.js:549`) depends on it.

The behaviour is pinned by tests rather than assumed:

| Input | Result | Test |
|---|---|---|
| `"email": ""` | accepted, stored `None` | `test_frontend_contract.py:60-66` |
| `"email": "   "` | accepted, stored `None` | `:69-73` |
| `"email": "\t\n "` | accepted | `:76-78` |
| `email` key absent | accepted, stored `None` | `:81-87` |
| `"email": "not-an-email"` | **`422`** | `:90-95` |
| `"email": "khach@example.com"` | accepted, stored | `:98-102` |
| `service_interest` absent | accepted, stored `None` | `:108-116` |
| `"service_interest": ""` | accepted, stored `None` | `:119-123` |
| `"service_interest": null` | accepted | `:126-128` |
| `"service_interest": "customs"` | accepted, stored | `:131-134` |
| `"service_interest": "not_a_real_service"` | **`422`** | `:136-139` |

The last row in each pair is the important one: normalising blank must not turn
validation *off*. `service_interest` is a `Literal` of four values
(`schemas.py:26`, `:81`); `""` is not a member, which is why the client omits the
key entirely (`register.js:554-559`).

`Attribution` does the same blank-to-`None` for its seven sub-fields
(`schemas.py:46-60`) and adds one real rule: `landing_page` must be an absolute
`http`/`https` URL with a netloc (`schemas.py:62-70`), because a relative value
like `/` is a `422` (`test_frontend_contract.py:261-268`).

### 2.4 The no-JavaScript fallback does not complete a registration

The form carries `method="post"` and a real `action` (`index.html:461`), which
looks like a working no-JS path. It is not one. With no enctype the browser sends
`application/x-www-form-urlencoded`, and FastAPI parses that into a mapping the
body model cannot extract from. Measured:

```
form-encoded POST -> 422 {'error': {'code': 'VALIDATION_ERROR',
  'message': 'One or more fields are invalid.',
  'fields': {'body': 'Input should be a valid dictionary or object to extract fields from'}}}
```

So a JavaScript-disabled submission reaches the API and is rejected with a raw
JSON `422` — a page of JSON in the browser, not a confirmation and not a
friendly error. The `novalidate` attribute is set from JS precisely so native
validation still works without JS (`register.js:648-651`), but native validation
passing is not the same as the submission succeeding. This is recorded as a
limitation, not a defect claim: nothing in this revision advertises a working
no-JS registration.

---

## 3. The server sequence

The whole journey lives in `RegistrationService.register()`
(`backend/app/services/registration.py:99-142`). Order matters and is exactly
this:

| # | Step | Anchor |
|---|---|---|
| 1 | Normalise the `Idempotency-Key` header (strip; blank → `None`; >128 chars → `400`) | `services/registration.py:103`, `:495-507` |
| 2 | Compute the SHA-256 request fingerprint (password excluded) | `:104`, `:516-547` |
| 3 | If a key was given and a lead already has it → `_replay()`, which compares fingerprints | `:106-109`, `:187-211` |
| 4 | If the phone already has a `REGISTERED` lead → raise `409 DUPLICATE_PHONE` | `:115-122` |
| 5 | Build the `Lead` with `registration_status=PENDING`, fingerprint, consent evidence | `:124`, `:213-240` |
| 6 | **`repository.create(lead)` — commit #1** | `:126` |
| 7 | Log that the lead exists | `:135-140` |
| 8 | Call the provider via `_attempt()` | `:142`, `:242-288` |
| 9 | Map the outcome and **commit #2** via `update_status()` | `:290-374` |

### 3.1 Validation happens before step 1

Step 0 is not in the service at all: FastAPI deserialises the body into
`RegistrationCreate` before the handler body runs
(`backend/app/routers/registrations.py:22`, `backend/app/schemas.py:73-123`). A
validation failure never reaches the service, so no lead row is created and no
provider is contacted. `backend/tests/test_security.py:107-109` asserts the row
count is zero after an oversized body, and the 422 paths assert no provider call.

Validation rules, all in `schemas.py`:

| Field | Rule | Anchor |
|---|---|---|
| `full_name` | required; 2–120 chars **after trim** | `:76`, `:92-98` |
| `phone` | required; normalised server-side, invalid → error | `:77`, `:100-106` |
| `password` | required; 8–200 chars | `:78`, `:108-116` |
| `email` | optional; blank → `None`; else `EmailStr` | `:79`, `:85-90` |
| `province` | optional; max 120 | `:80` |
| `service_interest` | optional; one of four literals | `:81` |
| `consent` | required; must be `True` | `:82`, `:118-123` |
| `attribution` | optional; each sub-field ≤300; `landing_page` absolute | `:83`, `:33-70` |

Phone normalisation is its own module (`backend/app/phone.py:29-67`): separators
are stripped, `+`/`00` prefixes handled, then exactly 9 national digits required
and the first digit must be in `235789` (`phone.py:22`, `:64-65`). The canonical
form is `+84XXXXXXXXX` (`phone.py:67`). This is why `0912 345 678` and
`+84912345678` are the same customer — `backend/tests/test_duplicate.py:73-78`
asserts four spellings all collide.

`password` is a pydantic `SecretStr` (`schemas.py:78`). The module docstring
explains the choice: `repr()`, `str()`, `model_dump()` and tracebacks all render
`**********` instead of the value, so it cannot escape into a log or an error
message by accident (`schemas.py:1-8`). The one place it is unwrapped is the
provider call (`services/registration.py:245`).

### 3.2 What the lead row captures at creation

`_build_lead()` (`services/registration.py:213-240`) writes, besides the customer
and attribution fields:

| Column | Value | Anchor |
|---|---|---|
| `registration_status` | `PENDING` | `:220` |
| `idempotency_key` | the normalised key, or `None` | `:234` |
| `request_fingerprint` | SHA-256 hex (64 chars) | `:235` |
| `consent_given_at` | `utcnow()` | `:236` |
| `consent_version` | `CONSENT_VERSION` = `"2026-02-v1"` | `:229`, `config.py:45` |
| `tracking_token` | `secrets.token_urlsafe(32)` | `:230` |
| `attempt_count` | `1` | `:231` |

Measured on a fresh lead:

```
consent_given_at: 2026-10-01 12:20:13.200869 | consent_version: 2026-02-v1
request_fingerprint len: 64 | idempotency_key: None
```

Consent evidence is recorded regardless of the provider outcome — asserted by
`backend/tests/test_consent.py:40-50` for the `202` path.

### 3.3 The two commits

**Commit #1** — `repository.create(lead)` (`services/registration.py:126`).
`create()` does `session.add` then `session.commit()` then `session.refresh`
(`backend/app/repositories/sqlalchemy_repo.py:35-45`). The row is durable when
this returns.

**Commit #2** — `repository.update_status(...)`, which commits
(`sqlalchemy_repo.py:92-101`). Which branch of `_apply_result` runs determines the
new status and the HTTP code (§7).

`attempt_count` starts at `1` on creation (`services/registration.py:239`) and is
incremented only on a retry (`:284`, `:308`, `:332`, `:346`, `:363` — all pass
`increment_attempt=is_retry`), so it counts *attempts*, not rows.

---

## 4. The request fingerprint

Before anything is looked up, the service hashes the canonical request body
(`services/registration.py:104`, `:516-547`). It covers `full_name`, `phone`,
`email`, `province`, `service_interest`, `consent` and the whole attribution
block, serialised deterministically:

```python
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

The password is excluded, and the docstring gives the reason
(`services/registration.py:519-522`): "a hash of a password is still
password-derived material, and storing it would hand an attacker a cheap
offline-cracking target for no benefit — the key is already scoped to a single
browser session."

The fingerprint is what makes an `Idempotency-Key` safe to treat as a *request*
identity, and the client's key lifecycle (§1.2) is the reason it is needed: the
client keeps one key across failed attempts, so "same key, corrected phone" is a
reachable path. Without the fingerprint the replay would hand customer B customer
A's lead id and customer code (`models.py:116-123`).

Full replay semantics are in §8.

---

## 5. The property that matters: the lead is committed before the provider call

### 5.1 Where it happens

`services/registration.py:124-142`:

```python
        lead = self._build_lead(payload, key=key, fingerprint=fingerprint)
        try:
            self.repository.create(lead)
        except DuplicateIdempotencyKeyError:
            # Another writer won the race for this key. Replay only if the body
            # matches; otherwise it is the same misuse, not a retry.
            existing = self.repository.get_by_idempotency_key(key) if key else None
            if existing is not None:
                return self._replay(existing, fingerprint)
            raise

        logger.info(
            "lead created lead_id=%s status=%s provider=%s",
            ...
        )

        return self._attempt(lead, payload)          # <-- provider call is here
```

`self.repository.create(lead)` at **`services/registration.py:126`** commits the
row (`sqlalchemy_repo.py:38`). `self._attempt(...)` at
**`services/registration.py:142`** is the only path that reaches
`self.provider.register(request)` at **`services/registration.py:259`**. There is
no code path in which the provider is contacted before the row is committed.

The row is created with `registration_status=PENDING`
(`services/registration.py:220`), which is also the column default
(`models.py:87-88`) — so even a process that died between the two commits leaves a
`PENDING` row, not a missing one.

### 5.2 Every provider failure mode keeps the lead

`_attempt()` wraps the provider call — **and the shape check on its result** — in
a broad `except Exception` (`services/registration.py:258-286`). The comment names
the reason: "provider bugs must not 5xx" (`:268`). The shape check being *inside*
the guard is deliberate (`:260-263`): "an adapter bug must surface as a retained
lead with a 202 — not as a 500 the customer cannot act on".

| Failure mode | Result | `last_error_code` | HTTP | Test |
|---|---|---|---|---|
| Provider returns `UNAVAILABLE` with an HTTP status | lead stays `PENDING` | from `result.error_code`, else `PROVIDER_UNAVAILABLE` | `202` | `test_lead_retention.py:77-94` |
| Provider times out (`http_status is None`, `retryable`) | lead stays `PENDING` | `PROVIDER_TIMEOUT` | `202` | `:97-104` |
| Provider raises | lead stays `PENDING` | `PROVIDER_ERROR` | `202` | `:107-117` |
| Provider returns a non-`RegistrationResult` | lead stays `PENDING` | `PROVIDER_CONTRACT_ERROR` | `202` | `services/registration.py:264-282` |
| Provider process crash mid-call | lead is already committed | — | *(no response)* | not tested; see §10 |

The error code now prefers the adapter's own `error_code`
(`services/registration.py:550-562`), which is why `PROVIDER_TIMEOUT` and
`PROVIDER_UNAVAILABLE` are no longer inferred from `http_status` alone: a refused
connection and a read timeout both arrive with `http_status = None`, and calling
both `PROVIDER_TIMEOUT` "makes a genuine outage undiagnosable from the stored
data" (`:552-556`). The adapters supply `PROVIDER_TIMEOUT`,
`PROVIDER_UNREACHABLE`, `PROVIDER_RATE_LIMITED` or `PROVIDER_UNAVAILABLE`
(`providers/khaibao9610.py:206`, `:220`, `:255-257`).

`_pending_body()` returns a `tracking_token` to the client
(`services/registration.py:405-414`). That token is generated at row creation as
`secrets.token_urlsafe(32)` (`:238`) and is compared with
`secrets.compare_digest` (`:510-513`), so it is not guessable and not
timing-comparable.

### 5.3 The residual window that is *not* covered

The ordering removes every failure mode *after* commit #1. It does not remove the
window between the HTTP request arriving and commit #1 completing: a crash or a
lost connection in that window loses the registration, because nothing was ever
written. The client sees a transport error and no lead exists.

Retrying is safe because the client keeps its `Idempotency-Key` on a network error
(`register.js:529-538`, §1.2) and the fingerprint is unchanged, so the retry is a
true replay. But the guarantee itself is narrower than it is sometimes stated:
**"a provider outage can never lose a lead"** — it is *not* "a registration can
never be lost".

---

## 6. The `PENDING` retry path

A `PENDING`/`FAILED` lead is recoverable by four distinct routes.

### 6.1 The customer polls with the tracking token

`GET /api/v1/registrations/{lead_id}` with header `X-Tracking-Token`
(`routers/registrations.py:37-51`). `get_status()` returns a flat `404` both when
the lead does not exist **and** when the token does not match — deliberately, so
the route never confirms that a lead id exists
(`services/registration.py:144-150`). The body never contains the password, phone
or email (`routers/registrations.py:48`, `services/registration.py:482-489`).

### 6.2 The same customer simply registers again

A `PENDING`/`FAILED` row is **not** a duplicate. `find_registered_by_phone()`
filters on `registration_status == REGISTERED`
(`sqlalchemy_repo.py:114-125`), so a phone with only `PENDING`/`FAILED` rows can
try again and gets a **new row**. Explicitly commented: "A phone whose earlier
lead is PENDING/FAILED may try again — that customer is not registered yet, and
refusing them would strand them permanently"
(`services/registration.py:111-114`). Asserted in
`test_duplicate.py:80-96`, including that the result is two rows and never a
merge.

Note the interaction with the client key: a new attempt after a `202` normally
reuses the same key *and the same body*, which is a replay (§8.2). To get a new
row the customer must change something that is in the fingerprint — which is
exactly what the `409`-driven rotation is for.

### 6.3 The operator retry route

`POST /api/v1/admin/registrations/{lead_id}/retry`
(`routers/admin.py:59-69`), gated by `X-Admin-Token` compared in constant time
(`admin.py:49-56`). When `ADMIN_API_TOKEN` is unset the route answers `404` — the
docstring states "Disabled means *absent*, not *closed*" (`admin.py:5-8`), and a
wrong token also gets `404` rather than `401` "because there is no reason to
confirm the route is real" (`admin.py:8-11`).

The retry **requires the password to be supplied again**:

```python
        if not password:
            logger.info("retry requested without a password lead_id=%s", lead.lead_id)
            return _retry_body(lead, retried=False, message=MESSAGE_RETRY_NEEDS_PASSWORD)
```
(`services/registration.py:167-169`)

This is a direct consequence of the no-password-storage constraint, and the
service docstring says so: "The password is not stored anywhere — that is a hard
constraint, not an oversight — so an operator retry has to supply it again.
Without it we cannot honestly talk to the provider, and inventing one would
create an account the customer cannot sign in to." (`services/registration.py:153-159`)

The route returns `200` with `"retried": false` and an explanatory message rather
than an error, so the caller learns *why* from the response
(`admin.py:36-42`, `services/registration.py:70-73`). On a successful retry,
`attempt_count` increments to 2 and the response carries `retried: true`
(`test_security.py:205-252`).

`list_pending()` exists on the repository as "the retry work queue"
(`repositories/base.py:65-73`, `sqlalchemy_repo.py:127-138`), but **nothing calls
it** at this revision — there is no scheduler or background worker. See §10.

---

## 7. The response contract, with real bodies

Every body below was produced by running the app in-process and is quoted
verbatim.

### 7.1 `201` — provider confirmed the account

Written by `_registered_body()` (`services/registration.py:380-402`) after the
`SUCCESS` branch (`:293-317`).

```json
{
  "lead_id": "<lead-uuid>",
  "registration_status": "REGISTERED",
  "external_customer_id": "<external-id>",
  "external_customer_code": "TT0000X",
  "message": "Registration complete. You can sign in to the customer portal.",
  "login_url": "https://khachhang.viporder.com.vn"
}
```

The external values are whatever the provider returned
(`:306-307`); the `mock-…`/`TT…` values above come from the default mock
provider (`providers/mock.py:124-132`). The DB row is set to `REGISTERED`, which
clears `last_error_code`/`last_error_message` (`sqlalchemy_repo.py:74-76`).

### 7.2 `202` — the lead was kept, the provider could not confirm

Written by `_pending_body()` (`services/registration.py:405-414`).

```json
{
  "lead_id": "<lead-uuid>",
  "registration_status": "PENDING",
  "external_customer_id": null,
  "external_customer_code": null,
  "tracking_token": "<tracking-token>",
  "message": "We received your registration but could not confirm it yet. Keep the tracking token to check the result; we will complete it shortly.",
  "login_url": "https://khachhang.viporder.com.vn"
}
```

`202` is returned by three distinct paths: provider `UNAVAILABLE`
(`:352-374`), provider raised or violated the contract (`:268-286`), and
idempotent replay of a non-`REGISTERED` lead (`:437-467` — see §8.3).

### 7.3 `409` — three different meanings, one status code

**(a) The phone is already registered.** Raised from two places, both with the
same code and message: the pre-check when a `REGISTERED` lead already exists
(`services/registration.py:115-122`), and the provider reporting a duplicate
(`:319-337`).

```json
{
  "error": {
    "code": "DUPLICATE_PHONE",
    "message": "This phone number is already registered. Please sign in to the customer portal."
  }
}
```

**The existing customer code is never returned.** In the provider-duplicate branch
the lead row is kept and marked `FAILED` with `last_error_code="DUPLICATE_PHONE"`,
and the comment states the reason: "We deliberately do not return the existing
customer code: knowing a phone number must not be enough to learn a customer's
code." (`services/registration.py:320-322`). Proven three ways in
`backend/tests/test_duplicate.py:57-70`: the existing code, the existing
`lead_id`, and even the string `external_customer_code` are asserted absent from
the response text.

The two paths differ in one observable way: the pre-check creates **no row**,
while the provider-reported duplicate creates a `FAILED` row that is retained
(`test_duplicate.py:114-126`).

**(b) The key was reused with a different body.** Raised by `_replay()`
(`services/registration.py:204-209`).

```json
{
  "error": {
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": "This Idempotency-Key was already used for a different registration. Start a new registration."
  }
}
```

The refusal leaks nothing about the earlier attempt —
`backend/tests/test_idempotency.py:138-146` asserts that neither customer's
customer code, lead id, nor the string `external_customer_code` appears, and that
the second attempt created no row.

Because both are `409`, a client keying only on the status code cannot tell them
apart; `error.code` is the discriminator. `register.js` resets the key on either
(§1.2).

**(c) A terminal reply could not be reproduced.** Raised by `_response_for()`
only for a row that predates the stored-response columns, whose cause is not one
the service recognises (`services/registration.py:478-479`). The code is
`REGISTRATION_FAILED`, which is **not** in the original contract
(`errors.py:29-30`):

```json
{
  "error": {
    "code": "REGISTRATION_FAILED",
    "message": "This registration did not complete. Please start a new registration."
  }
}
```

It fails closed rather than guessing: the reply never claims success, never
claims pending, and never invents a cause-specific code the client would act on.
See §8.3 for when it is reachable — it is a shape this service never writes.

### 7.4 `413` — the body exceeds `MAX_REQUEST_BYTES`

Produced by the middleware, not by a route (`backend/app/middleware.py:112-127`):

```json
{
  "error": {
    "code": "PAYLOAD_TOO_LARGE",
    "message": "Request body must not exceed 2048 bytes."
  }
}
```

`2048` is the configured limit in that run; the default is 65536
(`config.py:104`). The message interpolates the real configured value
(`middleware.py:123`). No lead is created — asserted at
`test_security.py:107-109`.

### 7.5 `422` — validation failed

Two different bodies share the status code.

**(a) Schema validation** (`backend/app/errors.py:128-139`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "One or more fields are invalid.",
    "fields": {
      "consent": "Value error, consent must be true to register"
    }
  },
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "consent"],
      "msg": "Value error, consent must be true to register"
    }
  ]
}
```

The `detail` list is the standard FastAPI shape **minus every echoed input
value** — `sanitised_errors()` keeps only `type`, `loc` and `msg`
(`errors.py:97-108`). This is why a rejected password does not come back in the
body (`test_validation.py:44-49`). `register.js` renders `error.fields` per
control (`register.js:511-515`).

**(b) The provider rejected the details** (`services/registration.py:339-350`):

```json
{
  "error": {
    "code": "PROVIDER_INVALID",
    "message": "The registration service rejected these details. Please check them and try again."
  }
}
```

Note the difference in consequence: (a) creates no row, (b) has **already created
a lead row** — commit #1 happened before the provider was called — and marks it
`FAILED` with `last_error_code="PROVIDER_INVALID"` (`:341-349`).

### 7.6 `429` — rate limited

Produced by `RateLimitMiddleware` (`backend/app/middleware.py:186-199`):

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many registration attempts. Please try again later."
  }
}
```

plus a `Retry-After` header in whole seconds, computed as
`max(1, int(window - (now - hits[0])) + 1)` (`middleware.py:172`, `:196`) — so it
is always ≥1. Measured on a fresh bucket: `Retry-After: 600`. Asserted at
`test_security.py:115-128`, which also asserts the blocked attempt created no
lead. The limiter covers `POST` on exactly `/api/v1/registrations` and nothing
else (`middleware.py:178-179`, `:25`).

`register.js` does **not** rotate the key on a `429` (§1.2), so the customer's
retry after the window is the same idempotent attempt.

### 7.7 Other codes

| Code | HTTP | When | Anchor |
|---|---|---|---|
| `INVALID_REQUEST` | `400` | body is not valid JSON; or `Idempotency-Key` longer than 128 chars | `errors.py:121-127`, `services/registration.py:501-506` |
| `NOT_FOUND` | `404` | lead unknown, token wrong, admin route disabled, admin token wrong | `services/registration.py:148-149`, `:153-154`, `admin.py:50-55` |
| `METHOD_NOT_ALLOWED` | `405` | wrong verb | `errors.py:34`, `:143-155` |
| `INTERNAL_ERROR` | `500` | unexpected exception; never a stack trace | `main.py:132-148` |

---

## 8. Idempotency: the key and the fingerprint

`Idempotency-Key` is a request-identity claim, not a cache key. It is honoured
only when the body fingerprint matches.

### 8.1 Normalisation

`_normalise_idempotency_key()` (`services/registration.py:495-507`):

| Input | Result |
|---|---|
| header absent | `None` |
| `"   "` (whitespace only) | `None` — a blank key is ignored, not stored |
| `"k" * 200` (over 128 chars) | raises `ApiError(400, INVALID_REQUEST, ...)` |
| any other non-blank string | stored as given, stripped |

Bound: `MAX_IDEMPOTENCY_KEY_LENGTH = 128` (`:53`), matching the
`String(128)` column (`models.py:113`) and its unique index
(`models.py:184`). Asserted at `test_idempotency.py:104-113`.

### 8.2 Replay decision table

`register()` consults the key first (`:106-109`), `_replay()` compares the
fingerprint before any reply is produced (`:187-211`), and `_response_for()`
decides *which* reply (`:437-479`). Combining all three:

| Stored state for this key | Fingerprint | Outcome | Anchor |
|---|---|---|---|
| no row | — | proceed to create | `:106-109` |
| row, any status | differs | `409 IDEMPOTENCY_KEY_REUSED`, no reply produced | `:199-208` |
| row, any status | stored value is `NULL` | `409 IDEMPOTENCY_KEY_REUSED` (fail closed) | `:196-198` |
| row, `PENDING` | matches | `202` + a pending body built from **current state** | `:452-453` |
| row, terminal, stored pair present | matches | the **stored status and body, verbatim** | `:455-456` |
| row, terminal, no stored pair, `REGISTERED` | matches | `201` derived from the row | `:465-471` |
| row, terminal, no stored pair, `FAILED` with a recognised cause | matches | `409 DUPLICATE_PHONE` or `422 PROVIDER_INVALID` | `:473-476` |
| row, terminal, no stored pair, anything else | matches | `409 REGISTRATION_FAILED` (fail closed) | `:478-479` |
| concurrent create lost the race | matches | replay of the winner | `:127-133` |

The provider is **not** called again on a replay — asserted with a counting
provider at `test_idempotency.py:45-71`, which proves `provider.calls == 1` after
two identical requests.

### 8.3 The `FAILED`-replay defect — fixed in `4d026def` (PR #13)

**Status: fixed.** The defect is kept in full, because its reproduction and the
fact that no test covered it are the most useful part of the record.

**What was wrong.** `_response_for()` reconstructed the reply from the row's
current state and mapped *any* non-`REGISTERED` status to `202` +
`_pending_body()`. `FAILED` is a non-`REGISTERED` status, so the **first**
identical request returned `409`, while a **replay** of the same body under the
same key returned `202` with `registration_status: "FAILED"` and a `message`
saying the registration "could not be confirmed yet" — untrue of a lead the
provider had already rejected.

Reproduced at `df9b84ea`:

```
=== FINDING 1: FAILED-replay (provider duplicate) — still reproduce? ===
first  -> 409 DUPLICATE_PHONE
replay -> 202 | registration_status: FAILED | code: None
         message: We received your registration but could not confirm it yet. Keep the tracking to
         db row: FAILED DUPLICATE_PHONE | fp: e4f278250331
```

The stored fingerprint matched the resubmitted body, so the fingerprint check
from PR #10 did **not** catch it: `_replay()` passed, and the contradiction was
introduced later, by `_response_for()`.

Three consequences, all removed by the fix:

1. **The status code changed between two identical requests** — `409` then `202`.
   A client keying behaviour off the status code saw two different answers for
   the same key and the same body.
2. **The status code contradicted the body.** `202` is documented as "provider
   unavailable, lead kept" (`backend/README.md:126`), but the provider had been
   reachable and had explicitly rejected the registration.
3. **The operator-facing message was misleading** on a lead whose
   `last_error_code` is `DUPLICATE_PHONE`.

`register.js` handled it benignly by accident — it branches on `202` into
`onPending`, which keeps the key and shows "chưa tạo được mã khách hàng ngay"
(`register.js:464-491`) — so the customer was told something approximately true
rather than being shown a contradiction. That accident is not a reason the defect
was acceptable.

**No test covered it.** `backend/tests/test_idempotency.py` exercises replay of
`201` (`:184-191`), replay of `202` (`:74-87`, `:194-204`), and mismatched-body
refusals (`:119-217`) — never a replay where the first attempt ended `FAILED`. It
could therefore have regressed silently, which is why it was written down rather
than left as a known-bad path.

**How it is fixed.** The reply is now *stored* and returned verbatim instead of
being reconstructed. Two columns hold it — `response_status`
(`models.py:153`) and `response_body` (`models.py:154`) — written whenever an
attempt reaches a terminal outcome:

| Outcome | Stored | Anchor |
|---|---|---|
| `SUCCESS` | `201` + the registered body, built once and both stored and returned | `:309-317` |
| provider `DUPLICATE` | `409` + the `DUPLICATE_PHONE` envelope | `:326-337` |
| provider `INVALID` | `422` + the `PROVIDER_INVALID` envelope | `:340-350` |
| `UNAVAILABLE` (stays `PENDING`) | **cleared** to `None`/`None` — PENDING is not terminal | `:360-364` |

`_response_for()` resolves in a fixed order (`:437-479`):

1. `PENDING` → `202` from **current state**, always (`:452-453`). There is no
   stored reply to honour, and a later admin retry legitimately changes it.
2. a stored pair → returned **verbatim** (`:455-456`). This is the case that was
   broken; the reconstruction is what turned a `409` into a `202`.
3. only for a row that predates the columns is a reply **derived**
   (`:465-479`): `REGISTERED` → `201` built from the row; a `FAILED` row whose
   `last_error_code` is `DUPLICATE_PHONE` → `409`, or `PROVIDER_INVALID` → `422`
   (table at `:431-435`, used at `:473-476`); anything else **fails closed**
   (`:478-479`).

`REGISTRATION_FAILED` is an additive code that is **not** in the original
contract (`errors.py:29-30`). It is reachable only for a row shape this service
never writes — a terminal lead with no stored pair and an unrecognised cause,
i.e. a row written before migration `0003_stored_response` or by something other
than this service. Failing closed there is deliberate: it never claims success,
never claims pending, and never invents a cause-specific code the client would
act on (`:478`).

The behaviour is now pinned by `backend/tests/test_replay_fidelity.py`: every
outcome replays with its own status code and a byte-identical body
(`:120-140`), a terminal outcome stores its reply (`:146-175`), a later admin
success replaces a stored failure so the replay becomes `201` (`:188-236`), and
each legacy-derivation branch is covered including the fail-closed one
(`:254-304`).

### 8.4 Concurrency

Two simultaneous requests with the same key are resolved by the database, not by
a read-then-write check. `create()` catches `IntegrityError`, rolls back, and
raises `DuplicateIdempotencyKeyError` when the conflict is on the idempotency
index (`sqlalchemy_repo.py:35-45`, `:141-143`); the service then applies the same
fingerprint check and either replays or re-raises
(`services/registration.py:127-133`). The detection is a substring match on the
driver's error text — `"idempotency" in message or
"uq_leads_idempotency_key" in message` (`sqlalchemy_repo.py:141-143`) — so it is
tied to the index name and to SQLite/psycopg error wording.

### 8.5 Client and server responsibilities

| Concern | Client (`register.js`) | Server |
|---|---|---|
| Mint the key | yes, per submitted phone (`:142-175`) | — |
| Rotate the key | on `201`, on `409`, on phone edit (`:460`, `:508`, `:665`) | — |
| Persist the key | `sessionStorage` (`:166-173`) | column `idempotency_key` (`models.py:113`) |
| Reject a reused key with a different body | — | `409 IDEMPOTENCY_KEY_REUSED` (`:199-208`) |
| Expiry | none — lives until rotated or the session ends | none — a stored key is honoured indefinitely |

The absence of any expiry on either side means a client that reuses a key across
two genuinely different customers would get a `409` rather than the first
customer's data, which is the safe direction. The 128-character bound is the
server's only limit on the key itself.

---

## 9. Claims from the previous revision of this document that were stale

This document previously described the revision `fcecb00`. Four of its findings
no longer apply. They are listed here rather than silently removed, because a
reader who saw the earlier version needs to know which parts changed.

A fifth item went stale *after* this document was regenerated against `df9b84ea`:
the `FAILED`-replay defect it recorded as an open finding was fixed in `4d026def`
(PR #13). §8.3 keeps the defect and its reproduction and now states the fix.

| Previous claim | Status now | What replaced it |
|---|---|---|
| "`static/js/register.js` does not exist at this revision, so there is no client-side validation, no `Idempotency-Key`, and no duplicate-submit protection" | **STALE** | The file exists (673 lines) and implements all three. §1 replaces the gap with the real lifecycle, including the exact rotation rules. |
| "The shipped form would `422` if wired as-is: the consent checkbox has no `name`, and the select is named `service` not `service_interest`" | **STALE — fixed** | `index.html:539` now has `name="consent" value="true"`, and `register.js:206` maps `service` → `service_interest`. §2.2 tables the real mapping; §2.4 records the *new*, different limitation (the no-JS fallback returns a raw `422`). |
| "No test parses the real HTML form; `test_frontend_contract.py`'s docstring claims provenance from `app.js` that `app.js` does not support" | **partly STALE** | `app.js` no longer handles the form at all (`app.js:1-14`), and the client payload shape is now expressed in `register.js:542-561`. The observation that no test parses `index.html` as HTML still holds — see §10. |
| "`static/js/app.js` shows a placeholder and calls `preventDefault` on submit" | **STALE** | `app.js` is now page chrome (footer year, mobile nav, contact/service tracking) and does not touch the form (`app.js:1-14`; its only `preventDefault` is at `:140`, in the nav toggle). |

Everything else in the previous version — the persist-before-provider ordering,
the two commits, the retry paths, the response bodies, the `PENDING`/`FAILED`
recovery routes — was re-verified against `df9b84ea` and still holds, with line
numbers updated throughout.

---

## 10. What is not verified

1. **No browser ever ran `register.js`.** The client behaviour in §1 is read from
   the source. No test in `backend/tests/` loads or executes JavaScript, and
   nothing in this session drove a real browser. The rotation rules, the
   `sessionStorage` guard across reloads, and the `data-completed` latch are
   **descriptions of the code, not observations of it running**.
2. **No test parses `index.html`.** The field mapping in §2.2 was established by
   reading the markup and the controller. `tools/check_site.py` validates HTML
   structure, accessibility and link rules, but it does not assert that a form
   control's `name` matches what the controller reads. A rename on either side
   would not fail the build.
3. **The §8.3 defect was measured ad hoc, and is now covered by the suite.** The
   reproduction in §8.3 is the original probe output against `df9b84ea`. It no
   longer reproduces, and the fixed behaviour is pinned by
   `backend/tests/test_replay_fidelity.py` (`:120-140`, `:146-175`, `:254-304`).
4. **No live provider call was ever made.** The default is `mock`
   (`config.py:82`) and the live adapter refuses to construct without both
   switches (`khaibao9610.py:91-102`). Every provider outcome in §5.2 and §7 comes
   from the mock provider or an injected fake.
5. **No PostgreSQL run.** Every test uses SQLite under pytest's `tmp_path`
   (`conftest.py:5-9`). The `FAILED`-row behaviour, the partial-index race, and
   the `IntegrityError` text matching were all exercised on SQLite only.
   psycopg's error wording differs, and `_is_idempotency_conflict()` matching is
   driver-wording-dependent (`sqlalchemy_repo.py:141-143`).
6. **The crash-between-requests window (§5.3) was not tested.** There is no test
   that kills a process between commit #1 and the provider call. The claim that
   the row survives rests on reading `create()`'s commit.
7. **`list_pending()` is never called in application code.** The repository method
   exists and is tested, but there is no scheduler, cron, worker or CLI that
   drives it. A `PENDING` lead is only completed if a customer retries or an
   operator calls the admin route.
8. **The multi-worker case was never run.** Both deployment paths start a single
   uvicorn worker (`deploy/systemd/viporder-web.service:51`), so the cross-worker
   race is not reachable as deployed. It would become reachable the moment
   `--workers` is raised, and the unique index on `idempotency_key`
   (`models.py:166`) is what would make it safe — the database doing the work, not
   the application. That race was not exercised, because every test runs in one
   process.
9. **The final response bodies in §7 come from the mock provider.** No body in
   this document was observed from `apiviporder.com`. The real upstream's response
   shape, error strings and duplicate phrasing are unverified
   (`khaibao9610.py:1-9`).
10. **The 328-test suite passes at this revision** (`python -m pytest -q` →
    `328 passed, 1 warning`), but a passing suite is not a status claim, and it
    does not cover the client at all.
