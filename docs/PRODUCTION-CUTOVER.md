# Production cutover — VIPORDER.COM.VN

> ## Status: PLAN ONLY. **PREPARE, DO NOT EXECUTE.**
> Nothing in this document has been run. No DNS record was changed, no server was
> touched, no certificate was issued, no configuration was installed. The live
> host was only ever *read* over the public internet — DNS queries, `GET`/`HEAD`,
> TLS handshakes, and **no `POST`**.
>
> Written against `docs/final-packs` @ `ae6c53a` (`origin/develop` @ `ae6c53a`).
> Every `file:line` anchor below was read at that revision.

---

## 0. How this document relates to `docs/DNS-CUTOVER.md`

**Read `docs/DNS-CUTOVER.md` first.** It is the measurement record: DNS, HTTP,
TLS and certificate facts taken on 2026-10-02, quoted verbatim, with every
statement labelled MEASURED or PROPOSED. **This document does not repeat those
measurements and does not replace that evidence.** It adds three things the older
document does not have: a decided HSTS value, a timed window (T-24h → T+30m), and
numeric rollback criteria.

### 0.1 What this document supersedes or narrows

Where the two disagree **only** in the rows below. Nowhere else.

| `docs/DNS-CUTOVER.md` | Status here | Why |
|---|---|---|
| §2.6 — *"This repository's proposed configuration sends … `max-age=31536000; includeSubDomains`"* (`:383-384`) | **SUPERSEDED** | At `ae6c53a` the header is `max-age=31536000` **without** `includeSubDomains` (`deploy/nginx/viporder-security-headers.conf:45`). The quoted value is from an earlier revision. |
| §2.4 finding 4 (`:346-347`) — *"the repository's configuration sends HSTS with `includeSubDomains`"* | **SUPERSEDED** | Same reason. |
| §6.4(c) (`:588-596`) — HSTS as *"the single most dangerous interaction in the plan"* | **NARROWED** | The hazard mechanism it describes was created by `includeSubDomains`. That is withheld, so the mechanism no longer applies to what nginx serves. **One part is not closed** — see §3.3 here, which is new and has not been measured. |
| §6.4 mitigation 3 (`:613-616`) — *"Consider dropping `includeSubDomains` … a decision for the owner"* | **SUPERSEDED** | The decision is made and shipped. Reasoning recorded at `deploy/nginx/viporder-security-headers.conf:29-43` and `docs/SECURITY.md` §5.2 (`:590-610`). |
| §6.7 T-1h (`:763-764`) — *"Decide and record the HSTS decision"* | **NARROWED** | No longer a decision. It is now a **read-back** that the installed snippet still withholds it (§4.2 here). |
| §9 table (`:1132`) — *"Dropping `includeSubDomains` from HSTS — PROPOSAL"* | **SUPERSEDED** | It is implemented, not proposed. |
| §6.7 checklist (`:733-777`) and §8 (`:1076-1108`) | **NARROWED** | Replaced as the *operating* form by §4 (timed sections) and §5 (numeric rollback criteria) below. The underlying triggers keep their names **T1–T10** so the two documents stay cross-referenceable, and the *reasons* in §8 remain valid and unchanged. |
| §6.7 T-0 (`:767-773`) — *"Deploy the apex vhost only"* | **NARROWED** | Sequenced explicitly in §4.3 here. A configuration that binds 443 cannot be installed while Apache holds 443 (§6.4(a) of the older document), so the portal path must be re-proven **before** the apex is switched. The intent of the older text is preserved; the order is made explicit. |

### 0.2 What remains valid, unchanged, and must still be read

| `docs/DNS-CUTOVER.md` | Still the authority for |
|---|---|
| §2.1 DNS measurements | apex / `www` / `khachhang` all `103.159.50.70`; `apiviporder.com` a different host in a different zone |
| §2.2 HTTP behaviour | the live redirect chain, **and the malformed redirect target** (restated in §2 here) |
| §2.3 TLS measurements | the apex certificate's SAN set is exactly `{viporder.com.vn, www.viporder.com.vn}` — it does **not** cover `khachhang` |
| §2.5 | `apiviporder.com` is **not in this cutover** in any respect |
| §3 | the TARGET architecture |
| §4 | the eight values only the owner can supply |
| §6.1 | **if this plan is followed, no DNS record changes at all** |
| §6.2 | lower the TTLs *before* the window, and why a TTL lowered at the same moment does nothing |
| §6.3 | what the reverse proxy must do, and the **two gaps** (no `server_name khachhang…` block; no port-80 ACME location for the portal) |
| §6.4(a)(b)(d) and mitigations 1, 2, 4 | the four mechanisms by which the portal can be broken without touching its DNS, and the three mitigations that still apply |
| §6.5 | loop avoidance, the "a link is not a redirect" rule, and the four-item loop checklist |
| §6.6 | Apache today vs nginx in this repository, and what that implies |
| §6.8 cases A/B/C | the rollback *procedure*, quoted and referenced by §6 here |
| §7 | the verification **commands** — this document references them instead of rewriting them |
| §8 | the rollback **reasons** — this document only adds thresholds to them |

**One thing in the older document is now false and must not be relied on:** its
statement that the repository emits `includeSubDomains`. It did, at
`8f83043`-era revisions. It does not at `ae6c53a`.

---

## 1. Targets, and the one invariant

| Name | Behaviour after a successful cutover | Source of the target behaviour |
|---|---|---|
| `viporder.com.vn` | serves the new website — `200`, the page titled `VIPORDER - Vận chuyển & nhập hàng Trung Quốc` (`index.html:6`) | `deploy/nginx/viporder.com.vn.conf:73-180` |
| `www.viporder.com.vn` | **301** to `https://viporder.com.vn$request_uri` — canonical, path preserved | `deploy/nginx/viporder.com.vn.conf:59-68` (`:67`) |
| `khachhang.viporder.com.vn` | **the EXISTING legacy portal. PRESERVE.** Not served here, not proxied, not modified | `deploy/nginx/viporder.com.vn.conf:13-14`; `docs/DEPLOYMENT.md:22-23,36-37` |

Also unchanged and out of scope: `apiviporder.com` (`docs/DNS-CUTOVER.md` §2.5).

### 1.1 The invariant

> `khachhang.viporder.com.vn` gets **no DNS change, no certificate change, no
> document-root change, and no vhost rewrite** as part of this cutover. If the
> portal must change, it is a separate change with its own plan, window and
> rollback.
> — `docs/DNS-CUTOVER.md:620-623`

That is the whole plan in one sentence. Everything else in this document exists
because **that sentence is not sufficient by itself**, for one measured reason:

> `viporder.com.vn`, `www.viporder.com.vn` and `khachhang.viporder.com.vn` all
> resolve to **the same address**, `103.159.50.70`.
> — `docs/DNS-CUTOVER.md:143-146`, measured 2026-10-02

One host serves the public apex, the www alias and the live customer portal.
Anything done to that host can take the portal down even though its DNS record was
never touched. The four mechanisms are enumerated at `docs/DNS-CUTOVER.md` §6.4
and none of them is repeated here — read them before you agree to a window.

### 1.2 The asymmetry that decides every judgement call

The portal serves existing paying customers. The new site serves nobody until it
is live. **A cutover that fails back to the previous state costs a deploy window.
A cutover that stays up while the portal is broken costs customers.**
(`docs/DNS-CUTOVER.md:1104-1108`.) When in doubt, roll back.

---

## 2. The inherited defect this cutover must not reproduce

**Measured 2026-10-02** (`docs/DNS-CUTOVER.md:234-244`):

```
$ curl -sS -o /dev/null -D - "https://khachhang.viporder.com.vnrobots.txt"
curl: (6) Could not resolve host: khachhang.viporder.com.vnrobots.txt

$ dig +noall +answer +comments A khachhang.viporder.com.vnrobots.txt
;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 33567
```

The apex redirect target is **malformed for every non-root path**. The `Location`
is the target string concatenated with the remaining path with **no `/`
separator**:

| Requested today | `Location` today |
|---|---|
| `/` | `https://khachhang.viporder.com.vn` *(correct only because the remaining path is empty)* |
| `/robots.txt` | `https://khachhang.viporder.com.vnrobots.txt` |
| `/sitemap.xml` | `https://khachhang.viporder.com.vnsitemap.xml` |
| `/api/v1/registrations` | `https://khachhang.viporder.com.vnapi/v1/registrations` |
| `/nonexistent-xyz` | `https://khachhang.viporder.com.vnnonexistent-xyz` |

**Consequence today:** `https://viporder.com.vn/robots.txt` and
`https://viporder.com.vn/sitemap.xml` do **not** return their file contents, and
`https://viporder.com.vn/api/v1/registrations` is answered with a 302 before the
request ever reaches an application (`docs/DNS-CUTOVER.md:249-253,660-675`).

### 2.1 The requirement, stated so it can be checked by a command

The replacement must **preserve the path**. The repository's `www` block does
this by construction:

```
deploy/nginx/viporder.com.vn.conf:67
    return 301 https://viporder.com.vn$request_uri;
```

`$request_uri` **begins with `/`**, so the separator cannot be lost. A check that
the defect was not inherited:

```zsh
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  https://www.viporder.com.vn/robots.txt
```

* **Expected:** `301 https://viporder.com.vn/robots.txt`
* **Failure — rollback trigger R5:** a target containing `viporder.com.vnrobots`,
  i.e. the separator bug reproduced in nginx.

### 2.2 The defect is *behaviour*, not a configuration line

The exact server-side rule that produces it is **not measurable from the public
internet** and is **owner input** (`docs/DNS-CUTOVER.md` §4 #4). Read and archive
the live Apache vhosts before anything is installed — **they are the rollback**
(`docs/DNS-CUTOVER.md` §6.6 item 1).

**Say this out loud before the window:** a rollback restores the previous *public
behaviour*, **defect included**, because that defect is a state the business has
already been in and no customer is harmed by returning to it
(`docs/DNS-CUTOVER.md:836-840`). Do not "improve" the rollback under pressure.

---

## 3. The HSTS warning

### 3.1 What ships, and what must not be added

`deploy/nginx/viporder-security-headers.conf:45`:

```
add_header Strict-Transport-Security "max-age=31536000" always;
```

* **`includeSubDomains` is deliberately NOT sent, and must stay that way for
  now.** The reasoning is in that file's own comment at lines 29-43 and in
  `docs/SECURITY.md` §5.2 (`:590-610`). The staged plan is: serve `max-age` alone → complete
  the cutover → confirm every subdomain is on valid HTTPS and intends to remain
  there → **then** add `includeSubDomains` in a separate, deliberate change.
* **`preload` must not be added.** `preload` requires `includeSubDomains`; adding
  it while the snippet withholds `includeSubDomains` is a structural
  contradiction, and adding it after would defeat the staged plan at lines 41-43
  by making the commitment effectively permanent in browser binaries rather than
  a header you can change next month.

### 3.2 Why this makes the cutover recoverable

`includeSubDomains` binds **every subdomain** to HTTPS for a year, and the browser
caches it. `khachhang.viporder.com.vn` shares an IP with the apex and has its own
certificate that the apex certificate does not cover (`docs/DNS-CUTOVER.md`
§2.3). So:

| | `includeSubDomains` **enabled** | `includeSubDomains` **withheld** (what ships) |
|---|---|---|
| A visitor loads the new apex once | their browser holds a 1-year policy for `viporder.com.vn` **and every subdomain** | a 1-year policy for **the exact hostname** `viporder.com.vn` only |
| The cutover then breaks the portal's TLS | **hard, non-bypassable error** for that visitor. Rolling the server back **does not release them** — the header that causes the failure is already on their machine, and only the year expiring clears it | an **ordinary** certificate/TLS failure, visible and fixable by restoring the portal's listener. No browser holds any cached policy for `khachhang` |
| Time to recover | up to **365 days**, per affected visitor | minutes, once the portal's listener is restored |

**That asymmetry is the entire reason the header is staged, and it is why a
cutover that breaks a subdomain is recoverable *today* and would not be if
`includeSubDomains` were enabled.** Do not enable it during this window, and do
not enable it after the cutover until a full `max-age` has been served without
incident and every subdomain has been measured.

`max-age=31536000` alone is still a real commitment on the apex itself: a browser
that loads `https://viporder.com.vn/` will refuse plain HTTP to that hostname for
a year. That is intended. It also means **a rollback does not unpin the apex for
visitors who already loaded it** — which is acceptable, because the apex was on
HTTPS before the cutover too (`http://viporder.com.vn/` already `301`s to HTTPS,
measured `docs/DNS-CUTOVER.md:162-169`).

### 3.3 The one part that is NOT closed — reported, not glossed

The recoverability argument in §3.2 holds **only while every response served from
`viporder.com.vn` withholds `includeSubDomains`.** HSTS is host-scoped, not
path-scoped: one response carrying `includeSubDomains` is enough for the browser
to apply the policy to the whole host and its subdomains.

**The two tiers of this stack disagree in the configuration:**

| Tier | Header it emits | Where |
|---|---|---|
| nginx (the static site) | `max-age=31536000` | `deploy/nginx/viporder-security-headers.conf:45` |
| the application (proxied under `/api/`) | `max-age=31536000; includeSubDomains` | `backend/app/middleware.py:253` |

nginx `add_header` **adds to** a proxied response; it does not replace the
upstream's headers, and there is **no `proxy_hide_header
Strict-Transport-Security`** anywhere in `deploy/`. So the configuration implies
that a response from `/api/*` carries **two** `Strict-Transport-Security`
headers, one of which contains `includeSubDomains`.

**Nobody has measured that response.** This is recorded as an **open item, not a
defect**, because it has not been observed — the middleware only emits HSTS over
HTTPS (`backend/app/middleware.py:252-262`), and both tiers have never been run
together on a live host.

**Measure it at T-1h, before the window, and do not start the window until the
answer is known:**

```zsh
# Count AND values. A count of 2 is the finding; read both lines.
curl -sSI "https://$STAGING_HOST/api/v1/health" | grep -i '^strict-transport-security'
```

If two headers come back and one contains `includeSubDomains`, then §3.2's
recoverability argument does **not** hold for `/api/*`, and one of these must be
done **before** the cutover, as a separate, reviewed change:

1. add `proxy_hide_header Strict-Transport-Security;` to the two `/api/`
   locations (`deploy/nginx/viporder.com.vn.conf:115-131`), leaving nginx as the
   single source of the header; **or**
2. stop the application emitting HSTS at all, on the grounds that the TLS
   terminator is the right place for it.

Do not decide this in the window.

---

## 4. The window

### 4.0 Which check runs at which stage

`C` = capture the BEFORE value (this is what "did we break it?" is answered
against). `V` = verify. `—` = not applicable at this stage.

| Check | T-24h | T-1h | T0 | T+5m | T+30m |
|---|---|---|---|---|---|
| DNS | C | V | V | V | V |
| TLS | C | V | V | V | V |
| nginx | V (staging copy) | V | V | V | V |
| health | C | V | V | V | V |
| registration | C | V | V | V | V |
| tracking | C | V | V | V | V |
| login redirect | C | V | V | V | V |
| robots | C | V | V | V | V |
| sitemap | C | V | V | V | V |
| analytics | C | V | V | — | V |
| logs | V (baselines) | V | V | V | V |

At T-24h several of these have **no working endpoint to capture** — today the
apex answers `302` for every path and there is no `/api/v1/health` on the public
host. Capture the *redirect behaviour* in those cases; the point of the C column
is to have a recorded BEFORE, not a healthy one.

### 4.1 Shared preamble

```zsh
# zsh. Replace every <<FILL IN>>. Quoted on purpose.
SHA="<<FILL IN: 40-hex release SHA, the value CI printed as 'Verified revision:'>>"
ROLLBACK_SHA="<<FILL IN: read /srv/viporder/RELEASE before deploying>>"
SRC=/srv/viporder/src
SITE=/srv/viporder/site
APP=/srv/viporder/app
VENV=/srv/viporder/venv
WINDOW="$HOME/viporder-cutover"
mkdir -p "$WINDOW"
echo "capture started $(date -u +%Y-%m-%dT%H:%M:%SZ) sha=$SHA" | tee "$WINDOW/timeline.txt"
```

Every capture goes into `$WINDOW`, named by stage. Run everything **from outside
the server** — `docs/DNS-CUTOVER.md:846-847`: nothing here trusts
`systemctl status`.

The four names are always all four, in this order:

```zsh
for h in viporder.com.vn www.viporder.com.vn khachhang.viporder.com.vn apiviporder.com; do
  printf '%-32s %s\n' "$h" "$(dig +short A "$h" | tr '\n' ' ')"
done
```

### 4.2 T-24 h — make the change reversible before making it

**Owner actions.** These are `docs/DNS-CUTOVER.md` §6.7 T-24h, plus three items
this document adds. **Do all of them; a window started without them is not this
plan.**

| # | Action | Evidence that it is done |
|---|---|---|
| 1 | **Decide the topology**: nginx in front of Apache (preferred, §6.4 mitigation 1), or nginx replaces Apache. Record it. | the decision written into `$WINDOW/timeline.txt` |
| 2 | **Read and archive the live Apache vhosts for `viporder.com.vn` AND `khachhang.viporder.com.vn`. Store them off-host. This is the rollback.** | the two files off-host, with `sha256sum` recorded |
| 3 | Record the OS and the installed web-server packages on `103.159.50.70` | package list captured |
| 4 | Lower TTLs: `www` 3600 → 300; apex 600 → 300; **`khachhang` unchanged at 600** | §4.2.1 below |
| 5 | Confirm `KHAIBAO9610_MODE` will stay `mock` (`docs/DEPLOYMENT.md:242-243`) | a written confirmation |
| 6 | Confirm the release SHA, and that the working tree is clean | `git -C "$SRC" rev-parse HEAD` == `$SHA`; `git -C "$SRC" status --porcelain` prints nothing |
| 7 | **NEW — resolve the two proxy gaps by design**, not in the window: there is no `server_name khachhang.viporder.com.vn` block, and the port-80 block lists only the two apex names so there is **no ACME location for the portal** (`docs/DNS-CUTOVER.md` §6.3, `:545-555`) | a decided design, written down |
| 8 | **NEW — read back that HSTS still withholds `includeSubDomains`** | §4.2.2 below |
| 9 | **NEW — confirm the portal's certificate has a working renewal path** and note its expiry | §4.2.3 below |

#### 4.2.1 DNS

```zsh
dig +noall +answer A viporder.com.vn
dig +noall +answer A www.viporder.com.vn
dig +noall +answer A khachhang.viporder.com.vn
dig +noall +answer A apiviporder.com
dig +noall +answer NS viporder.com.vn
```

**Success looks like** four names captured, and the NS set still
`ns1`–`ns4.zonedns.vn` (`docs/DNS-CUTOVER.md:104-108`).

Then lower the TTLs in the DNS control panel — **which is owner input; this
document does not name it** (`docs/DNS-CUTOVER.md` §4 #1).

**Success looks like, after the previous TTL has expired** (wait > 3600 s for
`www`, > 600 s for the apex — `docs/DNS-CUTOVER.md:517-520`): `dig` returning
**300** for the apex A and the `www` CNAME, and **still 600** for `khachhang`.

**If it fails:** if you cannot edit the DNS zone, **stop**. Every rollback path in
`docs/DNS-CUTOVER.md` §6.8 assumes you can. Note honestly that, per §6.1, *if this
plan is followed no DNS record changes at all* — the TTL exercise buys only a fast
emergency record change, so a failure here is a reason to record the risk, not
automatically to abort.

**Do not touch the `khachhang` record** — not its TTL, not its value.

#### 4.2.2 HSTS read-back

```zsh
grep -n 'add_header Strict-Transport-Security' \
  /etc/nginx/snippets/viporder-security-headers.conf
```

**Success looks like** exactly:

```
45:add_header Strict-Transport-Security "max-age=31536000" always;
```

**If it fails** — if `includeSubDomains` or `preload` appears — **stop**. Do not
start the window until the snippet matches
`deploy/nginx/viporder-security-headers.conf` at `$SHA`. This single line is what
makes §3.2 true.

#### 4.2.3 The portal's certificate

```zsh
/opt/homebrew/bin/python3.12 - <<'PY'
import ssl, socket
for h in ["viporder.com.vn", "www.viporder.com.vn", "khachhang.viporder.com.vn"]:
    ctx = ssl.create_default_context()
    with socket.create_connection((h, 443), timeout=20) as s:
        with ctx.wrap_socket(s, server_hostname=h) as ss:
            print(h, "->", ss.getpeercert()["notAfter"])
PY
```

**Baseline measured 2026-10-02** (`docs/DNS-CUTOVER.md` §2.3) — **re-measure; these
will have moved if renewal has run**:

* `viporder.com.vn` — `Dec 29 11:14:47 2026 GMT`, SANs `{viporder.com.vn, www.viporder.com.vn}`
* `khachhang.viporder.com.vn` — `Dec 28 06:08:52 2026 GMT`, SANs
  `{khachhang.viporder.com.vn, www.khachhang.viporder.com.vn}`

**Success looks like** three `notAfter` dates recorded in `$WINDOW/tls-t24h.txt`,
and the portal's expiry **not earlier** than the 2026-10-02 baseline.

**If it fails:** if the portal certificate is inside 30 days of expiry or its
renewal path is broken, **fix renewal first**. A predictable expiry is the one
outage that is entirely avoidable (`docs/DNS-CUTOVER.md:1099-1101`).

#### 4.2.4 Capture the BEFORE behaviour of all four names

```zsh
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) T-24h ==="
  for h in viporder.com.vn www.viporder.com.vn khachhang.viporder.com.vn apiviporder.com; do
    echo "--- $h ---"
    curl -sS -o /dev/null -D - "https://$h/" 2>&1 | head -12
  done
  echo "--- every-path behaviour on the apex ---"
  for p in / /robots.txt /sitemap.xml /index.html /api/v1/registrations /nonexistent-xyz; do
    printf '%-30s %s\n' "$p" \
      "$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "https://viporder.com.vn$p")"
  done
  echo "--- portal identity ---"
  curl -sSI https://khachhang.viporder.com.vn/ | tr -d '\r' | grep -iE '^(etag|last-modified|content-length)'
} | tee "$WINDOW/before-t24h.txt"
```

**Success looks like** a file that reproduces `docs/DNS-CUTOVER.md` §2.2: apex
`302` to `khachhang`, portal `200`, and the **malformed** `/robots.txt` and
`/sitemap.xml` targets with no `/` separator. If the observed behaviour does **not**
match the measurement, **re-measure and stop** — the host changed under you
(`docs/PROJECT-STATUS.md:76-77`: *"If a row and a PR disagree, the PR is wrong —
re-measure"*).

**The portal's `etag` and `last-modified` in that file are the reference values
every later check compares against.** Measured 2026-10-02 they were
`"bf5-65c996fd53352"` and `Tue, 29 Sep 2026 06:38:56 GMT`; they will be whatever
your capture says, not these.

### 4.3 T0 — the window

**Order matters, and this is the explicit sequencing that narrows
`docs/DNS-CUTOVER.md` §6.7 T-0.**

#### T0.0 Preconditions

```zsh
cd "$WINDOW"
cat before-t24h.txt >/dev/null || echo "STOP: no T-24h capture"
# Substitute the archive paths you actually recorded at T-24h step 2.
# /root/rollback/ is the example path used by docs/DNS-CUTOVER.md:815 -- it is a
# convention, not a fact about your host.
sudo test -f "<<FILL IN: path to the archived viporder.com.vn Apache vhost>>" \
  || echo "STOP: no archived Apache vhost"
sudo test -f "<<FILL IN: path to the archived khachhang Apache vhost>>" \
  || echo "STOP: no archived portal vhost"
```

**Success looks like** no `STOP` line printed. Any `STOP` means T-24h was not
finished and the window does not open.

#### T0.1 Apply the topology change, preserving the portal's path

A configuration that binds 443 **cannot** be installed while Apache holds 443
(`docs/DNS-CUTOVER.md` §6.4(a)). And if nginx becomes the only listener on 443,
a request for `khachhang…:443` matches the apex server block — because nginx
falls back to the default server when no `server_name` matches — and presents a
certificate that does **not** include `khachhang`. The browser fails the
handshake while the portal's DNS record is perfectly correct (§6.4(b)).

So, before anything is switched for the apex, one of:

* **Preferred:** move Apache to a loopback port and give nginx a
  `server_name khachhang.viporder.com.vn` block that `proxy_pass`es to it,
  **with the portal's own certificate**
  (`/etc/letsencrypt/live/khachhang.viporder.com.vn/…`) and its own ACME location
  on port 80; **or**
* nginx takes 80+443 on a **second address** and Apache keeps 443 for the portal.

**Neither is the default of either configuration file** (`docs/DNS-CUTOVER.md`
§6.6 item 6). This is a deliberate change and it must be recorded as one.

#### T0.2 `nginx -t` before anything is reloaded

```zsh
sudo nginx -t
```

**Success looks like** `syntax is ok` / `test is successful`.

**If it fails:** the two failure modes that matter are `host not found in
upstream` (wrong upstream variant installed — you need
`deploy/nginx/upstream-systemd.conf`, installed as
`/etc/nginx/upstreams/viporder-upstream.conf`) and `cannot load certificate`.
**Do not reload on a failed test.** `nginx -t` is not a substitute for testing
the portal (`docs/DNS-CUTOVER.md:712-715`).

#### T0.3 **Prove the portal first** — before the apex is switched

```zsh
curl -sSI https://khachhang.viporder.com.vn/ | tr -d '\r' | grep -iE '^(HTTP|etag|last-modified|server)'
```

**Success looks like** an `HTTP/2 200` (or `HTTP/1.1 200`), and `etag` /
`last-modified` **byte-identical to `before-t24h.txt`**.

**If it fails — this is rollback trigger R1/R2 and it is the primary one. It does
not require a second opinion.** The portal was supposed to be untouched. Roll
back (`docs/DNS-CUTOVER.md` §6.8 case A or B) and re-read §6.4.

#### T0.4 Switch the apex

Install the apex vhost / remove the catch-all for the apex. Keep the archived
Apache portal vhost in place on disk whatever the topology.

```zsh
sudo nginx -t
sudo systemctl reload nginx      # reload, not restart, if the topology allows
```

**Success looks like** `test is successful`, then a silent reload.

#### T0.5 The full T0 check

Run all of this. It is `docs/DNS-CUTOVER.md` §7 with the numeric gates of §5 here.

**DNS**

```zsh
dig +noall +answer A viporder.com.vn
dig +noall +answer CNAME www.viporder.com.vn
dig +noall +answer A khachhang.viporder.com.vn
dig +noall +answer A apiviporder.com
```
**Expected:** apex `103.159.50.70` (TTL 300); `www` CNAME → `viporder.com.vn`
(TTL 300); `khachhang` `103.159.50.70` **TTL 600**; `apiviporder.com`
`8.217.152.4` unchanged. **Any change to `khachhang` or `apiviporder.com` is an
unintended change** — the plan calls for no record change at all
(`docs/DNS-CUTOVER.md` §6.1). Trigger **T3**.

**TLS**

Use the SAN-reading block from `docs/DNS-CUTOVER.md:914-921` **verbatim** — copy
it from that document rather than hand-typing it, because a typo in a certificate
check produces a reassuring result for the wrong reason:

```zsh
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

**Expected:** apex SANs include `viporder.com.vn` and `www.viporder.com.vn`;
`khachhang` SANs include `khachhang.viporder.com.vn` and
`www.khachhang.viporder.com.vn`; and the expiry dates are **not earlier than**
the T-24h capture. **A name mismatch on any of the four names is trigger T5 →
roll back.**

**nginx**

```zsh
curl -sSI https://viporder.com.vn/ | tr -d '\r' | grep -i '^server:'
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://www.viporder.com.vn/robots.txt
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://viporder.com.vn/
sudo tail -20 /var/log/nginx/viporder.error.log
```
**Expected:** `server: nginx` (a value containing `Apache` means the old vhost is
still answering and the cutover did not happen — `docs/DNS-CUTOVER.md:857-862`);
`301 https://viporder.com.vn/robots.txt` **with the separator**;
`301 https://viporder.com.vn/`; and no `[emerg]`/`[alert]` line.

**health**

```zsh
curl -sS https://viporder.com.vn/api/v1/health | python3 -m json.tool
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/api/v1/health
```
**Expected:** `200`, a body reporting `"status":"ok"` and a `"mode"` field. **Read
the `mode` value.** While it says `mock`, real registration is **not** live
(`deploy/post-deploy-check.sh:70`). Do not report the site as taking real
registrations.

**registration**

```zsh
./deploy/post-deploy-check.sh https://viporder.com.vn
```
**Expected:** the registration probe returns **400 or 422**.
**`429` is a WARN, not a PASS** — it means rate limiting blocked the probe and
validation was **not** exercised (`deploy/post-deploy-check.sh:159`).
**Do not send a real registration.** Registration is the one path that creates
state in a third-party system, and `KHAIBAO9610_MODE=http` is forbidden until
issue #4 is resolved (`docs/DEPLOYMENT.md:242-243`). Trigger **T8**.

**tracking**

```zsh
curl -sS -o /dev/null -w 'warehouse %{http_code}\n' \
  "https://viporder.com.vn/api/tracking/warehouse-imports/KY4001103376087-2-4-%7Cs"
curl -sS -o /dev/null -w 'sealing   %{http_code}\n' \
  "https://viporder.com.vn/api/tracking/package-sealings/A1918106"
curl -sS -o /dev/null -w 'hostile   %{http_code} (must be 400)\n' \
  "https://viporder.com.vn/api/tracking/warehouse-imports/%3Cscript%3E"
```
**Expected with the shipped `mock` configuration: `503 PROVIDER_UNAVAILABLE`** on
both lookups — that is the correct, honest response, not a fault
(`docs/STAGING-RUNBOOK.md:858-899`). The hostile keyword must be **400**, always.
**Record the status AND the body's key names**, because the two upstream
envelopes differ (bare object vs `{"data":{...}}`).

**login redirect**

```zsh
curl -sS https://viporder.com.vn/ | grep -o 'https://khachhang.viporder.com.vn' | wc -l
curl -sS -o /dev/null -w '%{http_code}\n' https://khachhang.viporder.com.vn/
```
**Expected:** a **non-zero** count of portal links in the served HTML
(`index.html:134,186,277,729,740,809`). `0` means the served page is not this
repository's `index.html`. The portal must return `200` **and must not redirect
to the apex** (`docs/DNS-CUTOVER.md:686-692`).

Then in a browser: click the "existing customer" CTA and confirm it lands on
`https://khachhang.viporder.com.vn` in **one hop, with no bounce back**. The link
must be an `<a href>`; it must **never** be a server-side redirect, because a
redirect can be answered by another redirect and a link cannot
(`docs/DNS-CUTOVER.md:654-658`). Trigger **T7**.

**robots**

```zsh
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/robots.txt
curl -sS https://viporder.com.vn/robots.txt | grep -i '^sitemap:'
```
**Expected:** `200`, and `Sitemap: https://viporder.com.vn/sitemap.xml`
(`robots.txt:19`). **Before: `302` to a non-existent hostname.** Trigger **T6**.

**sitemap**

```zsh
curl -sS -o /dev/null -w '%{http_code}\n' https://viporder.com.vn/sitemap.xml
curl -sS https://viporder.com.vn/sitemap.xml | grep -o '<loc>[^<]*</loc>'
```
**Expected:** `200`, and **exactly one line** —
`<loc>https://viporder.com.vn/</loc>` (`sitemap.xml:20`). More than one means a page
was added to the sitemap without being added to the site.

**analytics**

```zsh
curl -sS https://viporder.com.vn/ | grep -c 'googletagmanager\|connect.facebook.net\|fbq('
```
> **Superseded 2026-10-10:** GA4 and Meta Pixel are now ENABLED (owner decision);
> the network panel SHOULD show `gtag/js?id=G-581GP4TP51` and `fbevents.js`. The
> text below is the cutover-time expectation, kept as history.

**Expected:** `0` occurrences of the loaders. The analytics layer is **dormant by
default** — `VIPORDER_TRACKING.enabled` is `false`
(`static/js/analytics.js:71-78`), and the third-party loaders are skipped while it
is (`:567,649-653`). Enabling it is **out of scope for this cutover** and is a
separate change: `docs/SECURITY.md` §11 risk 15 requires the nginx CSP to be
tightened at the same time. In a browser, the network panel must show **zero**
requests to `googletagmanager.com`, `google-analytics.com`, `region1.google-analytics.com`,
`connect.facebook.net` or `facebook.com`.

**logs**

```zsh
sudo tail -50 /var/log/nginx/viporder.access.log
sudo tail -50 /var/log/nginx/viporder.error.log
sudo tail -50 /var/log/viporder/app.err.log
sudo grep -c " 429 " /var/log/nginx/viporder.access.log
```
**Expected:** `200` on `/` in the access log; **no** new `[emerg]` or `[alert]`
and at most a handful of `[error]`; **no traceback** in `app.err.log`. A `429`
count you can explain from your own probes is expected behaviour — two limiters
apply (`10r/m` on `= /api/v1/registrations`, `120r/m` on `^~ /api/`,
`deploy/nginx/viporder.com.vn.conf:25-26,115-131`). Trigger **T9**.

#### T0.6 T0 gate

**Do not declare T0 complete** unless every line below is true:

* portal `200` with `etag` equal to the T-24h capture;
* apex `/` `200` on **5 consecutive** probes;
* `www` `/robots.txt` `301` with the path separator present;
* `robots.txt` and `sitemap.xml` `200` with their contents;
* zero TLS name mismatches on the four names;
* `/api/v1/health` `200` with `"status":"ok"`;
* registration probe `400`/`422`;
* zero new `[emerg]`/`[alert]`;
* `curl -sSL --max-redirs 10` completes on all four names.

Otherwise → §5 row that fired → §6.

### 4.4 T+5 m — the first confirmation the change is stable

```zsh
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) T+5m ==="
  curl -sSI https://khachhang.viporder.com.vn/ | tr -d '\r' | grep -iE '^(HTTP|etag|last-modified)'
  for h in viporder.com.vn www.viporder.com.vn khachhang.viporder.com.vn apiviporder.com; do
    printf '%-32s %s\n' "$h" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$h/")"
  done
  for p in / /robots.txt /sitemap.xml /api/v1/health; do
    printf '%-20s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://viporder.com.vn$p")"
  done
  curl -sSL --max-redirs 10 -o /dev/null -w 'max-redirs probe: %{http_code} (%{num_redirects} hops)\n' https://viporder.com.vn/
} | tee "$WINDOW/after-t5m.txt"

diff <(grep -iE '^(etag|last-modified)' "$WINDOW/before-t24h.txt") \
     <(grep -iE '^(etag|last-modified)' "$WINDOW/after-t5m.txt") \
  && echo "PORTAL IDENTITY UNCHANGED"
```

**Checks.** DNS (`dig` as at T0.5), TLS (the SAN block), nginx (`server:` header
still `nginx`), health (`200` × 3), registration (probe again — `400`/`422`),
tracking (`503` with `mock`, hostile `400`), login redirect (portal `200` and not
redirecting to the apex), robots (`200`), sitemap (`200`), logs.

**Success looks like:** `PORTAL IDENTITY UNCHANGED` printed; every port `200`
except `apiviporder.com` which is not in scope; **0** 5xx responses on `/api/*`
in the window; **0** new `[emerg]`/`[alert]`; **≤ 5** new `[error]` lines.

**If it fails:** the portal identity line failing is **R2** — roll back
immediately, no second opinion.

### 4.5 T+30 m — the window is open to the public

```zsh
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) T+30m ==="
  LC_ALL=C awk '{print $9}' /var/log/nginx/viporder.access.log | sort | uniq -c | sort -rn | head -20
  printf '5xx on /api/ last 15m: '
  sudo grep -c ' /api/.* 5[0-9][0-9] ' /var/log/nginx/viporder.access.log
  printf '429 on registration last 10m: '
  sudo grep -c 'POST /api/v1/registrations.* 429 ' /var/log/nginx/viporder.access.log
  sudo grep -cE '\[(emerg|alert)\]' /var/log/nginx/viporder.error.log
  sudo systemctl show viporder-web -p NRestarts -p ActiveState -p MainPID
  sudo systemctl list-timers certbot.timer --no-pager
} | tee "$WINDOW/after-t30m.txt"

for i in $(seq 1 20); do
  curl -sS -o /dev/null -w '%{time_total}\n' https://viporder.com.vn/api/v1/health
done | sort -n | awk '{a[NR]=$1} END {printf "health p95 = %.3f s over %d probes\n", a[int(NR*0.95)], NR}'

sudo certbot renew --dry-run
```

**Checks.** DNS, TLS, nginx, health, registration, tracking, login redirect,
robots, sitemap, analytics (browser network panel — zero third-party requests),
logs.

**Success looks like, all of it:**

* portal `200`, `etag` still equal to the T-24h capture;
* 5xx on `/api/*` ≤ **1 %** of `/api/*` requests over the window, and **< 5
  responses** absolute;
* `429`s on `/api/v1/registrations` ≤ **5** in 10 minutes, and 0 of them from a
  client you cannot identify as your own probing;
* `[emerg]`+`[alert]` count **0**; new `[error]` lines ≤ **10** in 5 minutes;
* `NRestarts=0`, `ActiveState=active`;
* health **p95 ≤ 1.000 s** over 20 probes;
* `certbot renew --dry-run` succeeds for **both** certificates;
* every path from `docs/DNS-CUTOVER.md` §2.2 now returns real content instead of
  a 302 — including `/api/v1/registrations` (which must now reach the app and
  return a validation status, not a redirect);
* no `ERR_TOO_MANY_REDIRECTS` in a browser, and the four-item loop checklist at
  `docs/DNS-CUTOVER.md:686-692` is all true.

**Then close the window**: append the final SHA, the T0 timestamp, the T+30m
timestamp and the observed numbers to `$WINDOW/timeline.txt`, and keep the file.
Per `docs/GO-LIVE-CHECKLIST.md:3-5`: an unticked box is fine; a ticked box without
evidence is not.

**If it fails:** the window is not closed. Apply §5 → §6.

---

## 5. Rollback criteria — explicit, numeric

Any single row below is sufficient. **No debate required.**
The names **T1–T10** are from `docs/DNS-CUTOVER.md:781-794`; this document adds
the thresholds.

### 5.1 Immediate — roll back now, no second opinion

| # | Fires when | Trigger |
|---|---|---|
| **R1** | `https://khachhang.viporder.com.vn/` is **anything other than `200`**, at any point, or any TLS error on that name | T3 / T5 |
| **R2** | The portal's `etag` **or** `last-modified` differs from the T-24h capture | T4 |
| **R3** | `https://viporder.com.vn/` is not `200` on the first probe, or on any **2 of 5** consecutive probes | T1 / T2 |
| **R4** | `ERR_TOO_MANY_REDIRECTS` anywhere, or `curl -sSL --max-redirs 10` exiting non-zero on any of the four names | T7 |
| **R5** | `https://www.viporder.com.vn/robots.txt` returns a `Location` containing `viporder.com.vnrobots` — the §2 defect reproduced — **or** `/robots.txt` / `/sitemap.xml` are not `200` within **5 minutes** of T0 | T6 |
| **R6** | Any TLS name mismatch on any of the four names | T5 |

### 5.2 Thresholds — roll back when crossed

| # | Metric | Threshold | How it is measured |
|---|---|---|---|
| **R7** | Unhandled application errors | **≥ 1** traceback or `ERROR` line attributable to a request in `app.err.log` | `sudo tail -200 /var/log/viporder/app.err.log` |
| **R8** | `/api/*` server errors | **> 5** responses with status `5xx` in any 5-minute window, **or** `> 1 %` of `/api/*` requests over the window, whichever is reached first | nginx access log, status column |
| **R9** | `/api/v1/health` latency | **p95 > 1.000 s** across 20 consecutive probes | the loop in §4.5 |
| **R10** | nginx health | **≥ 1** `[emerg]` or `[alert]`, **or ≥ 10** `[error]` lines in 5 minutes | `viporder.error.log` |
| **R11** | Registration validation | The invalid-payload probe does **not** return `400` or `422` — excluding `429`, which is a WARN and means *not exercised* | `deploy/post-deploy-check.sh` (T8) |
| **R12** | Rate limiting hurting real users | **> 5** `429`s on `POST /api/v1/registrations` in 10 minutes from distinct client addresses | nginx access log (T9) |
| **R13** | Portal certificate | `notAfter` **moves backwards** from the T-24h value, **or** `certbot renew --dry-run` fails for `khachhang.viporder.com.vn` | §4.2.3, `certbot renew --dry-run` |
| **R14** | Apex certificate | `notAfter` moves backwards from the T-24h value, or the SAN set shrinks | §4.2.3 |
| **R15** | Process stability | `NRestarts` **> 0** for `viporder-web` | `systemctl show viporder-web -p NRestarts` |
| **R16** | Consequence of a stale portal certificate | The portal certificate cannot renew and will expire **before 2026-12-28 06:08:52 GMT** *(baseline measured 2026-10-02; re-measure at T-24h)* | `docs/DNS-CUTOVER.md:1099-1101` |

### 5.3 Not a rollback trigger — read it correctly

`server: Apache/2` on the apex **after** T0 does not mean "roll back". It means
**the cutover did not happen** — the old vhost is still answering
(`docs/DNS-CUTOVER.md:857-862`). Fix the installation; the previous behaviour is
still live and no customer is affected.

---

## 6. Rollback, without damaging `khachhang`

The procedures are `docs/DNS-CUTOVER.md` §6.8 cases A, B and C. **Use them
verbatim.** This section adds only the constraints that protect the portal.

### 6.1 Case A — nginx in front of Apache (the preferred topology)

Only the apex needs undoing. `docs/DNS-CUTOVER.md:802-808`:

```zsh
# On the server. Verify the symlink target before removing it.
ls -l /etc/nginx/sites-enabled/viporder.com.vn.conf
rm /etc/nginx/sites-enabled/viporder.com.vn.conf
nginx -t && systemctl reload nginx
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://viporder.com.vn/
```

**Read the symlink target before you remove it.** `ls -l` first, every time.

### 6.2 Case B — nginx replaced Apache

Restore the archived Apache vhost, confirm Apache holds 443 again, then stop
nginx — `docs/DNS-CUTOVER.md:812-820`, with paths adjusted to what was actually
recorded at T-24h.

```zsh
cp /root/rollback/viporder.com.vn.apache.conf /etc/apache2/sites-available/
a2ensite viporder.com.vn
apache2ctl configtest
systemctl stop nginx
systemctl reload apache2
```

### 6.3 Case C — DNS was changed despite this plan

Lower the TTL first (`docs/DNS-CUTOVER.md` §6.2), restore the record, wait out the
old TTL, then confirm:

```zsh
dig +noall +answer A viporder.com.vn
```

### 6.4 Three things the rollback must NOT do

1. **Do not touch the `khachhang.viporder.com.vn` DNS record.** Not to "fix" it,
   not to "restore" it. It was never changed (§1.1).
2. **Do not touch, reissue, move or delete
   `/etc/letsencrypt/live/khachhang.viporder.com.vn/`.** That certificate belongs
   to the portal and the portal was never in scope (`docs/DNS-CUTOVER.md` §6.4,
   §6.7: *"Explicitly out of scope for this window: the portal's DNS record,
   certificate, document root and vhost"*).
3. **Do not stop or reload the web server without a passing `nginx -t` first**,
   and **never stop a process by matching its name.** `pkill -f`, `pkill`,
   `killall` are banned in this project (`CLAUDE.md` §16.7) — the flag matches the
   whole command line, and a pattern as short as `cat` has already taken down
   unrelated applications on a machine in this project. Stop services with
   `systemctl`; if you must `kill`, take the PID from `systemctl show -p MainPID`
   and look at the full command line before you signal it.

### 6.5 Verify the rollback from outside — never from the server

```zsh
curl -sS -I https://viporder.com.vn/            # 302 -> khachhang is the ACCEPTED prior state
curl -sS -I https://khachhang.viporder.com.vn/  # expect 200
curl -sSI https://khachhang.viporder.com.vn/ | tr -d '\r' | grep -iE '^(etag|last-modified)'
```

**Success looks like:** the portal returns `200` and its `etag`/`last-modified`
match the T-24h capture again, and the apex is back to the behaviour recorded in
`before-t24h.txt` — **including** the malformed redirect for non-root paths.

**That last point is deliberate.** A rollback restores the previous public
behaviour, defect included (§2.2). Restoring it is **not a failure**: the prior
behaviour is a state the business has already been in, and no customer is harmed
by returning to it. A failed cutover that rolls back cleanly is a good outcome; a
failed cutover that stays up because nobody wanted to admit failure is not
(`docs/DNS-CUTOVER.md:836-840`).

---

## 7. Values the owner must supply

The full list is `docs/DNS-CUTOVER.md` §4 (eight items) — **read it; it is not
repeated here.** This document adds only what the timed window needs on top:

| # | Value | Why this document needs it | Where the constraint comes from |
|---|---|---|---|
| 1 | **The three TTL values you can actually set**, and whether you can edit the zone at all | T-24h step 4 | `docs/DNS-CUTOVER.md` §6.2 |
| 2 | **The topology decision for the window** — nginx in front of Apache, or nginx replaces Apache | T0.1; it changes the portal's serving path either way | `docs/DNS-CUTOVER.md` §6.4 mitigation 1, §6.6 item 6 |
| 3 | **The design that gives `khachhang` its own 443 listener and its own port-80 ACME location** | T0.1; without it the portal gets the apex certificate (a name mismatch) and cannot renew | `docs/DNS-CUTOVER.md` §6.3, §6.4(b) |
| 4 | **The server's real hostname, SSH access and OS** | T-24h step 3; the nginx config assumes a Debian/Ubuntu layout | `docs/DNS-CUTOVER.md` §4 #3, §6.6 item 5 |
| 5 | **The current Apache vhost text for both names** | T-24h step 2; it is the rollback and the source of the malformed redirect | `docs/DNS-CUTOVER.md` §4 #4 |
| 6 | **A written confirmation that `KHAIBAO9610_MODE` stays `mock`** | T-24h step 5 and the T0 registration check | `docs/DEPLOYMENT.md:242-243` |
| 7 | **The maintenance window and the customer-notification plan** | the window itself | `docs/DNS-CUTOVER.md` §4 #7 |
| 8 | **A decision on the §3.3 `/api/*` HSTS duplication**, if the T-1h measurement shows two headers | it decides whether §3.2 is true | measured at T-1h; `backend/app/middleware.py:253`, `deploy/nginx/viporder-security-headers.conf:45` |

**This document names no DNS provider, no control panel, no hosting provider and
no credential**, because none is recorded in this repository and none has been
chosen (`docs/DNS-CUTOVER.md:462-464`).

---

## 8. What is NOT proven

1. **Nothing has been executed.** This is a plan. No DNS record, no certificate,
   no nginx configuration and no server was touched while writing it.
2. **The §3.3 HSTS question is unmeasured.** The configuration implies two
   `Strict-Transport-Security` headers on `/api/*`, one of them containing
   `includeSubDomains`. Nobody has observed it. Until it is measured at T-1h,
   **§3.2 is an argument from the nginx tier alone**, and the cutover must not be
   treated as fully subdomain-recoverable.
3. **The portal's server configuration is not visible from outside.** Whether
   `khachhang` is a static SPA on the same Apache host or is proxied to an
   application is **owner input** (`docs/DNS-CUTOVER.md` §4 #5). The cutover
   method depends on it.
4. **The portal's renewal method is not visible** — HTTP-01 on the same port 80,
   DNS-01, or manual (`docs/DNS-CUTOVER.md` §4 #6). §6.3's gap (no port-80 ACME
   location for the portal) cannot be closed until this is known.
5. **The exact Apache rule producing the malformed redirect has never been read**
   (`docs/DNS-CUTOVER.md` §4 #4). It must be read at T-24h, and the archived file
   is the only rollback.
6. **The repository's nginx configuration has never run on the production host.**
   What is verified is local only: syntax parses, the six security headers are
   present on `/`, a static asset and `robots.txt`, and there is exactly one
   `Cache-Control` per response — macOS, nginx 1.31.6. **Not verified:**
   production-host behaviour, TLS/OCSP stapling with a real certificate, HTTP/2
   negotiation, and the `/api/` proxy path with a backend running
   (`deploy/nginx/README.md:86-94`).
7. **The `/api/` proxy path has never been exercised end to end.** The T0 health
   check is the first time it will be.
8. **`apiviporder.com` is out of scope and stays out of scope** (`docs/DNS-CUTOVER.md`
   §2.5). Its records appear in these documents only because they were measured.
9. **The analytics layer must stay dormant through this cutover.** Enabling it is
   a separate change that also requires tightening the nginx CSP
   (`docs/SECURITY.md` §11 risk 15, `:1026`). Turning it on during the window adds a
   third-party failure mode to a window that already has a live-customer
   dependency.
10. **No backup has ever been restored** (`docs/DEPLOYMENT.md:224-227`).
11. **No log rotation is committed** (`deploy/systemd/viporder-web.service:91-92`;
    `docs/STAGING-RUNBOOK.md` §15 gap 4). A cutover onto a host with unbounded
    logs is a disk-full outage waiting for a date.
12. **The release SHA for the cutover has not been chosen**, and a staging pass is
    not a production authorization: the gates in `docs/GO-LIVE-CHECKLIST.md` are
    separate and must be ticked with their own evidence
    (`docs/STAGING-RUNBOOK.md:14-18`).
