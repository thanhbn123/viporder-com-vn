# DNS / domain cutover — VIPORDER.COM.VN

> **Status: PLAN ONLY. Nothing in this document has been executed.**
> No DNS record was changed. No server was touched. The live host was only
> *read* over the public internet (DNS queries, `GET`/`HEAD`, TLS handshakes).

This document has two halves, and they must not be confused:

* **MEASURED** — output of a command run on 2026-10-02, quoted verbatim below.
* **PROPOSED** — a change that has *not* been made and is *not* a fact. Every
  proposal is labelled.

Where a value is not measurable from the public internet (the DNS provider's
control panel, the actual vhost file text on the server), this document says so
and lists it as **owner input required** rather than guessing.

---

## 1. Why this document exists

`viporder.com.vn` today does **not** serve the public website in
`index.html` (index.html:1). It redirects away from it. Moving the site there is
therefore not a DNS-only operation: it replaces the web server software on the
host that also serves the existing customer portal
(`docs/DEPLOYMENT.md:22`, `docs/PROJECT-STATUS.md:17`).

The project's own records say nothing is deployed — `docs/DEPLOYMENT.md:3`,
`docs/PROJECT-STATUS.md:73`, and blocker 2 at `docs/PROJECT-STATUS.md:178`
("DNS + VPS access for `viporder.com.vn` | Owner-held | Nothing can be
deployed. Prep only."). That statement is true of **this repository's nginx
stack**. It is not true of the domain: a live Apache host is already answering
on `viporder.com.vn`, and this document measures it.

---

## 2. BEFORE — raw measurements

All commands below were run on **2026-10-02, 07:28–07:32 UTC**, from a single
workstation, with recursion against the default resolver unless stated.

### 2.1 DNS

```
$ dig +noall +answer +comments A viporder.com.vn
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 7556
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 4095
;; ANSWER SECTION:
viporder.com.vn.	600	IN	A	103.159.50.70
```

```
$ dig +noall +answer +comments AAAA viporder.com.vn
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 49157
;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 1, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 512
```
No ANSWER section: the apex has no AAAA.

```
$ dig +noall +answer +comments CNAME viporder.com.vn
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 32146
;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 1, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 512
```
No CNAME at the apex, which is correct — an apex may not be a CNAME.

```
$ dig +noall +answer A www.viporder.com.vn
;; ANSWER SECTION:
www.viporder.com.vn.	3600	IN	CNAME	viporder.com.vn.
viporder.com.vn.	3600	IN	A	103.159.50.70

$ dig +noall +answer CNAME www.viporder.com.vn
;; flags: qr aa rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1
;; ANSWER SECTION:
www.viporder.com.vn.	3600	IN	CNAME	viporder.com.vn.
```

```
$ dig +noall +answer A khachhang.viporder.com.vn
;; ANSWER SECTION:
khachhang.viporder.com.vn. 600	IN	A	103.159.50.70
```

```
$ dig +noall +answer A apiviporder.com
;; ANSWER SECTION:
apiviporder.com.	600	IN	A	8.217.152.4
```

Authoritative servers, measured:

```
$ dig +noall +answer NS viporder.com.vn
viporder.com.vn.	600	IN	NS	ns3.zonedns.vn.
viporder.com.vn.	600	IN	NS	ns2.zonedns.vn.
viporder.com.vn.	600	IN	NS	ns1.zonedns.vn.
viporder.com.vn.	600	IN	NS	ns4.zonedns.vn.

$ dig +noall +answer SOA viporder.com.vn
viporder.com.vn.	600	IN	SOA	ns1.zonedns.vn. root.viporder.com.vn. 2026092902 1200 600 1209600 3600

$ dig +noall +answer NS apiviporder.com
apiviporder.com.	86400	IN	NS	ns8.alidns.com.
apiviporder.com.	86400	IN	NS	ns7.alidns.com.

$ dig +noall +answer SOA apiviporder.com
apiviporder.com.	600	IN	SOA	ns7.alidns.com. hostmaster.hichina.com. 2026082815 3600 1200 86400 600
```

**Summary of what was measured** (this table is transcription, not inference):

| Name | Type | Value | TTL |
|---|---|---|---|
| `viporder.com.vn` | A | `103.159.50.70` | 600 |
| `viporder.com.vn` | AAAA | none (NODATA) | — |
| `www.viporder.com.vn` | CNAME | `viporder.com.vn.` | 3600 |
| `khachhang.viporder.com.vn` | A | `103.159.50.70` | 600 |
| `khachhang.viporder.com.vn` | AAAA | none (NODATA) | — |
| `apiviporder.com` | A | `8.217.152.4` | 600 |

**A note on the TTL column, because it is the value most likely to be misread.**
A TTL in a `dig` answer is the *remaining* lifetime of a cached record, not a
fixed property of the zone. Re-querying `www.viporder.com.vn` minutes later
returned the same CNAME with **600** where the first query returned **3600**.
Both are honest readings of the same record at different moments. The table
above is what the resolver said at the time quoted, and no TTL in it should be
treated as more precise than that. What matters for §6.2 is the *configured*
value, which is only knowable in the control panel — owner input, §4.1.

**Two facts that matter more than the rest:**

1. `viporder.com.vn`, `www.viporder.com.vn` and `khachhang.viporder.com.vn` all
   resolve to **the same address** `103.159.50.70`. One host serves the public
   apex, the www alias and the existing customer portal. This is measured, and
   it is the reason §6.4 is the highest-risk section of this plan.
2. `apiviporder.com` resolves to a **different address** (`8.217.152.4`), is in a
   **different zone**, on a **different set of nameservers** (`alidns.com`, SOA
   contact `hostmaster.hichina.com`). It is not part of the
   `viporder.com.vn` cutover at all. See §2.5.

### 2.2 HTTP behaviour

```
$ curl -sS -I https://viporder.com.vn/
HTTP/2 302
date: Fri, 02 Oct 2026 07:28:08 GMT
server: Apache/2
location: https://khachhang.viporder.com.vn
content-type: text/html; charset=iso-8859-1

$ curl -sS -o /dev/null -D - http://viporder.com.vn/
HTTP/1.1 301 Moved Permanently
Date: Fri, 02 Oct 2026 07:28:08 GMT
Server: Apache/2
Location: https://viporder.com.vn/
Content-Length: 232
Content-Type: text/html; charset=iso-8859-1
```

```
$ curl -sS -I https://www.viporder.com.vn/
HTTP/2 302
date: Fri, 02 Oct 2026 07:28:08 GMT
server: Apache/2
location: https://khachhang.viporder.com.vn
content-type: text/html; charset=iso-8859-1

$ curl -sS -o /dev/null -D - http://www.viporder.com.vn/
HTTP/1.1 301 Moved Permanently
Date: Fri, 02 Oct 2026 07:28:08 GMT
Server: Apache/2
Location: https://www.viporder.com.vn/
Content-Length: 236
```

```
$ curl -sS -I https://khachhang.viporder.com.vn/
HTTP/2 200
date: Fri, 02 Oct 2026 07:28:09 GMT
server: Apache/2
last-modified: Tue, 29 Sep 2026 06:38:56 GMT
etag: "bf5-65c996fd53352"
accept-ranges: bytes
content-length: 3061
vary: Accept-Encoding,User-Agent
content-type: text/html
```

```
$ curl -sS -I https://apiviporder.com/
HTTP/1.1 200 OK
Date: Fri, 02 Oct 2026 07:30:59 GMT
Server: Apache/2.4.6 (CentOS) OpenSSL/1.0.2k-fips PHP/5.6.40
X-Powered-By: PHP/8.1.29
Cache-Control: no-cache, private
Set-Cookie: XSRF-TOKEN=…; expires=Fri, 02-Oct-2026 09:30:59 GMT; Max-Age=7200; path=/; samesite=lax
Set-Cookie: viporder_session=…; expires=Fri, 02-Oct-2026 09:30:59 GMT; Max-Age=7200; path=/; httponly; samesite=lax
Content-Type: application/json
```

The body at `https://apiviporder.com/` is:

```
{"message":"Test event fired"}
```

**Does every path on `viporder.com.vn` redirect?** Measured path by path:

```
/                                  status=302  location=https://khachhang.viporder.com.vn
/robots.txt                        status=302  location=https://khachhang.viporder.com.vnrobots.txt
/sitemap.xml                       status=302  location=https://khachhang.viporder.com.vnsitemap.xml
/index.html                        status=302  location=https://khachhang.viporder.com.vnindex.html
/api/v1/registrations              status=302  location=https://khachhang.viporder.com.vnapi/v1/registrations
/nonexistent-xyz                   status=302  location=https://khachhang.viporder.com.vnnonexistent-xyz
/static/img/og-viporder.png        status=302  location=https://khachhang.viporder.com.vnstatic/img/og-viporder.png
/404.html                          status=302  location=https://khachhang.viporder.com.vn404.html
```

Yes — every path redirects, including `/robots.txt`, `/sitemap.xml` and the
registration endpoint.

**And the redirect target is malformed.** The `Location` for a non-root path is
the target string concatenated with the remaining path **with no `/`
separator**. Following such a target fails at DNS:

```
$ curl -sS -o /dev/null -D - "https://khachhang.viporder.com.vnrobots.txt"
curl: (6) Could not resolve host: khachhang.viporder.com.vnrobots.txt

$ dig +noall +answer +comments A khachhang.viporder.com.vnrobots.txt
;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 33567
```

This is a description of **measured behaviour**. The exact server-side rule text
that produces it is **not measurable from the public internet** — see §4.

Note what this means for the two documents the new site ships: today
`https://viporder.com.vn/robots.txt` and `https://viporder.com.vn/sitemap.xml`
do not return their file contents. They redirect to hostnames that do not
exist. The repository's `robots.txt:19` sitemap declaration and
`sitemap.xml:20` `<loc>` are correct as files and unreachable as URLs.

Security headers on the live host, checked for completeness:

```
$ curl -sSI https://viporder.com.vn/ | grep -iE 'strict-transport|content-security|x-frame|x-content'
  (no match)
```

The live host sends no HSTS and no other security header. The repository's
`deploy/nginx/viporder-security-headers.conf` sends six. That difference is
intended, and it is the subject of §2.6.

### 2.3 TLS

`s_client` on this workstation is LibreSSL 3.3 and does not accept `-ext`; the
certificates were read with Python's `ssl` module instead, which is equivalent
and gives the full SAN list.

```
########## viporder.com.vn ##########
tls_version : TLSv1.2
cipher      : ECDHE-ECDSA-AES128-GCM-SHA256
subject     : ((('commonName', 'viporder.com.vn'),),)
issuer      : ((('countryName', 'US'),), (('organizationName', "Let's Encrypt"),), (('commonName', 'YE1'),))
notBefore   : Sep 30 11:14:48 2026 GMT
notAfter    : Dec 29 11:14:47 2026 GMT
SANs        :
    DNS viporder.com.vn
    DNS www.viporder.com.vn

########## www.viporder.com.vn ##########
tls_version : TLSv1.2
cipher      : ECDHE-ECDSA-AES128-GCM-SHA256
subject     : ((('commonName', 'viporder.com.vn'),),)
issuer      : ((('countryName', 'US'),), (('organizationName', "Let's Encrypt"),), (('commonName', 'YE1'),))
notBefore   : Sep 30 11:14:48 2026 GMT
notAfter    : Dec 29 11:14:47 2026 GMT
SANs        :
    DNS viporder.com.vn
    DNS www.viporder.com.vn

########## khachhang.viporder.com.vn ##########
tls_version : TLSv1.2
cipher      : ECDHE-ECDSA-AES128-GCM-SHA256
subject     : ((('commonName', 'khachhang.viporder.com.vn'),),)
issuer      : ((('countryName', 'US'),), (('organizationName', "Let's Encrypt"),), (('commonName', 'YE2'),))
notBefore   : Sep 29 06:08:53 2026 GMT
notAfter    : Dec 28 06:08:52 2026 GMT
SANs        :
    DNS khachhang.viporder.com.vn
    DNS www.khachhang.viporder.com.vn

########## apiviporder.com ##########
tls_version : TLSv1.2
cipher      : ECDHE-RSA-AES256-GCM-SHA384
subject     : ((('commonName', 'apiviporder.com'),),)
issuer      : ((('countryName', 'US'),), (('organizationName', "Let's Encrypt"),), (('commonName', 'YR2'),))
notBefore   : Sep 19 18:04:14 2026 GMT
notAfter    : Dec 18 18:04:13 2026 GMT
SANs        :
    DNS apiviporder.com
    DNS www.apiviporder.com
```

**The important measurement here:** `khachhang.viporder.com.vn` has its **own
certificate**, distinct from the apex certificate. The apex certificate's SAN
set is exactly `{viporder.com.vn, www.viporder.com.vn}` — it does **not** cover
`khachhang.viporder.com.vn`.

That single fact drives §6.4 and §6.6.

### 2.4 Did anything differ from the prior report?

The prior measurement of `viporder.com.vn` reported:

* HTTP 302 → `https://khachhang.viporder.com.vn`
* `Server: Apache/2`

**Both reproduced exactly. Nothing differed at the root path.**

The difference is in coverage, not in value. The prior report measured `/` only.
Measuring the other paths — and the other three names — produced four findings
it did not contain:

1. **The redirect target is malformed for every non-root path** (§2.2). At `/`
   the target is correct only because the remaining path is empty. This is not
   a contradiction of the prior report; it is a defect the prior report's
   vantage point could not see.
2. **`www.viporder.com.vn` behaves identically to the apex** — it also 302s to
   `khachhang`. The prior report did not state what www does.
3. **`apiviporder.com` is a different host in a different zone on different
   nameservers**, not a name in the `viporder.com.vn` zone (see §2.5).
4. **The live host sends no HSTS**, while this repository's configuration sends
   HSTS **without** `includeSubDomains` (see §2.6 — this line said the opposite
   until 2026-10-02; the withholding landed in `87d5440`).

Per `docs/PROJECT-STATUS.md:76` ("If a row and a PR disagree, the PR is wrong —
re-measure"), the discrepancy here was resolved by re-measuring, not by
reconciling. Finding 1 was confirmed three times with three different paths.

### 2.5 `apiviporder.com` is not part of this cutover

Anchored in the repository: `apiviporder.com` is the **provider** API, not the
VIPORDER public site. `docs/KHAIBAO9610-INTEGRATION.md:126` records the
effective registration endpoint as
`POST https://apiviporder.com/frontend/v1/register`, and
`docs/KHAIBAO9610-INTEGRATION.md:105` records a separate admin API at
`https://apiviporder.com/api/v1`.

The measurements are consistent with that and add nothing that contradicts it:

* different address (`8.217.152.4`) from the apex host (`103.159.50.70`);
* different nameservers (`ns7`/`ns8.alidns.com`, SOA `hostmaster.hichina.com`);
* different server stack (`Apache/2.4.6 (CentOS)`, `PHP/5.6.40`,
  `X-Powered-By: PHP/8.1.29`) from the apex host (`Apache/2` with no version
  detail exposed, no PHP);
* a Laravel-style JSON response with `XSRF-TOKEN` and `viporder_session`
  cookies at `/`.

**Conclusion (measured):** `apiviporder.com` is a separate system outside the
`viporder.com.vn` zone. Nothing in this cutover should change it. Its records
appear in this document only because they were asked for and measured.

### 2.6 The HSTS difference, and why it is a hazard

Measured: the live host sends **no** `Strict-Transport-Security`.

This repository's proposed configuration sends:

```
deploy/nginx/viporder-security-headers.conf:29
# SUPERSEDED 2026-10-02. This document used to quote the header as carrying
# `includeSubDomains`. It does not, and must not: the snippet ships
# `max-age=31536000` alone, and the application no longer emits
# `includeSubDomains` either (it did, on /api/*, where nginx `add_header` ADDED a
# second header rather than replacing it — measured, then fixed).
add_header Strict-Transport-Security "max-age=31536000" always;
```

The same value is emitted by the application at
`backend/app/middleware.py:253`.

`includeSubDomains` means the browser applies the HSTS policy to
`khachhang.viporder.com.vn` as well, **even though that host never sends the
header**. Once a returning visitor has loaded `https://viporder.com.vn/` with
HSTS active, their browser will refuse plain HTTP to the portal, and — because
HSTS failures are not bypassable in the way a normal certificate warning is —
a certificate problem on the portal becomes a hard error rather than a
click-through.

This is a **proposal hazard, not a live one**: nothing today sends HSTS, so no
browser has the policy cached yet. It becomes live the moment the new nginx
configuration is served. See §6.4 and §8.

---

## 3. TARGET architecture

```
Internet
  │
  ├─ viporder.com.vn ────────────► nginx  (TLS, security headers, rate limit)
  │                                   ├─ /      static site   /srv/viporder/site
  │                                   └─ /api/  → 127.0.0.1:8000 (uvicorn)
  │                                                └─ KHAIBAO9610 adapter (outbound)
  │
  ├─ www.viporder.com.vn ────────► 301 → https://viporder.com.vn/$request_uri
  │
  └─ khachhang.viporder.com.vn ──► the EXISTING customer portal
                                    UNCHANGED. Not proxied. Not re-hosted here.

Registration:  browser → https://viporder.com.vn/api/v1/registrations
                        → VIPORDER backend → KHAIBAO9610

Existing-customer login:  a LINK on the new site
                        → https://khachhang.viporder.com.vn
```

This matches `docs/DEPLOYMENT.md:11-37` and `docs/PROJECT-STATUS.md:20-25`.
The login is a link, never a proxy — `docs/DEPLOYMENT.md:36` states the rule
("a link, not a proxy"), the nginx config restates it at
`deploy/nginx/viporder.com.vn.conf:14`, and it is enforced on the site by the
portal links at `index.html:125`, `:177`, `:268`, `:570`, `:581`, `:650` and
by the server-side constant at `backend/app/config.py:24`.

---

## 4. Values the owner must supply

Nothing here is inferred or invented. These are the things this document
**cannot** measure from the public internet, and therefore does not name:

| # | Value needed | Why it cannot be measured here |
|---|---|---|
| 1 | **DNS control-panel product/name for the `viporder.com.vn` zone** | The delegation is measurable (`ns1`–`ns4.zonedns.vn`, §2.1) but the *account*, the panel and the API/console used to edit records are not visible from outside. Whether the owner edits records through a reseller panel, a registrar console, or a Zonedns account is unknown to this document. |
| 2 | **Login credentials / API token for that panel** | Not visible, must not be committed. |
| 3 | **The server's real hostname, SSH access, and OS** | `docs/DEPLOYMENT.md:52` already lists this as owner-supplied. Only the public IP `103.159.50.70` is measured. Its provider is not named here because it was not measured. |
| 4 | **The current Apache vhost file text for `viporder.com.vn` and for `khachhang.viporder.com.vn`** | This is what produces the malformed redirect in §2.2. It must be read on the server before cutover, and a copy kept for rollback. |
| 5 | **Whether `khachhang.viporder.com.vn` is a static SPA on the same Apache host, or proxied to an application** | It answers `200` with a 3,061-byte document and a `vary` header; that is consistent with a static SPA (as `docs/KHAIBAO9610-INTEGRATION.md:116-117` records) but the server configuration is not visible. |
| 6 | **How the `khachhang` certificate is renewed** | Two distinct Let's Encrypt certificates exist (§2.3). Whether renewal is HTTP-01 on the same port 80, DNS-01, or manual is not visible. §6.6 explains why this decides the cutover method. |
| 7 | **Maintenance window and customer-notification plan** | Business decision. |
| 8 | **Confirmation that `KHAIBAO9610_MODE` stays `mock`** | `docs/DEPLOYMENT.md:237` forbids `http` mode without the verified contract (issue #4). Registration must not go live in real mode as part of a DNS cutover. |

**Proposed TTL/record values in §6.1 are proposals.** They are written as
proposals because the owner, not this document, decides them.

---

## 5. What this document does not do

* It does not change DNS. No record was written.
* It does not touch the live host. Every command against it was `dig`,
  `GET`/`HEAD`, or a TLS handshake. **No `POST` was ever sent to production** —
  including to `apiviporder.com`.
* It does not name a DNS provider, control panel, or hosting provider that was
  not measured. `ns1`–`ns4.zonedns.vn` is a *measured delegation*, quoted from
  `dig`; it is not a claim about which account or console the owner uses.

---

## 6. CUTOVER

### 6.0 SUPERSEDING DECISION — the site moves to `160.22.170.20` (owner, 2026-10-08)

**Read this before §6.1.** §6.1 was written for "the new site runs on the existing
host `103.159.50.70`". On 2026-10-08 the owner decided production runs on the
staging VPS **`160.22.170.20`**. That is the "conditional" case at the end of §6.1,
and it changes what must happen in DNS.

**Re-measured 2026-10-09** (public resolver; authoritative `ns1.zonedns.vn` agrees
for A and MX):

| Name | Type | Value | TTL |
|---|---|---|---|
| `viporder.com.vn` | A | `103.159.50.70` | **3600** (was 600 on 2026-10-02) |
| `viporder.com.vn` | MX | `10 viporder.com.vn.` | 3600 |
| `viporder.com.vn` | TXT / AAAA / CAA | none | — |
| `_dmarc.viporder.com.vn` | TXT | none | — |
| `www.viporder.com.vn` | CNAME | `viporder.com.vn.` | 3600 |
| `khachhang.viporder.com.vn` | A | `103.159.50.70` | 3600 |
| `mail`, `webmail`, `cpanel`, `ftp`, `api`, `admin` `.viporder.com.vn` | A | `103.159.50.70` | — |
| `zz-khong-ton-tai-8341.viporder.com.vn` (random) | A | `103.159.50.70` | — **a wildcard `*` record exists** |
| `web.viporder.vn` (acceptance host, other zone) | A | `160.22.170.20` | — |

**HAZARD — email.** The MX record points at the **apex name** `viporder.com.vn`,
not at a mail host. Changing the apex A record to `160.22.170.20` therefore also
moves **inbound mail for `@viporder.com.vn`** to a VPS that runs no mail server:
mail would bounce or be lost. Nothing in §6.1 covered this, because §6.1 never
changed the apex.

**Record changes, in order (PROPOSALS — none applied):**

| # | When | Change | Why |
|---|---|---|---|
| 1 | ≥ 24 h before | `viporder.com.vn` A TTL 3600 → **300** (value unchanged) | so step 4 and any rollback take minutes, not an hour |
| 2 | ≥ 24 h before | ensure `mail.viporder.com.vn` is an **explicit** A `103.159.50.70` (today it may be only the wildcard) | gives mail a name that does not move |
| 3 | ≥ 24 h before | MX `10 viporder.com.vn.` → **`10 mail.viporder.com.vn.`**, then send/receive a test mail | mail stops depending on the apex before the apex moves |
| 4 | cutover | `viporder.com.vn` A `103.159.50.70` → **`160.22.170.20`** | the actual move; `www` follows (CNAME) |
| — | never | `khachhang.viporder.com.vn` A, the wildcard `*`, `mail`/`webmail`/`cpanel` | all stay on `103.159.50.70`; the portal and mail are not moving |

Steps 2–3 must be confirmed with whoever hosts mail on `103.159.50.70`: if that
host's mail certificate or SPF expectations name the apex, they must accept the
`mail.` name too. **No AAAA** is added (the VPS IPv6 path is untested).

**On the VPS, before step 4.** MEASURED 2026-10-10: the production stack already
runs on `160.22.170.20`, next to staging, and is reachable only on the loopback.

| | Staging (unchanged) | Production |
|---|---|---|
| Checkout | `~/viporder-staging` | `~/viporder-production` (`git checkout --detach <SHA>`) |
| Compose project | `viporder` | `viporder-prod` (`-p viporder-prod`) |
| Overlay | `deploy/docker-compose.staging.yml` | `deploy/docker-compose.production-vps.yml` |
| nginx bind | `0.0.0.0:18081` / `0.0.0.0:18443` | **`127.0.0.1:28081` / `127.0.0.1:28443`** |
| Database volume | `viporder_db-data` | `viporder-prod_db-data` |
| TLS inside the stack | self-signed `staging.viporder.com.vn` | self-signed `viporder.com.vn` in `deploy/certs/` (git-ignored) |
| Admin token | `deploy/.env` | `deploy/.env` + `~/.viporder-prod-admin-token` (mode 600) |
| `KHAIBAO9610_ENABLE_REAL_REGISTRATION` | `no` | **`no` until cutover day** — set `yes` and `up -d` as the last step before step 4 |

The Caddy at `/srv/vip-staging-proxy/Caddyfile` owns :80/:443 for every project
on the host and is root-owned. On cutover day a `viporder.com.vn, www.viporder.com.vn`
block reverse-proxies to **`https://127.0.0.1:28443`** (the production nginx,
never staging's 18443) with `header_up Host viporder.com.vn` and a transport of
`tls_server_name viporder.com.vn` + `tls_insecure_skip_verify` (the inner
certificate is self-signed; Caddy terminates the public TLS). The exact block is
kept beside the checkout at `~/viporder-production/caddy-viporder.com.vn.snippet`.
Caddy can only obtain the public certificate *after* DNS points at it, so the
first minutes after step 4 may show a certificate error unless the certificate is
obtained by DNS-01 beforehand.

Bring-up and checks, as run on 2026-10-10 (all passed at `87e2695`):

```bash
cd ~/viporder-production/deploy
C="docker compose -p viporder-prod -f docker-compose.yml -f docker-compose.production-vps.yml"
$C build app && $C run --rm app alembic upgrade head && $C up -d
R="--resolve viporder.com.vn:28443:127.0.0.1"
curl -sk $R https://viporder.com.vn:28443/api/v1/health   # tracking_reads=live, registration_writes=disabled
curl -sk $R -o /dev/null -w '%{http_code}\n' https://viporder.com.vn:28443/.git/HEAD   # 403, not 200
```

**Rollback:** step 4 in reverse (A back to `103.159.50.70`). With step 1 done, the
TTL bounds the rollback to about 5 minutes. Steps 2–3 do not need rolling back.

### 6.1 Record changes

**All rows in this table are PROPOSALS.** None has been applied. The "current"
column is measured (§2.1).

| Name | Type | Current (MEASURED) | Proposed (PROPOSAL) | Reason |
|---|---|---|---|---|
| `viporder.com.vn` | A | `103.159.50.70` TTL 600 | **unchanged** | The apex already points at the host. The cutover changes what that host *serves*, not where it points (§6.6). |
| `viporder.com.vn` | AAAA | none | **none — do not add one** | No AAAA exists today. Adding one would send a share of traffic to a path never tested. |
| `www.viporder.com.vn` | CNAME | `viporder.com.vn.` TTL 3600 | **unchanged**, but see TTL note | www already follows the apex. nginx turns it into a 301 to the apex (`deploy/nginx/viporder.com.vn.conf:58-68`). |
| `khachhang.viporder.com.vn` | A | `103.159.50.70` TTL 600 | **UNCHANGED. DO NOT TOUCH.** | §6.4. This record is already correct and its correctness is the whole point. |
| `apiviporder.com` | A | `8.217.152.4` TTL 600 | **UNCHANGED. NOT IN THIS ZONE.** | §2.5. Provider system. |

The honest summary: **if this plan is followed, no DNS record changes at all.**
The apex already resolves to the right host. What changes is which server
software answers on that host, and with which configuration. A cutover that
begins by editing records has mis-diagnosed the problem.

The one record change that would be needed is conditional, and it is the owner's
call:

**PROPOSAL (conditional, only if the site moves to a different IP):** if
`viporder.com.vn` is ever pointed at an address other than `103.159.50.70`, then
the apex A record changes, `www` follows automatically (it is a CNAME), and
`khachhang` must **still** not be touched — which then requires deciding whether
the portal also moves, since it currently shares the address. That decision is
out of scope here because the current plan does not move the site.

### 6.2 Lower the TTL first — why, and by how much

Measured starting TTLs: **600 s** for `viporder.com.vn` and
`khachhang.viporder.com.vn`, **3600 s** for the `www` CNAME.

**PROPOSAL, to be run no later than 24 hours before the window:**

| Record | From | To | Rationale |
|---|---|---|---|
| `viporder.com.vn` A | 600 | 300 | 10 min is short enough that a mistake is recoverable within one coffee break, and long enough not to multiply query volume. |
| `www.viporder.com.vn` CNAME | 3600 | 300 | This is the one that actually needs it. A 1-hour TTL is the single longest thing standing between "we rolled back" and "customers see the rollback". |
| `khachhang.viporder.com.vn` A | 600 | **leave at 600** | Changing it is a change to the record this plan forbids changing. Leave it alone. |

The reason to lower TTL **before** the change rather than during it: a TTL only
affects how long a *new* answer is cached. The old, longer TTL is already in
resolvers worldwide. Lowering the value at the same moment as the cutover means
the change still propagates at the old, long TTL — the new short TTL has no
effect on records already cached. So:

1. Lower the TTL.
2. **Wait longer than the previous TTL** — more than 3600 s for `www`, more than
   600 s for the apex. A full 24 h is the safe reading, because resolvers and
   downstream caches do not all honour the TTL exactly.
3. Verify with `dig` that the new TTL is what resolvers now return (§7.2).
4. Only then make the change.

**Note the tension with §6.1.** If no record changes, the TTL exercise buys
nothing for DNS. It still buys something real: it makes the *rollback* of any
emergency record change fast. Keep the step, but do not pretend it is load-
bearing when the plan is already to change nothing.

### 6.3 What the reverse proxy must do

Reference: `deploy/nginx/viporder.com.vn.conf`.

| Requirement | Where it is expressed |
|---|---|
| Terminate TLS for `viporder.com.vn` and `www.viporder.com.vn` | `:73-87` (main site) and `:58-68` (www) |
| Redirect plain HTTP to HTTPS, keeping the ACME path on HTTP | `:40-53`. The `location /.well-known/acme-challenge/` at `:46-48` must stay reachable over HTTP or renewal fails. |
| Collapse `www` to the apex with a 301 that **preserves the path** | `:67` — `return 301 https://viporder.com.vn$request_uri;` |
| Serve the static site from disk | `:111` — `root /srv/viporder/site;`, populated per `docs/DEPLOYMENT.md:103-111` |
| Proxy registration and API to the app | `:115-121` (`= /api/v1/registrations`, strict `10r/m`) and `:125-131` (`^~ /api/`) |
| Send the correct scheme to the app | `deploy/nginx/proxy_params_viporder:8` — `X-Forwarded-Proto $scheme`. Without this the app can believe it is on HTTP and redirect forever; the app reads the header at `backend/app/middleware.py:260`. |
| Serve a real 404 with a branded body | `:138` `error_page 404 /404.html;` plus `:149-156` |
| Send the six security headers on every location | `:94` plus every `include` at `:146, :155, :162, :169`. This is not decorative: nginx drops server-level `add_header` in any location that declares its own, which once cost the whole header set (`docs/PROJECT-STATUS.md:69`, `docs/SECURITY.md`). `tools/check_nginx_config.py` exists to guard exactly this. |
| Never proxy or serve the portal | `:14` states it; no `server_name` for `khachhang` exists anywhere in the file |

**Two gaps in the current configuration, both required before cutover**, because
the host it will be installed on also serves the portal:

* **PROPOSAL — there is no `server_name khachhang.viporder.com.vn` block.**
  See §6.6. This must be resolved by design (nginx in front of Apache, or a
  full port of the portal vhost), not discovered in production.
* **PROPOSAL — the port-80 block at `:43` lists only
  `viporder.com.vn www.viporder.com.vn`.** If nginx becomes the only listener on
  port 80 on that host, `khachhang.viporder.com.vn` has no HTTP vhost and,
  critically, no ACME challenge location. That breaks HTTP-01 renewal for the
  portal certificate, which expires **2026-12-28 06:08:52 GMT** (§2.3).

### 6.4 How `khachhang` stays untouched — the highest-risk part

**The DNS record must not be modified.** `khachhang.viporder.com.vn` A
`103.159.50.70` (TTL 600) is already correct. There is no reason to edit it, and
editing it is the failure mode this section exists to prevent. If a change list
for the cutover window contains this record, the change list is wrong.

That is the easy half. The hard half is that **not touching the DNS record is
not sufficient to keep the portal working**, for one measured reason:

> `viporder.com.vn`, `www.viporder.com.vn` and `khachhang.viporder.com.vn` all
> resolve to the **same address**, `103.159.50.70` (§2.1).

So the portal shares a host with the site being cut over. Anything done to that
host can take the portal down even though its record was never touched. Four
concrete mechanisms, each grounded in a measurement:

**(a) Port 443 collision.** Apache answers 443 today for all three names
(`Server: Apache/2`, §2.2). nginx cannot bind the same port at the same time. So
the cutover is either a handover or a layering — and either way the portal's
serving path changes, even though its DNS entry does not.

**(b) Certificate name mismatch, if nginx becomes the only listener.** The apex
certificate covers exactly `{viporder.com.vn, www.viporder.com.vn}` (§2.3). The
only 443 server blocks in `deploy/nginx/viporder.com.vn.conf` are for those two
names (`:62`, `:77`). A request for `khachhang.viporder.com.vn:443` would
therefore match the apex block — nginx falls back to the default server when no
`server_name` matches — and present a certificate that does **not** include
`khachhang`. The browser fails the handshake. The customer portal becomes
unreachable while its DNS record is perfectly correct.

**(c) HSTS makes (b) unrecoverable for returning visitors.**
`deploy/nginx/viporder-security-headers.conf:29` sets
`Strict-Transport-Security ... includeSubDomains`, and
`backend/app/middleware.py:253` does the same. **Both halves of this paragraph are
now false** — measured 2026-10-02, the API path returned TWO HSTS headers, one
carrying `includeSubDomains`, and both tiers were corrected. `includeSubDomains` on
`viporder.com.vn` covers `khachhang.viporder.com.vn`. A visitor who has loaded
the new site once will have a cached HSTS policy for the portal. If (b) happens,
they do not get a bypassable warning; they get a hard failure. **This is the
single most dangerous interaction in the plan**, and it is a *proposed* hazard:
nothing sends HSTS today (§2.6).

**(d) Shared server reload.** Replacing or reloading the web server is a
host-wide action. A configuration error introduced for the apex can stop the
portal from being served at all, independent of (a)–(c).

**PROPOSED mitigations, in order of preference:**

1. **Do not put nginx on the portal's port.** Run nginx in front of Apache
   (Apache moved to a loopback port, nginx `proxy_pass` for the portal), or run
   the new site on its own host. Either way the portal keeps a working TLS
   listener and a working certificate throughout.
2. **If nginx must replace Apache, add an explicit portal vhost first** — a
   `server_name khachhang.viporder.com.vn` block with the portal's own
   certificate (`/etc/letsencrypt/live/khachhang.viporder.com.vn/…`), its own
   document root or upstream, and its own ACME location on port 80. Add it and
   prove it works *before* removing anything.
3. ~~**Consider dropping `includeSubDomains` from HSTS**~~ — **DONE.** It is
   withheld in the snippet, and the application no longer emits it on `/api/*`
   (which is where it was leaking). `max-age` without `includeSubDomains`
   protects the apex only. This weakens a security control, so it is a decision
   for the owner and should be recorded either way.
4. **Prove the portal before and after, from outside**, with the commands in
   §7.4 — not with `systemctl status`.

**Explicit rule for the window:** `khachhang.viporder.com.vn` gets **no DNS
change, no certificate change, no document-root change, and no vhost rewrite**
as part of this cutover. If the portal must change, it is a separate change with
its own plan, window and rollback.

### 6.5 Avoiding a redirect loop

**The measurement first.** Today there is no loop, because the chain
terminates:

```
http://viporder.com.vn/     → 301 → https://viporder.com.vn/
https://viporder.com.vn/    → 302 → https://khachhang.viporder.com.vn
https://khachhang.viporder.com.vn/  → 200   (terminates)
```

`khachhang` answers `200` and never redirects back. **That terminating `200` is
the only thing preventing a loop today.** The loop is created by adding a second
edge to the graph.

**How the loop is created.** The new site's every existing-customer entry point
is a link to `https://khachhang.viporder.com.vn` (`index.html:125`, `:177`,
`:268`, `:570`, `:581`, `:650`; `404.html:35`, `:68`, `:100`;
`backend/app/config.py:24`). If a matching "unauthenticated visitor → go to
`viporder.com.vn`" rule is *also* introduced on the portal — a natural thing to
do once the marketing site lives at the apex, because the portal's "back to the
home page" link ought to point there — then a server-side cycle exists:

```
browser → https://viporder.com.vn/      → 302 → khachhang
        → https://khachhang…/           → 302 → viporder.com.vn
        → … repeating until the browser gives up with ERR_TOO_MANY_REDIRECTS
```

**The rule that prevents it:** the link from the new site to the portal is a
`<a href>` in HTML. It must **never** be a server-side redirect. A redirect can
be answered by another redirect; a link a human clicks cannot. A "back to home"
link on the portal is likewise a link. As long as both directions are links and
neither is a redirect, no cycle can form.

**The second, larger risk: leaving the catch-all in place.** Today *every* path
on the apex 302s away (§2.2). If that rule survives anywhere in the path — in
the old Apache vhost still bound to port 80, in an `.htaccess`, in a
front-ending proxy, in a CDN rule — then:

* `https://viporder.com.vn/` never serves `index.html`;
* `https://viporder.com.vn/robots.txt` and `/sitemap.xml` never serve their
  files;
* `https://viporder.com.vn/api/v1/registrations` is still answered with a 302,
  so registration is dead before the request reaches the app;
* and because the redirect is a prefix rule with no separator, the target is a
  hostname that does not resolve (§2.2).

**This is the single change that must be removed before anything else is
tested.** It is also why the plan is "replace what the host serves", not "edit
a record".

**The third variant — a loop that needs no second edge.** The same class of
problem arises if the application-level HTTPS redirect is driven by a proxy that
does not forward the scheme: the app sees HTTP, redirects to HTTPS, the proxy
serves HTTPS but the app still sees HTTP, forever. This configuration is already
correct — `deploy/nginx/proxy_params_viporder:8` sends
`X-Forwarded-Proto $scheme`, and the app reads it at
`backend/app/middleware.py:260`. Do not deploy a proxy configuration that omits
this header.

**Loop checklist for the window — all four must be true afterwards:**

1. `https://viporder.com.vn/` returns `200`, not `3xx`.
2. `https://viporder.com.vn/robots.txt` returns `200` and its contents.
3. `https://khachhang.viporder.com.vn/` returns `200` and does not redirect to
   the apex.
4. `curl -sSL --max-redirs 10` on the apex completes without hitting the limit.

### 6.6 Apache today, nginx in this repository — what that implies

**Measured:** the live host runs `Apache/2` (§2.2). This repository deploys
**nginx** (`deploy/nginx/viporder.com.vn.conf`, installed per
`docs/DEPLOYMENT.md:139-151`).

That makes the cutover a **server-software replacement on a shared host**, not a
DNS change. The implications are specific:

1. **The Apache configuration is the source of the redirect behaviour and must
   be read and kept before it is removed.** The malformed `Location` in §2.2
   comes from an Apache rule this document cannot see. Preserve the file; it is
   the rollback.
2. **Every behaviour Apache provides today must be re-provided by nginx or
   deliberately dropped.** Concretely: the HTTP→HTTPS redirect, the
   `viporder.com.vn` → `khachhang` redirect (dropped — §6.5), the portal vhost,
   and the portal's TLS certificate. The repository's nginx file covers the
   first; it does **not** cover the portal (no `server_name khachhang…`).
3. **`nginx -t` is not a substitute for testing the portal.** It validates
   syntax, and `tools/check_nginx_config.py` validates `add_header` inheritance.
   Neither can tell you that the portal's certificate is now being presented
   under the wrong `server_name`.
4. **Certificate renewal changes hands.** Two distinct certificates exist
   (§2.3). Whichever HTTP client answers port 80 decides whether ACME HTTP-01
   can still succeed. If nginx takes port 80 and has no portal ACME location,
   the portal certificate cannot renew and expires **2026-12-28 06:08:52 GMT**.
   That is a dated, predictable outage, and it is a rollback trigger (§8).
5. **The nginx configuration assumes a Debian/Ubuntu layout**
   (`/etc/nginx/sites-available`, `/etc/letsencrypt/live/…`,
   `docs/DEPLOYMENT.md:45`). The live host's OS is not measured and the
   provider-issued `Apache/2.4.6 (CentOS)` belongs to `apiviporder.com`, a
   *different* host — it must not be read across as evidence about
   `103.159.50.70`. **Verify the OS before assuming those paths exist.**
6. **Apache and nginx can coexist**, which is the preferred path (§6.4
   mitigation 1), but only with a deliberate port layout. Apache keeps 443 for
   the portal; nginx takes 443 for the apex on a **second address**, or nginx
   takes 80+443 and proxies the portal to Apache on a loopback port. Both work;
   neither is the default of either configuration file.

### 6.7 Pre-cutover checklist

Each item is a thing to *measure*, not to assume. Run in order; stop at the
first failure.

**T-24 h**

- [ ] Confirm the decision: nginx in front of Apache, or nginx replaces Apache.
      Record it. §6.6 lists what each implies.
- [ ] Read and archive the live Apache vhosts for `viporder.com.vn` **and**
      `khachhang.viporder.com.vn`. Store off-host. This is the rollback.
- [ ] Record the OS and installed web-server packages on `103.159.50.70`.
- [ ] Lower TTLs per §6.2 (`www` 3600 → 300; apex 600 → 300; **portal
      unchanged**).
- [ ] Confirm `KHAIBAO9610_MODE` will stay `mock` (`docs/DEPLOYMENT.md:237`).
- [ ] Confirm the release SHA to deploy and that the working tree is clean
      (`docs/DEPLOYMENT.md:71-83`).

**T-1 h**

- [ ] `dig` the new TTLs and confirm resolvers return 300 (§7.2).
- [ ] Capture the BEFORE state of all four names — DNS, HTTP, TLS — into one
      file, using §7 verbatim. This is what "did we break it?" is answered
      against.
- [ ] Confirm the portal answers `200` and note its `etag` and
      `last-modified` (measured today: `"bf5-65c996fd53352"`, `Tue, 29 Sep 2026
      06:38:56 GMT`). These must be **identical** afterwards.
- [ ] Confirm the portal certificate is not near expiry (currently
      2026-12-28) and that renewal still has a working path.
- [ ] Stage the nginx config and run `nginx -t` on the **staging** copy.
- [ ] Decide and record the HSTS decision: keep `includeSubDomains` or drop it
      for now (§6.4 mitigation 3).
- [ ] Have §8 open, with the exact commands, before touching anything.

**T-0**

- [ ] Deploy the apex vhost only. **Do not remove the Apache portal vhost.**
- [ ] `nginx -t` before reload; reload, do not restart, if the topology allows.
- [ ] Run §7 immediately.
- [ ] Only after the apex verifies, address the portal path per the decision
      made at T-24 h.

**Explicitly out of scope for this window:** the portal's DNS record,
certificate, document root and vhost; `apiviporder.com` in any respect; real
KHAIBAO9610 mode.

### 6.8 Rollback triggers and exact commands

**Rollback triggers — any one of these, no debate required:**

| # | Trigger | How it is measured |
|---|---|---|
| T1 | `https://viporder.com.vn/` does not return `200` | §7.1 |
| T2 | The apex still 302s to `khachhang` | §7.1 |
| T3 | `https://khachhang.viporder.com.vn/` does not return `200` | §7.4 |
| T4 | The portal's `etag`/`last-modified` changed | §7.4 |
| T5 | Any TLS name-mismatch error on any of the four names | §7.3 |
| T6 | `robots.txt` or `sitemap.xml` does not return `200` with its contents | §7.5 |
| T7 | `ERR_TOO_MANY_REDIRECTS` anywhere | §6.5 checklist |
| T8 | Registration returns anything other than the expected validation status | §7.8 |
| T9 | Rate limiting locks out legitimate users (`429` in normal use) | `:115-131` |
| T10 | Any 5xx on `/api/v1/*` that was not there before | §7.7 |

**Rollback procedure.** Two cases, and they are genuinely different.

*Case A — nginx is in front of Apache (the preferred topology).* The portal is
untouched by construction, so only the apex needs undoing. Point the apex vhost
back, or stop nginx:

```bash
# On the server. Verify the symlink target before removing it.
ls -l /etc/nginx/sites-enabled/viporder.com.vn.conf
rm /etc/nginx/sites-enabled/viporder.com.vn.conf
nginx -t && systemctl reload nginx
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://viporder.com.vn/
```

*Case B — nginx replaced Apache.* Restore the archived Apache vhost, confirm
Apache holds 443 again, then stop nginx:

```bash
# On the server. Adjust paths to what was actually recorded at T-24h.
cp /root/rollback/viporder.com.vn.apache.conf /etc/apache2/sites-available/
a2ensite viporder.com.vn
apache2ctl configtest
systemctl stop nginx
systemctl reload apache2
```

Then verify from **outside** — never from the server:

```bash
curl -sS -I https://viporder.com.vn/          # expect 302 -> khachhang again
curl -sS -I https://khachhang.viporder.com.vn/ # expect 200
```

*Case C — DNS was changed despite this plan.* Lower the TTL first (§6.2), then
restore the record and wait out the old TTL:

```bash
dig +noall +answer A viporder.com.vn          # confirm the old value is back
```

**Restoring the old behaviour is not a failure.** The prior behaviour —
everything redirecting to the portal — is bad for the new site but it is a
state the business has been in, and no customer is harmed by returning to it.
A failed cutover that rolls back cleanly is a good outcome. A failed cutover
that stays up because nobody wanted to admit failure is not.

---

## 7. AFTER — verification

Run all of this from a machine **outside** the server. Nothing here trusts
`systemctl status`.

### 7.1 curl — apex and www

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/
```
**Expected:** `200`. (Before: `302`. See §2.2.) A `302` here means the
catch-all redirect survived — trigger **T2**, roll back.

```bash
curl -sSI https://viporder.com.vn/ | tr -d '\r' | grep -i '^server:'
```
**Expected:** `server: nginx` (with a version, or `nginx` alone if
`server_tokens off`). **Before: `server: Apache/2`.** A value containing
`Apache` means the old vhost is still answering — the cutover did not happen.

```bash
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://www.viporder.com.vn/robots.txt
```
**Expected:** `301 https://viporder.com.vn/robots.txt` — a 301 that
**preserves the path** (`deploy/nginx/viporder.com.vn.conf:67`). If the target
is `https://viporder.com.vnrobots.txt`, the separator bug of §2.2 has been
reproduced in nginx — roll back.

```bash
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://viporder.com.vn/
```
**Expected:** `301 https://viporder.com.vn/` (`:50-52`).

### 7.2 DNS

```bash
dig +noall +answer A viporder.com.vn
```
**Expected after cutover (proposal, unchanged record):**
`viporder.com.vn. 300 IN A 103.159.50.70` — same address, the lowered TTL
(§6.2). **If the address changed, someone changed DNS against this plan.**

```bash
dig +noall +answer CNAME www.viporder.com.vn
```
**Expected:** `www.viporder.com.vn. 300 IN CNAME viporder.com.vn.` — TTL now
300, target unchanged.

```bash
dig +noall +answer A khachhang.viporder.com.vn
```
**Expected:** `khachhang.viporder.com.vn. 600 IN A 103.159.50.70` — **TTL still
600, address still `103.159.50.70`.** Any difference here is an unintended
change to the portal and is trigger **T3**.

```bash
dig +noall +answer A apiviporder.com
```
**Expected:** `apiviporder.com. 600 IN A 8.217.152.4` — unchanged. This zone was
never in scope.

```bash
dig +trace +noall +answer A viporder.com.vn | tail -5
```
**Expected:** the delegation still terminates at `ns1`–`ns4.zonedns.vn`. A
change here means the *zone* was moved, which this plan does not call for.

### 7.3 TLS

```bash
/opt/homebrew/bin/python3.12 - <<'PY'
import ssl, socket
for h in ["viporder.com.vn","www.viporder.com.vn","khachhang.viporder.com.vn"]:
    ctx = ssl.create_default_context()
    with socket.create_connection((h,443), timeout=20) as s:
        with ctx.wrap_socket(s, server_hostname=h) as ss:
            c = ss.getpeercert()
    print(h, "->", [v for t,v in c["subjectAltName"] if t=="DNS"], "expires", c["notAfter"])
PY
```

**Expected:**

* `viporder.com.vn` → SANs must include `viporder.com.vn` and
  `www.viporder.com.vn`. Expiry on or after **2026-12-29 11:14:47 GMT**
  (renewal may legitimately extend it).
* `khachhang.viporder.com.vn` → SANs must include
  `khachhang.viporder.com.vn` and `www.khachhang.viporder.com.vn`, and expiry
  must not move backwards from **2026-12-28 06:08:52 GMT**. A name mismatch
  here is trigger **T5** and, with HSTS active, is unrecoverable for returning
  visitors (§6.4c).

### 7.4 Browser checks

Performed in a real browser, because these are the checks a command line
cannot make for you:

1. `https://viporder.com.vn/` renders the public site — the page whose title is
   `VIPORDER - Vận chuyển & nhập hàng Trung Quốc` (`index.html:6`). Not the
   portal, not a redirect.
2. Address bar shows no certificate warning. Click the padlock: issuer
   Let's Encrypt, covering `viporder.com.vn`.
3. `https://khachhang.viporder.com.vn/` renders the portal — the page whose
   title is `VipOrder - Vận Chuyển Hàng Trung Quốc | Taobao, 1688, Tmall`
   (measured live, §2.2). **This is the check that matters most.**
4. In a **fresh private window** (to avoid a cached HSTS policy), load
   `https://viporder.com.vn/`, then navigate to the portal via the login CTA.
   It must load without a certificate warning. Repeat for
   `http://khachhang.viporder.com.vn/` in the same window — it must upgrade to
   HTTPS, not fail.
5. Network panel: no request in the chain is a `302` back to the apex.
6. The site is usable at a mobile breakpoint — noted as never visually verified
   at `docs/PROJECT-STATUS.md:92`.

### 7.5 robots.txt and sitemap.xml

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/robots.txt
```
**Expected:** `200`. **Before: `302` to a non-existent hostname.**

```bash
curl -sS https://viporder.com.vn/robots.txt | grep -i '^sitemap:'
```
**Expected:** `Sitemap: https://viporder.com.vn/sitemap.xml` (`robots.txt:19`).

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/sitemap.xml
```
**Expected:** `200`. **Before: `302` to a non-existent hostname.**

```bash
curl -sS https://viporder.com.vn/sitemap.xml | grep -o '<loc>[^<]*</loc>'
```
**Expected:** exactly one line — `<loc>https://viporder.com.vn/</loc>`
(`sitemap.xml:20`). More than one means a page was added to the sitemap without
being added to the site (`sitemap.xml:15-16`).

### 7.6 Canonical tag

```bash
curl -sS https://viporder.com.vn/ | grep -i 'rel="canonical"'
```
**Expected:** `<link rel="canonical" href="https://viporder.com.vn/">`
(`index.html:11`).

```bash
curl -sS https://www.viporder.com.vn/ -L | grep -i 'rel="canonical"'
```
**Expected:** after following the 301, the same canonical. The canonical must
**never** name `www` — the site's own canonical is the apex, and `www` 301s to
it (`:58-68`). A canonical pointing at `www` alongside a `www`→apex redirect is
a contradiction that wastes crawl budget.

### 7.7 Login redirect

```bash
curl -sS https://viporder.com.vn/ | grep -o 'https://khachhang.viporder.com.vn' | wc -l
```
**Expected:** a non-zero count — the portal link is present in the served HTML
(`index.html:125` and five more). `0` means the served page is not this
repository's `index.html`.

```bash
curl -sS https://viporder.com.vn/api/v1/health
```
**Expected:** a JSON body reporting `"status":"ok"`, plus a `"mode"` field. This
is the same check `deploy/post-deploy-check.sh:58-71` makes. Read the `mode`
value: while it says `mock`, real registration is **not** live
(`deploy/post-deploy-check.sh:70`, `docs/PROJECT-STATUS.md:196`). Do not report
the site as taking real registrations.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/api/v1/health
```
**Expected:** `200` (the health endpoint is proxied to the app, `:125-131`).

Then, in a browser: click the "existing customer" CTA on the new site and
confirm it lands on `https://khachhang.viporder.com.vn` and the portal loads —
one hop, no bounce back (§6.5).

### 7.8 Registration test — described, NOT run against production

Registration is the one path that **creates state in a third-party system**. It
must not be exercised against production as part of this cutover. Described
here so it can be run deliberately, later, in mock mode, with the owner's
agreement.

The real call is:

```
POST https://viporder.com.vn/api/v1/registrations
Content-Type: application/json
Idempotency-Key: <uuid>
```

proxied by nginx to the app (`deploy/nginx/viporder.com.vn.conf:115-121`) and
forwarded to KHAIBAO9610's
`POST https://apiviporder.com/frontend/v1/register`
(`docs/KHAIBAO9610-INTEGRATION.md:126`).

**Do not send it.** What to run instead, and why each is safe:

* **Validation probe — safe, because the payload is invalid.**
  `deploy/post-deploy-check.sh:152-156` sends deliberately invalid data
  (`full_name` empty, `phone` not a phone, `consent` false) and expects
  `400`/`422`. Nothing is created. Run the script rather than hand-rolling it:

  ```bash
  ./deploy/post-deploy-check.sh https://viporder.com.vn
  ```
  **Expected:** the summary line `PASS=n FAIL=0 WARN=w` and `DEPLOYMENT
  VERIFIED`, or an explicit list of failures. Note the script's own warning at
  `:158-159`: a `429` means rate limiting blocked the probe and validation was
  *not* exercised — do not read `429` as success.

* **Real registration — do not run now.** It requires `KHAIBAO9610_MODE` other
  than `mock`, which `docs/DEPLOYMENT.md:237` forbids until issue #4 is
  resolved. And `docs/KHAIBAO9610-INTEGRATION.md:157-160` records that
  `POST /frontend/v1/register` **does not return the customer code**, so a real
  test cannot be verified end-to-end from the response anyway. When it is run,
  run it with a phone number the owner has reserved for testing, and expect
  `201` (created), `202` (`PENDING`, lead retained), or `409` (duplicate) per
  `docs/PROJECT-STATUS.md:63` — never treat a `201` alone as "registration
  works".

* **What must *not* be run against production under any circumstances:** a
  `POST` that creates a real customer; a real credential; anything against
  `apiviporder.com` other than `GET`.

---

## 8. Rollback conditions

Roll back if **any** of the following is true after the change. Each is
measurable with §7, and each is a statement about the customer, not about the
deployment.

1. **The existing customer portal is not reachable.** Anything other than `200`
   at `https://khachhang.viporder.com.vn/`, or any TLS error on that name.
   **This is the primary trigger and it does not require a second opinion** —
   trigger **T3/T5**.
2. **The portal's served content changed.** `etag` or `last-modified` differs
   from the value captured at T-1 h. The portal was supposed to be untouched;
   if it moved, something outside this plan touched it — trigger **T4**.
3. **The apex does not serve the new site.** A `302` at
   `https://viporder.com.vn/` means the old catch-all survived — trigger
   **T1/T2**.
4. **A redirect loop exists.** `ERR_TOO_MANY_REDIRECTS`, or `curl -L` exceeding
   `--max-redirs 10` on any of the four names — trigger **T7**.
5. **Robots/sitemap still redirect**, or serve the wrong body — trigger **T6**.
   This is not cosmetic: it is the measurable signal that the apex is still
   being answered by the old rule.
6. **Registration returns an unexpected status** — trigger **T8**. In
   `mock` mode the expected outcomes are the validation statuses only.
7. **The portal certificate cannot renew.** If the renewal path is broken by
   the new server topology, roll back before **2026-12-28 06:08:52 GMT**, not
   after. A predictable expiry is the one outage that is entirely avoidable.
8. **Load or latency regresses materially** on `/api/*` — trigger **T10**.

**How to decide quickly.** The asymmetry is the whole argument: the portal
serves existing paying customers, and the new site serves nobody until it is
live. A cutover that fails back to the previous state costs a deploy window. A
cutover that stays up while the portal is broken costs customers. **When in
doubt, roll back.**

---

## 9. Measured vs proposed — one table

| Statement | Kind |
|---|---|
| All four names' A/CNAME values and TTLs (§2.1) | **MEASURED** |
| `ns1`–`ns4.zonedns.vn`; `ns7`/`ns8.alidns.com` (§2.1) | **MEASURED** (delegation, not account) |
| Apex and www 302 → `khachhang`; `khachhang` 200; `apiviporder.com` 200 (§2.2) | **MEASURED** |
| Every path on the apex redirects; target is malformed for non-root paths (§2.2) | **MEASURED** |
| `Server: Apache/2` on `103.159.50.70` (§2.2) | **MEASURED** |
| No HSTS on the live host (§2.2) | **MEASURED** |
| Certificates, issuers, validity, SANs; separate portal certificate (§2.3) | **MEASURED** |
| Apex, www and `khachhang` share the address `103.159.50.70` (§2.1) | **MEASURED** |
| The exact Apache rule producing the redirect | **NOT MEASURABLE** from outside — owner input (§4.4) |
| DNS control panel / account name | **NOT MEASURABLE** — owner input (§4.1) |
| Server hostname, OS, SSH access | **NOT MEASURABLE** — owner input (§4.3) |
| The portal's renewal method | **NOT MEASURABLE** — owner input (§4.6) |
| Any TTL value in §6.2 | **PROPOSAL** |
| "No DNS record changes at all" (§6.1) | **PROPOSAL** (the measured starting point is compatible with it) |
| Running nginx in front of Apache (§6.4, §6.6) | **PROPOSAL** |
| Adding a `server_name khachhang…` block and a port-80 ACME location (§6.3) | **PROPOSAL** |
| Dropping `includeSubDomains` from HSTS (§6.4) | **PROPOSAL** — weakens a control; owner decides |
| Every command in §7 and §8 | **PROPOSED procedure** — none has been run against a cut-over host |
