# Staging host requirements — VIPORDER.COM.VN

**What this is.** A short, exact list of what the owner must obtain before a staging
host can exist. Written for the owner, not for an engineer.

**Revision this is derived from.** Branch `docs/prestaging` @ `7309a4c`
(== `origin/develop`). Every number below is taken from a named file in this
repository at that revision, and the file is named so it can be checked.

> ## Status: NOTHING OBTAINED, NOTHING DEPLOYED
> There is no staging host, no IP address, no DNS record and no credential. This
> document is a shopping list, not a report.

> ## Two things to read before trusting any number here
>
> 1. **Docker is not installed on the machine this was prepared on.** Recorded in
>    `docs/PROJECT-STATUS.md` ("Docker is not installed on the machine this was built
>    on… the stack has never been run") and in `docs/STAGING-RUNBOOK.md` §15 gap 6.
>    Every Docker-specific statement below is **derived from configuration and has
>    never been executed**. It is marked `[derived]` wherever it appears.
> 2. **This document names no provider, no control panel and no IP address**, because
>    none is recorded anywhere in this repository and none has been chosen. Where a
>    value is unknown it is left as a placeholder. Nothing here may be guessed.

---

## 1. Values you must supply

Nothing in this table can be invented. If a value is unknown, the deployment stops at
that step.

| # | What | Why it is needed | Where the constraint comes from |
|---|---|---|---|
| 1 | **Real hostname** | The site must answer on a name a browser recognises, and TLS certificates are issued per name. | `deploy/nginx/viporder.com.vn.conf:43,62,77` names exactly `viporder.com.vn` and `www.viporder.com.vn`. A different name means editing `server_name` **and** the certbot `-d` flags **and** the certificate paths. |
| 2 | **Public IPv4 address of the host** | Needed to create the DNS record, and to test the site before DNS is live. Also the address the firewall rules key on. | `docs/STAGING-RUNBOOK.md` §0, `<STAGING_IP>` |
| 3 | **TLS contact email** | Let's Encrypt sends expiry warnings to it. | `certbot` requirement; `docs/STAGING-RUNBOOK.md` §0, `<TLS_CONTACT_EMAIL>` |
| 4 | **Database password** | The application connects to PostgreSQL with it. | `deploy/env.production.example` (`DATABASE_URL`), `deploy/docker-compose.yml:25` (`POSTGRES_PASSWORD` has no default — compose refuses to start without it) |
| 5 | **Admin API token** *(optional)* | Enables retrying a failed registration. **Empty means the admin route returns 404 — disabled, not merely unauthorised.** | `deploy/env.production.example` (`ADMIN_API_TOKEN`), `backend/app/config.py` |
| 6 | **SSH key** (recommended) or password | Everything is installed over SSH. See §7. | `docs/DEPLOYMENT.md` §4.2–4.8, `docs/STAGING-RUNBOOK.md` §3 |

Also useful, and asked for by the runbook: a **phone number you own** for the
registration test (`+84` + 9 digits starting with 2/3/5/7/8/9), and the **40-character
commit SHA** to deploy — CI verifies a PR head SHA, not a branch name
(`.github/workflows/ci.yml`).

**How to hand these over safely.** The password and the token must **not** go into the
repository, into a chat message, or into shell history. `docs/STAGING-RUNBOOK.md` §4.2
uses `\password viporder` at the PostgreSQL prompt, which does not echo and does not
reach the shell history.

---

## 2. The machine

| Item | Requirement | Basis |
|---|---|---|
| **OS** | **Debian 12 or Ubuntu 22.04+, 64-bit** | `docs/DEPLOYMENT.md` §2 states exactly this. `deploy/Dockerfile` builds `FROM python:3.12-slim`, `deploy/docker-compose.yml` uses `postgres:16-alpine` and `nginx:1.27-alpine` — all Debian-based, all multi-arch. |
| **CPU** | **1 vCPU minimum, 2 recommended** | Exactly **one** application process by design — see §2.1. Add PostgreSQL and nginx and that is three processes total. No artifact in this repository states a CPU requirement. |
| **RAM** | **2 GB recommended** (1 GB is the arithmetic floor) | See the breakdown in §2.2. |
| **Disk** | **20 GB minimum, 40 GB recommended** | See the breakdown in §2.3. |
| **Python** | **3.11 or 3.12** | `docs/DEPLOYMENT.md` §2, `docs/STAGING-RUNBOOK.md` §3. `deploy/Dockerfile` pins 3.12. |
| **Web server** | **Nginx 1.20 or newer** | `docs/DEPLOYMENT.md` §2. `deploy/docker-compose.yml` pins `nginx:1.27-alpine`. |
| **Database** | **PostgreSQL 16 recommended** (14+ acceptable; SQLite is staging-only) | See §5. |
| **Outbound network** | **443/tcp to the internet**; **80/tcp** if the HTTP-01 ACME challenge is used; **443/tcp to `github.com`** to check out the release | `deploy/nginx/viporder.com.vn.conf:46` serves `/.well-known/acme-challenge/` on plain HTTP. `docs/DEPLOYMENT.md` §4.1 checks out a tracked SHA on the server. |

### 2.1 One worker is deliberate, not a shortcut

Both deployment paths run the application with **`--workers 1`**:
`deploy/Dockerfile` (`CMD ["uvicorn", …, "--workers", "1"]`) and
`deploy/systemd/viporder-web.service` (`ExecStart=… --workers 1`).

The reason is in both files, and it is a correctness reason rather than a performance
one: **the rate limiter keeps its counters in process memory.** Each worker enforces
the limit independently, so with N workers the effective limit is
`N × RATE_LIMIT_ATTEMPTS`. At two workers, a limit of 10 becomes 20 in practice.
`deploy/env.production.example` says the same thing: raising this requires moving the
limiter to a shared store (Redis) first. **The concurrency guarantees are only verified
at one worker**, so do not size the machine on the assumption of scaling out later
without that work.

**Practical consequence for the purchase:** do not pay for many cores. This workload is
bounded by the database and the upstream provider, not by CPU.

### 2.2 RAM — where the number comes from

| Component | RAM | How it is known |
|---|---|---|
| Application (uvicorn, 1 worker) | **≤ 512 MB** | **Measured from configuration**: `deploy/systemd/viporder-web.service` sets `MemoryMax=512M`. systemd enforces this as a hard cap, so the application cannot exceed it. |
| nginx | ~20–40 MB | **~ estimate, anchored in configuration**: the two rate-limit zones are declared as `10m` each (`deploy/nginx/viporder.com.vn.conf`), so 20 MB of shared memory by configuration, plus the worker processes. |
| PostgreSQL 16 | ~200–400 MB | **~ estimate**. The upstream default `shared_buffers` is 128 MB. This is a PostgreSQL default, **not measured in this repository**. |
| Operating system | ~200–400 MB | **~ estimate** for a minimal Debian/Ubuntu server. Not measured here. |
| **Total** | **~0.5–1.3 GB** | 1 GB is the floor and leaves almost no headroom. **2 GB** gives room for `pip install`, `alembic upgrade head`, a backup running alongside the site, and the page cache that makes PostgreSQL fast. |

### 2.3 Disk — where the number comes from

| Item | Size | How it is known |
|---|---|---|
| **Public site** — what nginx actually serves (5 paths) | **248 KB** | **Measured** on this branch: `index.html` 28 KB, `404.html` 4 KB, `robots.txt` 4 KB, `sitemap.xml` 4 KB, `static/` 208 KB. |
| **Backend application code** | **612 KB** | **Measured**: `backend/` (`app/` 236 KB, `alembic/` 36 KB). |
| **Full git checkout** (including `.git`) | **2.6 MB** | **Measured**: 2.6 MB total, of which `.git` is 988 KB, 134 tracked files. |
| **Python 3.12 + the pinned virtualenv** | **~156 MB** | **Measured** at `/Users/duongthanh/code/viporder-com-vn/.venv` on **macOS 26.6.2 (arm64), 2026-10-02**. `backend/requirements.txt` pins 9 packages. A Debian/Ubuntu venv will differ somewhat — treat this as the order of magnitude, not the exact figure. |
| **OS + nginx + certbot + postgresql-client** | ~3–4 GB | **~ estimate**: a minimal Debian 12 install is roughly 1.5–2 GB and these packages add a few hundred MB each with their dependencies. |
| **PostgreSQL 16 data directory** | **< 1 GB for this workload** | **~ estimate**: the application's whole schema is the lead store — five migrations (`backend/alembic/versions/0001`…`0005`) creating one `leads` table and its indexes. Even a large number of registrations stays small. |
| **Backups — hourly, 30-day retention** | **~1.5 GB, and this grows** | **~ estimate**, and the largest single line item. `docs/DEPLOYMENT.md` §7 sets the frequency and retention (hourly → 720 dumps); the **dump size is not measured here**. The arithmetic assumes ~2 MB per dump at a low thousands of leads. Re-check once real data exists. |
| **Application logs** | **unbounded — size with headroom** | `deploy/systemd/viporder-web.service` appends to `/var/log/viporder/app.log` and `app.err.log`. `docs/STAGING-RUNBOOK.md` §15 gap 4 records that **no log rotation is committed**. This will grow until someone configures `logrotate`. |
| **Total** | **~5–7 GB before backups and logs** | Hence **20 GB minimum** (comfortable for backups, logs and OS updates), **40 GB recommended** so that a full disk — the failure that takes a database down hardest — is not the thing you discover on a Friday. |

**What is deliberately NOT in this budget** is the single largest thing on the machine
this was prepared on: see §8.

---

## 3. Ports

| Port | Direction | Who uses it | Basis |
|---|---|---|---|
| **80/tcp** | INBOUND | Nginx. Redirects everything to HTTPS **except** the ACME challenge path. | `deploy/nginx/viporder.com.vn.conf:41-42` `listen 80`; `:46` `location /.well-known/acme-challenge/`; `:51` `return 301 https://…`. Published in compose at `deploy/docker-compose.yml:79`. |
| **443/tcp** | INBOUND | Nginx, TLS. The only way the site is served. | `deploy/nginx/viporder.com.vn.conf:59-60,74-75` `listen 443 ssl`. Published in compose at `deploy/docker-compose.yml:80`. |
| **8000/tcp** | **LOOPBACK ONLY** | The application (uvicorn). **Must not be reachable from the internet.** | systemd: `--host 127.0.0.1` in `deploy/systemd/viporder-web.service`. Compose: `expose:` not `ports:` (`deploy/docker-compose.yml:69-70`), so it is not published to the host at all — nginx reaches it over the private compose network as `app:8000` (`deploy/nginx/upstream-compose.conf`). **Do not open this port in the firewall.** |
| **5432/tcp** | **LOOPBACK ONLY** | PostgreSQL. | Compose: `expose:` not `ports:` (`deploy/docker-compose.yml:34-35`). The systemd example URL is `…@127.0.0.1:5432/viporder` (`deploy/env.production.example`), so the host database should listen on loopback. **Do not open this port.** |
| **22/tcp** | INBOUND | SSH, for installation and administration. Owner-managed. | See §7. |
| **443/tcp** | OUTBOUND | Let's Encrypt (certbot) and, **only when enabled**, the KHAIBAO9610 upstream. | `deploy/env.production.example`: `KHAIBAO9610_BASE_URL=https://apiviporder.com/frontend/v1`. Note: `KHAIBAO9610_MODE=mock` and `KHAIBAO9610_ENABLE_REAL_CALLS=no` are the safe defaults, so **no outbound call happens until someone deliberately turns it on.** |
| **443/tcp** | OUTBOUND to `github.com` | Checking out the release on the server. | `docs/DEPLOYMENT.md` §4.1 (`git fetch --all && git checkout "$RELEASE_SHA"`). |

Only **80** and **443** should be open to the world. That is the whole external
surface.

---

## 4. Docker

**Is Docker required? No — and the recommended path does not use it.**

The repository ships **two alternative paths**, and `deploy/docker-compose.yml:4-5`
says: *"This is an ALTERNATIVE to the systemd deployment in deploy/systemd/. Use one or
the other, not both."*

| | systemd path **(recommended)** | compose path |
|---|---|---|
| Docker needed | **No** | Yes |
| Application | systemd unit `viporder-web.service` | `app` container |
| Nginx | host nginx | `nginx:1.27-alpine` container |
| Database | host PostgreSQL | `db` container (`postgres:16-alpine`) |
| Certificates | certbot on the host | `deploy/certs/` mounted into the container |

If you choose Docker anyway, the derived requirements are `[derived]` — from the
compose file's own syntax, never executed:

* **Docker Engine with the Compose v2 plugin** — `docker compose`, not the old
  `docker-compose`. `[derived]` The file relies on the top-level `name:` key and on
  `depends_on: condition: service_healthy`, which are Compose v2 features.
* **1 GB extra disk** for the three images (Python 3.12-slim, PostgreSQL 16-alpine,
  nginx 1.27-alpine) — `[derived]`, image sizes are not measured here.
* `POSTGRES_PASSWORD` **must be set** or compose refuses to start
  (`deploy/docker-compose.yml:25`).

**Everything Docker-specific in this document is derived from configuration and has
never been executed.** The compose stack has never been started, not on this machine
and not anywhere.

> **One thing genuinely fixed at this revision.** `docs/STAGING-RUNBOOK.md` §6B and §15
> still say the containerised stack "publishes the whole repository as the web root"
> and "cannot start" because the security-headers snippet is not mounted. **Both
> claims are stale at `7309a4c`.** `deploy/docker-compose.yml` now mounts an explicit
> list of the five public paths (`:93-97`) and does mount the snippet (`:105`), and
> `tools/check_compose.py` reports 14 mounts / 4 configs / 0 errors. The prose was
> written against `8f83043` and was not updated when the compose file was fixed in
> `4445d60`. Trust the compose file, not the runbook's summary — and fix the runbook
> separately.

---

## 5. PostgreSQL

**Required for staging: yes, if you want staging to resemble production.** The
alternative is SQLite, which `docs/DEPLOYMENT.md` §2 permits "for staging only". The
compose stack goes straight to PostgreSQL on purpose, so that the production data path
is exercised before go-live rather than during it.

| Item | Value | Basis |
|---|---|---|
| Version | **16 recommended**; 14+ acceptable | `deploy/docker-compose.yml:20` pins `postgres:16-alpine`. `docs/DEPLOYMENT.md` §2 says "PostgreSQL 14+". |
| Driver | `psycopg[binary]==3.3.6` — ships its own libpq, so **no `libpq-dev` needed** | `backend/requirements.txt`; `docs/STAGING-RUNBOOK.md` §3 |
| URL form | `postgresql+psycopg://viporder:<password>@127.0.0.1:5432/viporder` | `deploy/env.production.example` |
| Role and database | a role `viporder`, a database `viporder` owned by it | `docs/STAGING-RUNBOOK.md` §4.2 |
| Migrations | `alembic upgrade head` — **5 migrations**, `0001`…`0005` | `backend/alembic/versions/` (verified by listing); `docs/STAGING-RUNBOOK.md` §4.3 |
| `AUTO_CREATE_SCHEMA` | **must be `false`** | `deploy/env.production.example`. `true` (the default) makes the application create tables with `create_all` and bypass Alembic, letting the live schema drift from the migration history. |
| Backups | `pg_dump`, hourly, 30-day retention, to **off-host** storage | `docs/DEPLOYMENT.md` §7 |

`docs/DEPLOYMENT.md` §7 makes one point worth repeating to whoever runs this: the lead
store is the only irreplaceable data on the server — a registered customer's code
cannot be regenerated from the website — and **a backup that has never been restored is
not a backup.**

---

## 6. DNS and TLS

### 6.1 Suggested hostname

**Suggestion: `staging.viporder.com.vn`.**

> ### ⚠ No DNS record has been created.
> Nothing has been registered, delegated or pointed anywhere. There is no DNS access
> recorded in this repository. Creating the record is the owner's action, and until it
> exists the host cannot be reached by name.

**Why a subdomain rather than the production name** — this is a concrete risk, not
tidiness:

`deploy/nginx/viporder-security-headers.conf` sends:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

`includeSubDomains`, for **one year**, is applied by the browser to the hostname it is
served from **and every subdomain of it**. If you test staging on the real
`viporder.com.vn` name, then every browser that loads the staging site is pinned to
HTTPS for `viporder.com.vn` **and for all of its subdomains** — including
`khachhang.viporder.com.vn`, the existing legacy portal that this project does not
serve and does not control. If that portal ever needs plain HTTP, or its certificate
lapses, browsers will refuse to connect and the only cure is waiting for the year to
expire.

Serving staging from `staging.viporder.com.vn` confines the pin to
`*.staging.viporder.com.vn`, which is yours and disposable. (The repository's
`fix/deploy-source-exposure` branch additionally withholds `includeSubDomains`
altogether — but that is **not on `develop`**, so at the revision you are deploying,
`includeSubDomains` is present. Plan around it.)

**What the subdomain costs you.** The checked-in nginx configuration names **exactly**
`viporder.com.vn` and `www.viporder.com.vn`, so a different hostname is not a DNS-only
change. Four edits are required, and they must be recorded as a deliberate deviation:

| # | File | Line | Change |
|---|---|---|---|
| 1 | `deploy/nginx/viporder.com.vn.conf` | `:43` | `server_name viporder.com.vn www.viporder.com.vn;` → the staging name |
| 2 | `deploy/nginx/viporder.com.vn.conf` | `:62` | the `www` server block's `server_name` |
| 3 | `deploy/nginx/viporder.com.vn.conf` | `:77` | the apex server block's `server_name` |
| 4 | `deploy/nginx/viporder.com.vn.conf` | `:64-65, :80-81` | the four `ssl_certificate…` paths, which point at `/etc/letsencrypt/live/viporder.com.vn/` |

Also update the two `return 301 https://viporder.com.vn$request_uri;` redirects
(`:51`, `:67`), or staging will bounce visitors to production.

**Records needed** (once you have an IP): an **A record** for the staging name →
the host's public IPv4. Add **AAAA** only if the host genuinely has working IPv6 —
the config listens on IPv6 (`listen [::]:80`, `listen [::]:443 ssl`), so a
half-configured AAAA record is a real failure mode.

### 6.2 TLS

**Required. There is no plain-HTTP option.** Every HTTP request is redirected to HTTPS
except the ACME challenge (`deploy/nginx/viporder.com.vn.conf:51`), and the
post-deployment check fails if HSTS is missing (`deploy/post-deploy-check.sh`).

| Item | Value | Basis |
|---|---|---|
| Issuer | **Let's Encrypt via certbot** | `docs/DEPLOYMENT.md` §2, §4.8 |
| Command | `certbot --nginx -d <hostname>` | `docs/DEPLOYMENT.md` §4.8 shows `-d viporder.com.vn -d www.viporder.com.vn` |
| Needs | the DNS record live **before** issuing, inbound **80/tcp**, and a contact email | HTTP-01 challenge; `deploy/nginx/viporder.com.vn.conf:46` |
| Protocols | TLSv1.2 and TLSv1.3 | `deploy/nginx/viporder.com.vn.conf:82` |
| OCSP stapling | on | `:86-87` |
| Renewal | automatic; ensure the renewal timer is enabled and watched | certbot installs a timer; the contact email receives expiry warnings |

Get the DNS record live **first**. Issuing a certificate for a name that does not
resolve will fail, and repeated failures are rate-limited by Let's Encrypt.

---

## 7. SSH access

**Required. Nothing can be installed without it.** Every installation step in
`docs/DEPLOYMENT.md` §4.2–4.8 and `docs/STAGING-RUNBOOK.md` §3 uses `sudo`, so the
account must be able to escalate to root.

What to hand over:

| # | What | Notes |
|---|---|---|
| 1 | **SSH access to the host** | An **SSH key is strongly preferred** over a password. Public IPv4 address + username (or root). |
| 2 | **The port** | 22 unless you have moved it; inbound from the owner's or operator's address only. |
| 3 | **`sudo`/root capability** | The install writes to `/etc`, `/srv`, `/var/lib`, `/var/log`, runs `useradd`, `systemctl` and `apt-get`. |
| 4 | **The public key to authorise**, if you would rather not share a password | Whoever deploys adds it to `~/.ssh/authorized_keys`. |

**Never** put a password or a private key in this repository, in a commit, or in a chat
message. `docs/DEPLOYMENT.md` §9 lists what must not be deployed, and secrets are
item one.

The host also needs **outbound access to `github.com` over 443** to check out the
tracked commit (`docs/DEPLOYMENT.md` §4.1). If that is not acceptable, the release has
to be copied to the host by another route — decide that before provisioning, not
during.

---

## 8. What is NOT needed on the staging host

Stated because it is the difference between a small machine and a large one.

**The Playwright browsers are NOT needed on the staging host.** Verified six ways at
this revision rather than assumed:

1. `deploy/docker-compose.yml` defines **exactly three services** — `db`
   (`postgres:16-alpine`), `app` (built from `deploy/Dockerfile`), `nginx`
   (`nginx:1.27-alpine`). **No Node.js service.**
2. `deploy/Dockerfile`'s only dependency step is
   `pip install -r backend/requirements.txt`. There is **no `npm`, no `node`, and no
   browser download** anywhere in it. The image contains only `backend/` plus the
   virtualenv.
3. The compose nginx service mounts an **explicit list of five public paths**
   (`index.html`, `404.html`, `robots.txt`, `sitemap.xml`, `static/` —
   `deploy/docker-compose.yml:93-97`). The test suite is not one of them.
4. `package.json` lists `@playwright/test` under **`devDependencies`**, and its own
   description says the page "has no build step and **no runtime dependencies**".
5. `grep -ri 'node\|npm\|playwright' deploy/` returns **no match** — no deploy artifact
   refers to Node, npm or Playwright at all.
6. The browser **binaries are not in `node_modules` at all**: measured at
   `/Users/duongthanh/Library/Caches/ms-playwright`, **1.1 GB** on **macOS 26.6.2
   (arm64), 2026-10-02**. They are downloaded by `playwright install` and are part of
   no deploy artifact in this repository.

So that 1.1 GB stays on a developer's laptop. **Do not budget for it on the host.**

> **One caveat, recorded rather than glossed.** The **systemd** path publishes the site
> with an rsync whose exclusion list is
> `--exclude .git --exclude backend --exclude deploy --exclude tools --exclude docs
> --exclude var` (`docs/DEPLOYMENT.md` §4.4 and §5). That list does **not** exclude
> `tests/`, `node_modules/`, `package.json` or `playwright.config.js`. Measured, those
> four are about **100 KB** (tests/ 84 KB, package.json 4 KB, package-lock.json 4 KB,
> playwright.config.js 8 KB) — so the disk cost is trivial, but a test file and, if
> anyone ever ran `npm install` on the host, a `node_modules` directory would land in
> the web root where nginx serves it.
>
> **Neither deploy path installs Node**, so this does not happen by default. The
> repository's `fix/deploy-source-exposure` branch replaces the exclude list with
> `--files-from=deploy/published-files.txt`, which publishes only what the site is.
> **CORRECTED 2026-10-02: that fix IS on `develop`.** `docs/DEPLOYMENT.md` §4.4/§5 and `docs/STAGING-RUNBOOK.md` §3.3 already use `--files-from=deploy/published-files.txt`, and `tools/check_deploy_exposure.py` is committed. The paragraph that stood here was written against an earlier revision and was false at the SHA it described.

The original text follows, struck through, because the reasoning about the deny list was right and is what produced the fix:

~~That fix is not on `develop`, so at the revision you are deploying, use the exclude list and know that `tests/` and `node_modules/` become fetchable.~~

---

## 9. Summary checklist

| # | Obtain | Status |
|---|---|---|
| 1 | A host: Debian 12 / Ubuntu 22.04+, 64-bit, **2 vCPU / 2 GB RAM / 20–40 GB disk** | ☐ |
| 2 | **SSH access** to it (key preferred), with `sudo` | ☐ |
| 3 | **A public IPv4 address** for it | ☐ |
| 4 | Firewall: **80 and 443 in**; **8000 and 5432 NOT reachable**; SSH from your address only | ☐ |
| 5 | **DNS A record** for the staging hostname — **none exists today** | ☐ |
| 6 | **TLS contact email**, and ports 80 open long enough for the certificate | ☐ |
| 7 | **PostgreSQL 16** role `viporder` + database `viporder` + a password | ☐ |
| 8 | **Admin API token** (optional — empty disables the admin route) | ☐ |
| 9 | Decision: **systemd path (recommended, no Docker)** or compose path (Docker) | ☐ |
| 10 | Outbound 443 to `github.com` from the host, to check out the release | ☐ |

**Not needed:** Docker (if you take the systemd path), Node.js, npm, or the Playwright
browsers.
