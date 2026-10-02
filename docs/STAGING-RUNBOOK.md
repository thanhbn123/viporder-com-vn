# Staging runbook — VIPORDER.COM.VN

> **Status: NOT DEPLOYED.**
> There is no staging host, no VPS address, no DNS record and no credential
> anywhere in this repository. Nothing in this document has been executed
> against a real host. This is the set of commands to run **when** someone with
> VPS and DNS access does it, written so that every step is copy-pasteable and
> every step says what its own output must look like.
>
> Written against `develop` @ `8f83043` (`docs/runbook`). Every artifact anchor
> below (`file:line`) was read at that revision. **The commit to deploy is not
> fixed by this document** — it is the `$SHA` variable you supply in step 0.

**What "staging" means here:** this same configuration also serves production.
The differences are the host, the DNS record and the values in the env file —
not the code. Do not treat a staging pass as a production authorization; the
gates in `docs/GO-LIVE-CHECKLIST.md` are separate and must be ticked with their
own evidence.

---

## 0. Values you must supply

Nothing in this table may be guessed. If a value is unknown, stop at that step.

| Placeholder | What it is | Where the constraint comes from |
|---|---|---|
| `$SHA` | The **40-hex commit** to deploy. Must be the revision CI verified, not a branch name and not a tag. | CI checks the PR head SHA, not a merge commit: `.github/workflows/ci.yml:31-38` |
| `<STAGING_HOST>` | The hostname you will browse. The checked-in config names **exactly** `viporder.com.vn` and `www.viporder.com.vn`, so if staging is a different name you must edit `server_name` and record the deviation. | `deploy/nginx/viporder.com.vn.conf:43,62,77` |
| `<STAGING_IP>` | Public IPv4 of the VPS. Used only to test before DNS moves (step 9). | — |
| `<TLS_CONTACT_EMAIL>` | Email registered with Let's Encrypt; receives expiry warnings. | `certbot` requirement |
| `<DB_PASSWORD>` | Password for the PostgreSQL role. | `deploy/env.production.example:57` |
| `<ADMIN_API_TOKEN>` | Optional. **Empty means the admin retry route returns 404** — disabled, not merely unauthorised. | `deploy/env.production.example:95-98`, `backend/app/config.py:93-95` |
| `<TEST_PHONE>` | A phone number you own, used for the registration E2E. Canonical form is `+84` + 9 digits starting with `2/3/5/7/8/9`. | `backend/app/phone.py:15-16,22` |
| `<ROLLBACK_SHA>` | The 40-hex commit currently live, i.e. the rollback target. Read it **before** deploying. | step 12 |
| `$REPO_URL` | Git remote. Default below, taken from the unit file. | `deploy/systemd/viporder-web.service:15` |

### Paths used below (defaults follow `docs/DEPLOYMENT.md` §3)

```bash
SRC=/srv/viporder/src          # git checkout (this runbook's choice — see note)
VENV=/srv/viporder/venv        # virtualenv  (deploy/systemd/viporder-web.service:48)
APP=/srv/viporder/app          # backend/ copy (unit WorkingDirectory, line 23)
SITE=/srv/viporder/site        # static site (nginx root, deploy/nginx/viporder.com.vn.conf:111)
ENVFILE=/etc/viporder/viporder.env
REPO_URL="https://github.com/thanhbn123/viporder-com-vn.git"
```

`SRC` is the only path in this document that `docs/DEPLOYMENT.md` does not
already name; it is where the git checkout lives so that `app/` and `site/` can
be built from a tracked revision. Change it if you prefer another location, and
keep it consistent for the rest of the run.

---

## 0.1 Shell preamble — paste this once

```bash
# Quoted so the block parses before you substitute: an unquoted SHA=<SHA> is a
# bash redirection, not an assignment. Swap in the real values, keep the quotes.
SHA="<SHA>"
STAGING_HOST="<STAGING_HOST>"
STAGING_IP="<STAGING_IP>"
TLS_CONTACT_EMAIL="<TLS_CONTACT_EMAIL>"
DB_PASSWORD="<DB_PASSWORD>"
ADMIN_API_TOKEN="<ADMIN_API_TOKEN>"
TEST_PHONE="<TEST_PHONE>"
ROLLBACK_SHA="<ROLLBACK_SHA>"

SRC=/srv/viporder/src
VENV=/srv/viporder/venv
APP=/srv/viporder/app
SITE=/srv/viporder/site
ENVFILE=/etc/viporder/viporder.env
REPO_URL="https://github.com/thanhbn123/viporder-com-vn.git"
```

This block does **not** use `set -e`: it only assigns variables, and a failing
assignment here should not close your shell mid-runbook. The multi-line scripts
below use `set -e` inside their own subshells where an early exit is wanted.

---

## 1. Clone / fetch

Fresh server:

```bash
sudo install -d -o root -g root /srv/viporder
sudo git clone "$REPO_URL" "$SRC"
```

Server that already has a checkout:

```bash
sudo git -C "$SRC" fetch --all --tags --prune
```

Confirm the remote you just fetched is the one you think it is — this is a
public repository and the remote is the only thing deciding what code you get:

```bash
git -C "$SRC" remote get-url origin
```

Expected: the `$REPO_URL` above (`deploy/systemd/viporder-web.service:15`).

---

## 2. Check out the exact SHA

```bash
sudo git -C "$SRC" checkout --detach "$SHA"
```

Then prove you are where you think you are. Both commands must be run, and both
must produce the stated output — this is the same assertion CI makes before it
trusts a revision (`.github/workflows/ci.yml:50-54`):

```bash
git -C "$SRC" rev-parse HEAD          # must print exactly: $SHA
git -C "$SRC" status --porcelain      # must print NOTHING
```

If `git status --porcelain` prints anything, **stop**. A deployment from a dirty
tree is not the revision you recorded, and no later step can tell you that
(`docs/DEPLOYMENT.md:241`).

Record the release identity the service reads later:

```bash
echo "$SHA" | sudo tee /srv/viporder/RELEASE
```

The previous value of that file is your rollback target — read it **before**
overwriting:

```bash
sudo cat /srv/viporder/RELEASE 2>/dev/null || echo "(no previous release — first deploy)"
```

---

## 3. Install runtime

Debian/Ubuntu. Python **3.11 or 3.12** (`docs/DEPLOYMENT.md:46`); the pinned
versions are in `backend/requirements.txt`. `psycopg[binary]` ships its own
libpq, so no `libpq-dev` is needed.

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3.12 python3.12-venv python3.12-dev \
  nginx certbot \
  postgresql-client rsync ca-certificates curl
```

The interpreter binary name is distribution-specific — on a system where it is
just `python3`, use that instead. Verify before building the venv:

```bash
python3.12 --version        # expect 3.12.x (say 3.11.x only if you installed python3.11)
nginx -v                    # expect nginx/1.20 or newer (docs/DEPLOYMENT.md:48)
```

### 3.1 Service account and directories

Exactly the layout `docs/DEPLOYMENT.md:85-92` and the unit header
(`deploy/systemd/viporder-web.service:4-7`) assume:

```bash
sudo useradd --system --home /srv/viporder --shell /usr/sbin/nologin viporder \
  || echo "user viporder already exists"
sudo install -d -o viporder -g viporder /srv/viporder/app /srv/viporder/site
sudo install -d -o viporder -g viporder /var/lib/viporder /var/log/viporder
sudo install -d -m 0750 -o root -g viporder /etc/viporder
```

`/var/lib/viporder` and `/var/log/viporder` are the **only** writable paths the
unit grants (`deploy/systemd/viporder-web.service:84`); everything else is read
only under `ProtectSystem=strict` (line 67).

### 3.2 Virtualenv and dependencies

```bash
sudo python3.12 -m venv "$VENV"
sudo "$VENV/bin/pip" install --upgrade pip
sudo "$VENV/bin/pip" install -r "$SRC/backend/requirements.txt"
sudo chown -R viporder:viporder "$VENV"
"$VENV/bin/python" -c "import fastapi, uvicorn; print('fastapi', fastapi.__version__, 'uvicorn', uvicorn.__version__)"
```

### 3.3 Build `app/` and `site/` from the checked-out revision

`app/` is a copy of `backend/`; `site/` is the static site only. `nginx` serves
`site/` as its document root, so the exclusions are load-bearing — they are the
difference between publishing a website and publishing `docs/` and `backend/`
source (`docs/DEPLOYMENT.md:106-109`):

```bash
sudo rsync -a --delete "$SRC/backend/" "$APP/"
# Publish ONLY what the site is, by name. `-r` is required: --files-from cancels
# the -r implied by -a, so without it you would deploy a site with no CSS or JS.
sudo rsync -a -r --delete --files-from="$SRC/deploy/published-files.txt" \
  "$SRC/" "$SITE/"
sudo chown -R viporder:viporder "$APP" "$SITE"
```

Prove the web root contains only static files — this command must print nothing:

```bash
ls "$SITE" | grep -E '^(backend|docs|deploy|tools)$' || echo "OK: web root is static-only"
```

---

## 4. Create the env file from `deploy/env.production.example`

Install it with the mode the unit expects, then edit it in place:

```bash
sudo install -m 0640 -o root -g viporder \
  "$SRC/deploy/env.production.example" "$ENVFILE"
sudo vi "$ENVFILE"
sudo chmod 0640 "$ENVFILE"
sudo chown root:viporder "$ENVFILE"
```

Every key in that template is read by the application — the file's own header
says so and `backend/tests/test_env_templates.py:70-89` enforces it (key set ==
`Settings.model_fields`, no orphans, none missing). **You do not have to add
keys.** What you must set:

| Key | Staging value | Why |
|---|---|---|
| `APP_ENV` | `production` | `production` hides the API docs and is what `backend/app/config.py:137-149` branches on |
| `DATABASE_URL` | `postgresql+psycopg://viporder:<DB_PASSWORD>@127.0.0.1:5432/viporder` | The template's default is SQLite (`deploy/env.production.example:58`), acceptable for staging only (`docs/DEPLOYMENT.md:47`) |
| `AUTO_CREATE_SCHEMA` | `false` — **leave as-is** | `true` makes the app create tables with `create_all` and bypass Alembic (`deploy/env.production.example:35-40`) |
| `KHAIBAO9610_MODE` | `mock` — **leave as-is** | The real contract has not been supplied (issue #4) |
| `KHAIBAO9610_ENABLE_REAL_CALLS` | `no` — **leave as-is** | Real calls need *two* opt-ins (`backend/app/config.py:6-10`) |
| `ADMIN_API_TOKEN` | `<ADMIN_API_TOKEN>` or empty | Empty = route 404 |
| `ENABLE_API_DOCS`, `CORS_ALLOW_ORIGINS` | empty — **leave as-is** | Docs hidden in production; same-origin only |

### 4.1 Do NOT `source` the env file — measured, not assumed

The template contains a value with parentheses and spaces
(`deploy/env.production.example:90`). Sourcing it as a shell script fails and, worse,
**keeps going**:

```
$ bash -c 'set -a; . probe.env; echo "DATABASE_URL=[$DATABASE_URL]"'
probe.env: line 90: syntax error near unexpected token `('
DATABASE_URL=[sqlite:////var/lib/viporder/viporder.db]
$ echo $?
0
```

Exit status **0**, a syntax error on stderr, and three keys silently never set:
`KHAIBAO9610_USER_AGENT`, `MOCK_PROVIDER_BEHAVIOUR`, `ADMIN_API_TOKEN`. In
particular your admin token would be dropped and the retry route would 404 for
reasons nothing reports.

`systemd` is unaffected: `EnvironmentFile=` is parsed by systemd itself, not by
a shell (`deploy/systemd/viporder-web.service:25`). Where this document needs a
value on the command line it extracts that single key with `grep`/`cut` instead
of sourcing the file — see step 5.

Confirm the file is not world-readable and is outside the web root:

```bash
sudo stat -c '%a %U:%G %n' "$ENVFILE"     # expect: 640 root:viporder /etc/viporder/viporder.env
```

### 4.2 PostgreSQL role and database (only if you chose PostgreSQL)

```bash
sudo -u postgres psql -c "CREATE ROLE viporder LOGIN;"
sudo -u postgres psql -c "CREATE DATABASE viporder OWNER viporder;"
sudo -u postgres psql -c '\password viporder'   # prompts; never lands in shell history
```

Use `\password` rather than `PASSWORD '...'` on a command line: an inline
password ends up in your shell history and in the PostgreSQL log. Use the same
`<DB_PASSWORD>` you put in `$ENVFILE`.

---

## 5. Run database migrations

Migrations run **before** the service starts. `AUTO_CREATE_SCHEMA=false`
(`deploy/env.production.example:40`) means nothing creates the tables for you, and the
Dockerfile says the same for the container path (`deploy/Dockerfile:49-51`).

### 5.1 Why this step names the DSN explicitly

`backend/alembic/env.py:31-38` resolves the URL in this order:

1. `DATABASE_URL` from the **process environment**
2. `sqlalchemy.url` from `backend/alembic.ini` — deliberately blank (`backend/alembic.ini:11`)
3. the application default — `sqlite:///./var/viporder.db` (`backend/app/config.py:74`)

`get_settings()` reads `.env` **in the current directory**, not
`/etc/viporder/viporder.env`. So a bare `alembic upgrade head` on the server
does not fail — it creates `/srv/viporder/app/var/viporder.db` and reports
success while your real database is untouched. `_prepare_sqlite_path`
(`backend/app/db.py:24-32`) even creates the directory for you. Pass the DSN.

### 5.2 Read the DSN from the env file, safely

```bash
DB_URL="$(sudo grep -E '^DATABASE_URL=' "$ENVFILE" | tail -1 | cut -d= -f2-)"
test -n "$DB_URL" || { echo "REFUSING: DATABASE_URL is empty in $ENVFILE"; exit 1; }
printf 'migrating: %s\n' "$(printf '%s' "$DB_URL" | sed -E 's#(//[^:]+):[^@]+@#\1:***@#')"
```

The last line prints the target with the password masked, so the DSN can appear
in a terminal transcript without leaking the credential.

### 5.3 Upgrade

```bash
sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && exec '$VENV/bin/alembic' upgrade head"
```

`cd '$APP'` is required: `backend/alembic.ini:8` sets `script_location =
alembic`, relative to the working directory. Running as `viporder` matters for
the SQLite case, where the database lives in `/var/lib/viporder`
(`deploy/env.production.example:58`) — the only writable data path
(`deploy/systemd/viporder-web.service:84`).

### 5.4 Verify the revision — do not accept "no output" as success

```bash
sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && '$VENV/bin/alembic' current"
```

Expected on a first deploy — the head of the chain, from
`backend/alembic/versions/0001..0005`:

```
0005_live_phone_rule (head)
```

---

## 6. Start the application

The repository ships **two** deployment paths and they are alternatives — "Use
one or the other, not both" (`deploy/docker-compose.yml:4-5`).

> ### Use the systemd path (6A). It is the recommended one.
>
> 1. ~~**The compose path cannot start nginx as checked in.**~~ **FIXED** in
>    `deploy/docker-compose.yml` (commit `4445d60`): the security-headers snippet
>    the site config `include`s in five places is now mounted. The original defect
>    was real and measured — a missing `include` is a fatal `[emerg]`, not a
>    warning, and the containerised stack exited at startup. Kept visible so the
>    history is clear.
> 2. ~~**The compose path publishes the whole repository as the web root.**~~
>    **FIXED** in the same commit: the nginx service now mounts an explicit
>    five-path allow list instead of `../:/srv/viporder/site:ro`, so `README.md`,
>    `docs/*` and `backend/*` are no longer reachable.
> 3. **Docker was never executed for this project** — recorded as such in
>    `docs/PROJECT-STATUS.md`. This one is still true: the fixes above are
>    configuration-verified, not run. `python3 tools/check_compose.py` reports
>    14 mounts, 4 configs, 0 errors, and that is a check of the SEAM, not a
>    running stack.
>
> **The recommendation is unchanged — use systemd (6A)** — but the reason is now
> "systemd is the path this project has actually exercised", not "compose is
> broken". Section 6B.1 is retained because it documents which corrections were
> applied and why.

### 6A. systemd (recommended)

```bash
sudo cp "$SRC/deploy/systemd/viporder-web.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viporder-web
sudo systemctl status viporder-web --no-pager
```

The unit pins two flags deliberately; **do not edit them casually**:

* `--workers 1` (`deploy/systemd/viporder-web.service:51`). The rate limiter
  keeps its counters **in process memory**, so each worker enforces the limit
  independently and the effective limit is `RATE_LIMIT_ATTEMPTS x workers`. With
  two workers a limit of 10 becomes 20 in practice
  (`deploy/nginx/README.md:102-106`). **If you raise the worker count, the
  concurrency guarantees are no longer the ones that were verified:**
  `backend/tests/test_concurrency.py` must be re-run against the multi-worker
  topology first, and it needs a real database
  (`TEST_DATABASE_URL=... python -m pytest tests/test_concurrency.py -q`;
  the test skips itself without one — `backend/tests/test_concurrency.py:38-42`).
  Concurrency behaviour verified at one worker says nothing about two.
* `--forwarded-allow-ips 127.0.0.1` (`deploy/systemd/viporder-web.service:53`). uvicorn rewrites the client address
  from `X-Forwarded-For` **before the application sees the request**, so this
  flag — not the app's `TRUST_PROXY_HEADERS` — is the operative control
  (`deploy/nginx/README.md:108-114`). Pinning it to loopback is what stops a
  direct connection from forging a client IP.

Check it is actually listening on loopback only, and on one worker:

```bash
sudo ss -ltnp | grep 8000                  # expect 127.0.0.1:8000, not 0.0.0.0:8000
sudo systemctl show viporder-web -p NRestarts -p ActiveState -p MainPID
ps -o pid,ppid,command -p "$(systemctl show -p MainPID --value viporder-web)"
```

### 6B. docker compose (alternative)

```bash
cd "$SRC/deploy"
cp env.production.example .env
vi .env                       # POSTGRES_PASSWORD, ADMIN_API_TOKEN
docker compose config >/dev/null   # parse check before starting anything
docker compose run --rm app alembic upgrade head
docker compose up -d
docker compose ps
```

`POSTGRES_PASSWORD` has no default and compose refuses to start without it
(`deploy/docker-compose.yml:25`). The compose path sets `DATABASE_URL` in the
service environment, so — unlike the systemd path (step 5) — the migration
container reaches the right database by construction
(`deploy/docker-compose.yml:55`).

#### 6B.1 Corrections required at this SHA

These are **not** in the checked-in compose file. Apply them, and record the
deviation against `$SHA` in the deployment log.

**(a) Mount the security-headers snippet** — without this the nginx container
exits at startup. Add to the `nginx` service `volumes:` list
(`deploy/docker-compose.yml:81-91`):

```yaml
      - ./nginx/viporder-security-headers.conf:/etc/nginx/snippets/viporder-security-headers.conf:ro
```

**(b) Stop publishing the repository root as the web root.** Either mount a
prepared static directory instead of `../`, or — if the repository must stay
mounted — deny the non-static trees in the installed site config. Verify with:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "https://$STAGING_HOST/README.md"   # must be 404
curl -s -o /dev/null -w '%{http_code}\n' "https://$STAGING_HOST/index.html"  # must be 200
```

---

## 7. Configure nginx

### 7.1 Install the four files

`deploy/nginx/README.md:5-10` is the table of source → destination:

```bash
sudo install -d /etc/nginx/snippets /etc/nginx/upstreams
sudo cp "$SRC/deploy/nginx/viporder-security-headers.conf" /etc/nginx/snippets/
sudo cp "$SRC/deploy/nginx/proxy_params_viporder"          /etc/nginx/proxy_params_viporder
```

### 7.2 Install **exactly one** upstream — this is the whole reason it is an `include`

`deploy/nginx/upstream-systemd.conf` and `deploy/nginx/upstream-compose.conf`
are **alternatives**. Only one may be installed, and both must land on the same
path, because the site config includes that path by name:

```
include /etc/nginx/upstreams/viporder-upstream.conf;      # deploy/nginx/viporder.com.vn.conf:35
```

systemd (app on loopback) — the file for step 6A:

```bash
sudo cp "$SRC/deploy/nginx/upstream-systemd.conf" /etc/nginx/upstreams/viporder-upstream.conf
```

compose (app is a sibling container) — the file for step 6B:

```bash
sudo cp "$SRC/deploy/nginx/upstream-compose.conf" /etc/nginx/upstreams/viporder-upstream.conf
```

The address differs on purpose: `127.0.0.1:8000` under systemd
(`deploy/nginx/upstream-systemd.conf:11`), `app:8000` under compose
(`deploy/nginx/upstream-compose.conf:12`). Inside the nginx container
`127.0.0.1` **is nginx itself**, so the containerised stack could never have
reached the application with the systemd upstream. Because the address is an
`include`, getting this wrong fails loudly instead of proxying into the void —
measured in 7.5.

### 7.3 Install the site config

```bash
sudo cp "$SRC/deploy/nginx/viporder.com.vn.conf" /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/viporder.com.vn.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default     # Debian/Ubuntu default vhost
```

If `<STAGING_HOST>` is not `viporder.com.vn`, edit `server_name`
(`deploy/nginx/viporder.com.vn.conf:43,62,77`) and the certificate paths
(`deploy/nginx/viporder.com.vn.conf:64-65,80-81`) to match, and record that deviation. The domains that may
appear in this configuration are `viporder.com.vn`,
`www.viporder.com.vn` and `khachhang.viporder.com.vn` — the last one is a
**third-party host that is never proxied here**
(`deploy/nginx/viporder.com.vn.conf:13-14`).

> **Do not run `nginx -t` yet expecting success.** The two TLS server blocks
> point at `/etc/letsencrypt/live/viporder.com.vn/fullchain.pem`
> (`deploy/nginx/viporder.com.vn.conf:64-65,80-81`), which does not exist until step 8. That
> is why TLS comes next: the certificate must exist before this config can
> parse.

### 7.4 Why the security-headers snippet exists, and the rule you must not break

**nginx `add_header` does not merge across configuration levels.** The
documented rule, quoted at `deploy/nginx/viporder-security-headers.conf:8-10`
and `deploy/nginx/README.md:38-45`:

> These directives are inherited from the previous configuration level **if and
> only if there are no `add_header` directives defined on the current level.**

So **one** `add_header Cache-Control ...` inside a `location` silently cancels
**every** server-level `add_header` for that location. The homepage is served by
`location = /`, which needs its own `Cache-Control` (`deploy/nginx/viporder.com.vn.conf:143-147`).
Without the include, the HTML document would ship with **no CSP, no HSTS, no
X-Frame-Options, no X-Content-Type-Options, no Referrer-Policy and no
Permissions-Policy** — while `curl -I` against any other path looked perfect.
That exact bug shipped once. Measured in `deploy/nginx/README.md:53-58` with
real nginx 1.31.6:

| Location | Without the include | With the include |
|---|---|---|
| `/` | HTTP 200, security headers **0 / 6** | HTTP 200, **6 / 6** |
| `/static/style.css` | HTTP 200, **0 / 6** | HTTP 200, **6 / 6** |

Hence the snippet is `include`d at server level **and** in every location that
declares its own `add_header` (`deploy/nginx/viporder.com.vn.conf:94,146,155,162,169`). The
four locations that declare `add_header` are `/`, `/404.html`, the `/static/*`
block and the `robots.txt|sitemap.xml|favicon.ico` block.

**The rule, as a program — not a comment.** `tools/check_nginx_config.py`
enforces exactly this: no location may set a security header directly, and any
location that declares its own `add_header` must include the snippet
(`tools/check_nginx_config.py:111-137`, and
`tools/check_nginx_config.py:145-148` for server level). Run it
before and after any nginx edit:

```bash
python3 "$SRC/tools/check_nginx_config.py"     # must exit 0
```

Note its declared scope — it checks placement only, never syntax:

```
scope: add_header/include placement in deploy/nginx/*.conf only — this does NOT
validate nginx syntax; run `nginx -t` for that.
```

Two more standing rules from `deploy/nginx/README.md:62-71`: never set a
security header inside a `location` (two copies drift apart), and never use
`expires` together with an explicit `Cache-Control` — `expires` emits a second
`Cache-Control` and lets a client pick either.

### 7.5 What `nginx -t` does and does not catch — measured locally

Measured on macOS with nginx **1.31.6** (the same version
`deploy/nginx/README.md:88` reports), against the real site config with only the
filesystem path prefixes rewritten so it could be parsed without touching
`/etc`:

(temporary paths elided as `...`; everything else is verbatim)

```
=== deploy/nginx/upstream-systemd.conf installed ===
nginx: the configuration file .../nginx.conf syntax is ok
nginx: configuration file .../nginx.conf test is successful          [exit 0]

=== deploy/nginx/upstream-compose.conf swapped in (no `app` host on this machine) ===
nginx: [emerg] host not found in upstream "app:8000" in .../upstreams/...:12
nginx: configuration file .../nginx.conf test failed                 [exit 1]

=== no upstream file installed at all ===
nginx: [emerg] open() ".../upstreams/viporder-upstream.conf" failed (2: No such file or directory) in .../site.conf:35
nginx: configuration file .../nginx.conf test failed                 [exit 1]
```

And, for the failure mode behind the compose gap in 6B.1:

```
=== location with its own add_header, snippet present ===
nginx: ... test is successful                                        [exit 0]

=== same config, snippet file removed ===
nginx: [emerg] open() ".../snippets/viporder-security-headers.conf" failed (2: No such file or directory) in .../site.conf:4
nginx: configuration file .../nginx.conf test failed                 [exit 1]
```

Read that as three facts:

1. Installing the **wrong** upstream is caught, and installing **neither** is
   caught. "Exactly one" is enforced by `nginx -t`, which is why the address is
   an include rather than an inline block.
2. A missing `include` — including the compose snippet gap — is a **fatal
   `[emerg]`**, not a warning. nginx will not start.
3. `nginx -t` validates syntax and file resolution. It does **not** validate
   that the headers are actually returned; only a request to `/` does that
   (step 10).

---

## 8. TLS

Order matters. The certificate must exist before the site config can parse, and
the initial issuance needs port 80 free because there is nothing to serve the
ACME challenge from yet.

```bash
sudo install -d /var/www/certbot
sudo systemctl stop nginx
sudo certbot certonly --standalone --non-interactive --agree-tos \
  -m "$TLS_CONTACT_EMAIL" \
  -d viporder.com.vn -d www.viporder.com.vn
```

Expected: `Congratulations! Your certificate and chain have been saved at:
/etc/letsencrypt/live/viporder.com.vn/fullchain.pem`.

Now the config can be tested and started:

```bash
sudo nginx -t
sudo systemctl start nginx        # or: systemctl reload nginx if it was already up
```

Verify the certificate is the one being served, and that the expiry is readable:

```bash
openssl s_client -servername viporder.com.vn -connect "$STAGING_IP:443" </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -dates
```

Renewals run while nginx is up, using the webroot the config already defines
(`deploy/nginx/viporder.com.vn.conf:46-48`):

```bash
sudo certbot renew --dry-run
systemctl list-timers certbot.timer --no-pager
```

If the dry run fails, fix renewal **now** — a certificate that cannot renew is a
scheduled outage, not a future problem.

> **Watch item, read from the config, not measured here:** the server block sets
> `ssl_stapling on; ssl_stapling_verify on;` (`deploy/nginx/viporder.com.vn.conf:86-87`) but
> declares no `resolver` directive at either level. OCSP stapling needs a
> resolver to fetch the response, so stapling may be silently inactive. Check
> rather than assume:
>

```bash
openssl s_client -servername viporder.com.vn -connect "$STAGING_IP:443" -status </dev/null 2>/dev/null | grep -A1 "OCSP response"
```

---

## 9. Health check

Directly on the host, before DNS:

```bash
curl -sS "http://127.0.0.1:8000/api/v1/health" | python3 -m json.tool
```

Through nginx, testing the real `server_name` against the staging IP without
touching DNS — this is the trick that lets you validate the shipped config
before cutover:

```bash
curl -sS --resolve "viporder.com.vn:443:$STAGING_IP" \
  "https://viporder.com.vn/api/v1/health" | python3 -m json.tool
```

Expected body shape (`backend/app/routers/health.py:23-38`) — `status` is `ok`
only when the database is reachable, and the response is `503` when it is not:

```json
{
  "status": "ok",
  "service": "viporder-web",
  "version": "0.1.0",
  "checks": {
    "database": "ok",
    "provider": { "mode": "mock", "status": "ok" }
  }
}
```

`"mode": "mock"` is the honest expected value: real registration is **not**
live, and the site must not claim it is
(`docs/GO-LIVE-CHECKLIST.md:46-48,72-74`).

---

## 10. Smoke test

`deploy/post-deploy-check.sh` measures the site **from the outside, over the
public internet, which is the only vantage point that matters**
(`deploy/post-deploy-check.sh:5-8`). It sends an **invalid** registration payload
on purpose, so it proves validation is live without creating a real customer
(`deploy/post-deploy-check.sh:14-15,152-162`).

> **The script measures whatever the hostname resolves to.** Before DNS cutover,
> running it with `https://viporder.com.vn` would measure the *current*
> production host, not your staging box — and a green result would be a
> measurement of the wrong system. Pass the staging host you are actually
> testing.

```bash
cd "$SRC" && ./deploy/post-deploy-check.sh "https://$STAGING_HOST"
```

Expected: `PASS=n  FAIL=0  WARN=n` and `DEPLOYMENT VERIFIED`, exit `0`
(`deploy/post-deploy-check.sh:167-171`). It checks, in order: `/` returns 200;
`/api/v1/health` returns 200 with `status=ok` and reports the provider mode;
HTTP redirects to HTTPS; TLS handshake succeeds and HSTS is present; four
security headers are present; the homepage links to
`https://khachhang.viporder.com.vn` (a business rule, `deploy/post-deploy-check.sh:120-127`); `robots.txt`
declares a sitemap; `sitemap.xml` returns 200; and the registration endpoint
rejects an invalid payload with 400 or 422.

Two results need reading rather than ticking:

* `WARN  rate limited (429)` on the registration probe means the limit is
  working and validation **was not exercised** on that run (`deploy/post-deploy-check.sh:159`). Re-run from
  a different source, or accept that validation is unproven by this run.
* `WARN` on the customer portal means a **third-party host** returned something
  unexpected — investigate, do not hotfix it from this server (`deploy/post-deploy-check.sh:133`).

Then confirm the header set is genuinely complete on `/` itself, which is the
path that once lost all six (7.4):

```bash
curl -sI "https://$STAGING_HOST/" | grep -Ei \
  'strict-transport-security|x-content-type-options|x-frame-options|referrer-policy|permissions-policy|content-security-policy' | wc -l
```

Expected: **6**. Anything less means the include in the matching `location` is
missing, no matter what `nginx -t` said.

---

## 11. Browser E2E against staging

**There is no committed browser E2E harness at this revision, and this runbook
does not pretend otherwise.** Checked at `$SHA`:

* no `playwright`, `puppeteer`, `cypress` or `selenium` dependency or config
  exists anywhere in the tree;
* `tools/js/*.test.js` are `node:test` **logic** tests with no browser
  (`.github/workflows/ci.yml`, job `js-tests`);
* `tools/check_site.py` is stdlib-only and **never uses the network** — it
  parses local files (`tools/check_site.py:6-8,460`).

So the E2E below is driven by a person in a real browser. Every value and every
expected result is exact; the evidence must be recorded next to the SHA.

### 11.1 Before the run

```bash
# record the revision under test
git -C "$SRC" rev-parse HEAD
# confirm the host you named answers at all
curl -sI "https://$STAGING_HOST/api/v1/health" | head -1
```

### 11.2 Procedure

| # | Action | Expected result |
|---|---|---|
| 1 | Open `https://<STAGING_HOST>/` | 200; page renders; no mixed-content warning in the console |
| 2 | Click every "Đăng nhập" / "Kiểm tra đơn" control | Each opens `https://khachhang.viporder.com.vn`. Never a `viporder.com.vn` URL. (Business rule; enforced for the static HTML by `tools/check_site.py`.) |
| 3 | Submit the form with a **valid** name, phone `not-a-phone`, password `short`, consent **unticked** | Field-level errors; the request to `POST /api/v1/registrations` returns **422** and **no** lead is created |
| 4 | Submit twice, quickly, with the same data | **One** lead, not two — the `Idempotency-Key` path. (`docs/GO-LIVE-CHECKLIST.md:30-31`; races are covered by `backend/tests/test_concurrency.py`.) |
| 5 | *(optional, writes data)* Submit valid data with consent ticked | **201** if the provider confirmed, **202** if it was unreachable and the lead was kept (`backend/app/routers/registrations.py:27-29`). With `KHAIBAO9610_MODE=mock` expect the mock's configured behaviour. |
| 6 | Re-submit the same `<TEST_PHONE>` with a **new** idempotency key | **409**, and the response body contains **no** existing customer code (`docs/GO-LIVE-CHECKLIST.md:28-29`) |
| 7 | At 360 px width (device toolbar) | Both primary actions reachable with no horizontal scroll (`docs/GO-LIVE-CHECKLIST.md:35-36`) |

Valid values for step 5, from the real validation rules: name 2–120 characters
(`backend/app/schemas.py:76`); phone = 9 national digits whose first digit is one
of `2 3 5 7 8 9` (`backend/app/phone.py:22`); password ≥ 8 characters
(`backend/app/schemas.py:29`); `consent` must be `true`
(`backend/app/schemas.py:118-122`).

### 11.3 Corroborate step 5 in the database

A browser "success" screen is the application's claim about itself. The row is
the evidence.

```bash
PSQL_URL="${DB_URL/postgresql+psycopg/postgresql}"
psql "$PSQL_URL" -c "SELECT id, phone, registration_status, external_customer_code, created_at
                       FROM leads WHERE phone = '+84<TEST_PHONE_9_DIGITS>'
                       ORDER BY created_at DESC LIMIT 5;"
```

SQLite staging instead:

```bash
sudo -u viporder sqlite3 /var/lib/viporder/viporder.db \
  "SELECT id, phone, registration_status, external_customer_code, created_at
     FROM leads WHERE phone = '+84<TEST_PHONE_9_DIGITS>' ORDER BY created_at DESC LIMIT 5;"
```

**Clean up before re-testing.** Migration `0005` enforces one live lead per
phone (`backend/alembic/versions/0005_live_phone_rule.py:14-19`), so a phone used
for step 5 can never register again until that row is removed. Either use a
fresh `<TEST_PHONE>` per run, or delete the test row deliberately:

```bash
psql "$PSQL_URL" -c "DELETE FROM leads WHERE phone = '+84<TEST_PHONE_9_DIGITS>';"   # DESTRUCTIVE
```

Never run that against production, and never without the `phone =` predicate.

### 11.4 Optional: prove a real browser engine rendered the page

This exercises the page's JavaScript, which `curl` does not. Measured on macOS
with Chrome 154.0.8037.95: `--dump-dom` **writes the rendered DOM but the
process does not exit by itself**, so it must be bounded, and the bound must kill
exactly the PID it started — never match processes by name.

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"   # adjust per OS
PROFILE="$(mktemp -d)"        # never point this at a real user profile
OUT="$(mktemp)"
"$CHROME" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir="$PROFILE" --virtual-time-budget=6000 \
  --dump-dom "https://$STAGING_HOST/" >"$OUT" 2>/dev/null &
CPID=$!
for _ in $(seq 1 25); do kill -0 "$CPID" 2>/dev/null || break; sleep 1; done
kill -0 "$CPID" 2>/dev/null && kill "$CPID"      # bounded: kill the exact pid
sleep 1; kill -0 "$CPID" 2>/dev/null && kill -9 "$CPID"
wait "$CPID" 2>/dev/null
grep -c "khachhang.viporder.com.vn" "$OUT"       # expect >= 1
rm -rf "$PROFILE" "$OUT"
```

If you want this as a repeatable suite, the harness has to be added in a
separate PR. Nothing in this repository is a browser test today.

---

## 11.1 Tracking lookups (warehouse import and package sealing)

The two public search functions fan out to the legacy provider. They are separate
from the registration path and are worth checking on their own, because a working
registration says nothing about whether `KHAIBAO9610_BASE_URL` is correct for the
READ endpoints.

Both routes are behind the same double opt-in as registration
(`KHAIBAO9610_MODE=http` **and** `KHAIBAO9610_ENABLE_REAL_CALLS=yes`). **With the
default mock configuration they answer `503 PROVIDER_UNAVAILABLE`** — and that is
the correct, honest response, not a fault. Check which one you are looking at:

```bash
# Is live mode even on? Read it from the running service, not from the file.
sudo systemctl show viporder-web -p Environment | tr ' ' '\n' | grep -E 'KHAIBAO9610_(MODE|ENABLE_REAL_CALLS)'
```

```bash
# Warehouse import. NOTE: the keyword must be the FULL tracking code.
# Measured 2026-10-02: KY4001103376087 -> 404, but
# KY4001103376087-2-4-|s -> 200. The pipe MUST be percent-encoded.
curl -sS -o /tmp/wh.json -w 'warehouse HTTP %{http_code}\n' \
  "https://$STAGING_HOST/api/tracking/warehouse-imports/KY4001103376087-2-4-%7Cs"
python3 -m json.tool /tmp/wh.json | head -20
```

```bash
# Package sealing.
curl -sS -o /tmp/seal.json -w 'sealing  HTTP %{http_code}\n' \
  "https://$STAGING_HOST/api/tracking/package-sealings/A1918106"
python3 -m json.tool /tmp/seal.json | head -20
```

```bash
# A keyword that must NOT be accepted: rejected locally, no provider call.
curl -sS -o /dev/null -w 'hostile  HTTP %{http_code} (must be 400)\n' \
  "https://$STAGING_HOST/api/tracking/warehouse-imports/%3Cscript%3E"
```

**Expected:** `200` with `{"result":"found",...}` when live; `400` for the hostile
keyword **always**, because that check runs before any network call; `503` with a
Vietnamese message when live mode is off.

**What to record:** the status AND the body's key names. The two upstream envelopes
**differ** — `warehouse-imports` is a bare object, `package-sealings` is wrapped in
`{"data": {...}}` — and a staging check that only looks at status will not notice if
that has changed.

---

## 11.2 Source exposure checks (run this one out loud)

The deploy publishes an **allow list** (`deploy/published-files.txt`). A previous
revision published the whole repository minus a few names, and everything added
later became public: measured against real nginx, `/package.json`,
`/playwright.config.js`, `/tests/e2e/*.spec.js` and the entire `node_modules/` tree
were all fetchable.

Run these against staging. **Every one must be 403 or 404. A 200 is a release
blocker.**

```bash
for p in /backend/app/config.py /docs/SECURITY.md /.git/config /.env \
         /docker-compose.yml /requirements.txt /README.md /package.json \
         /package-lock.json /playwright.config.js /tests/e2e/helpers.js /node_modules; do
  printf '%-38s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$STAGING_HOST$p")"
done
```

And confirm the files that SHOULD be public still are:

```bash
for p in / /robots.txt /sitemap.xml /static/js/register.js /static/css/style.css; do
  printf '%-30s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$STAGING_HOST$p")"
done
```

Guard the same thing statically before you even deploy:

```bash
python3 tools/check_deploy_exposure.py
```

**One more, because it is easy to get wrong:** confirm the deployed site actually
has its CSS and JavaScript. `rsync --files-from` **cancels** the `-r` implied by
`-a`; without an explicit `-r` the directories are created and their contents are
not copied, producing a site that returns `200` for `/` and `404` for every asset
it needs.

```bash
curl -sS -o /dev/null -w 'style.css HTTP %{http_code}\n' "https://$STAGING_HOST/static/css/style.css"
curl -sS -o /dev/null -w 'register.js HTTP %{http_code}\n' "https://$STAGING_HOST/static/js/register.js"
```

---

## 12. Rollback

### 12.1 Read the rollback target first

```bash
sudo cat /srv/viporder/RELEASE        # the SHA that was live before this deploy
```

Call it `<ROLLBACK_SHA>`. If that file did not exist, this was the first deploy
and there is nothing to roll back **to** — the honest rollback is to stop the
service.

### 12.2 Code rollback

```bash
sudo git -C "$SRC" checkout --detach "$ROLLBACK_SHA"
git -C "$SRC" rev-parse HEAD                    # must equal $ROLLBACK_SHA
git -C "$SRC" status --porcelain                # must print nothing

sudo "$VENV/bin/pip" install -r "$SRC/backend/requirements.txt"
sudo rsync -a --delete "$SRC/backend/" "$APP/"
# Publish ONLY what the site is, by name. `-r` is required: --files-from cancels
# the -r implied by -a, so without it you would deploy a site with no CSS or JS.
sudo rsync -a -r --delete --files-from="$SRC/deploy/published-files.txt" \
  "$SRC/" "$SITE/"
sudo chown -R viporder:viporder "$APP" "$SITE"

sudo systemctl restart viporder-web
sudo systemctl reload nginx
echo "$ROLLBACK_SHA" | sudo tee /srv/viporder/RELEASE

cd "$SRC" && ./deploy/post-deploy-check.sh "https://$STAGING_HOST"
```

The check must be re-run: a rollback is a deployment and is verified the same
way (`deploy/post-deploy-check.sh:173`).

### 12.3 Alembic downgrade — and what it does *not* undo

```bash
sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && '$VENV/bin/alembic' downgrade -1"
```

**Going back one revision is not the same as restoring data.** Reversing a
migration undoes *schema*, and several of these migrations drop columns that
hold real customer data. Read the chain before you run it — head is
`0005_live_phone_rule`, and each step down is cumulative:

| Command | Reverses | What is actually lost |
|---|---|---|
| `downgrade -1` | `0005` | Nothing. Index-only: drops `uq_leads_live_phone` and recreates `uq_leads_registered_phone` + `uq_leads_in_flight_phone` (`backend/alembic/versions/0005_live_phone_rule.py:101-113`). |
| `downgrade -2` | `0004` | The `in_flight_at` column — in-flight claim state (`backend/alembic/versions/0004_phone_claim.py:100`). |
| `downgrade -3` | `0003` | `response_status`, `response_body` — the stored provider responses (`backend/alembic/versions/0003_stored_response.py:36-39`). |
| `downgrade -4` | `0002` | `consent_given_at`, `consent_version`, `request_fingerprint` — **the evidence a customer consented, and the idempotency binding** (`backend/alembic/versions/0002_consent_and_fingerprint.py:37-40`). |
| `downgrade -5` | `0001` | `op.drop_table("leads")` — **every lead** (`backend/alembic/versions/0001_create_leads.py:182`). |

So: `downgrade -1` is the only one this runbook treats as routine. Anything
below it destroys data that cannot be regenerated — a registered customer's code
cannot be reissued from the website (`docs/DEPLOYMENT.md:219-222`).

Take a backup **before** any downgrade below `-1`:

```bash
PSQL_URL="${DB_URL/postgresql+psycopg/postgresql}"    # re-derive if you skipped 11.3
sudo install -d -m 0700 /var/backups/viporder
pg_dump "$PSQL_URL" -Fc \
  -f "/var/backups/viporder/leads-before-downgrade-$(date +%Y%m%dT%H%M%S).dump"
```

and restore from a dump, not from a migration, when the data is what you lost.
Keep migrations backward compatible with the previous release — additive
columns, never a destructive change in the same release (`docs/DEPLOYMENT.md:204-207`).

### 12.4 Compose rollback

```bash
cd "$SRC/deploy"
git -C "$SRC" checkout --detach "$ROLLBACK_SHA"
docker compose build app && docker compose up -d
docker compose ps
```

---

## 13. Log inspection

The unit writes to **files**, not to the journal
(`deploy/systemd/viporder-web.service:91-92`) — `journalctl` will show unit
lifecycle only, not requests:

```bash
sudo systemctl status viporder-web --no-pager
sudo journalctl -u viporder-web -n 50 --no-pager      # start/stop/restart only

sudo tail -f /var/log/viporder/app.log                # stdout
sudo tail -f /var/log/viporder/app.err.log            # stderr — errors land here
```

nginx, per the `access_log`/`error_log` directives at
`deploy/nginx/viporder.com.vn.conf:108-109`:

```bash
sudo tail -f /var/log/nginx/viporder.access.log
sudo tail -f /var/log/nginx/viporder.error.log        # level: warn
```

Read the request path from the outside in, which is the order that finds
failures fastest: nginx access log → nginx error log → application log →
database. A `401`/`409`/`422` with a `200` before it is a different problem from
a `502`, and the access log tells you which you have before you read any code.

Watch for the rate-limit rejections this configuration can produce — they are
expected behaviour, not a bug:

```bash
sudo grep -c " 429 " /var/log/nginx/viporder.access.log
```

Two limiters apply: nginx's own zones, `10r/m` on
`location = /api/v1/registrations` and `120r/m` on `^~ /api/`
(`deploy/nginx/viporder.com.vn.conf:25-26,115-131`), and the application's in-process limiter
(`RATE_LIMIT_ATTEMPTS`, `deploy/env.production.example:66-68`). The application's is
the authority; nginx's protects the upstream socket
(`deploy/nginx/viporder.com.vn.conf:23-24`).

Compose path:

```bash
cd "$SRC/deploy"
docker compose logs -f --tail=100 app nginx db
```

**Rotation gap:** no `logrotate` configuration is committed. The unit appends to
`/var/log/viporder/app.log` and `app.err.log` indefinitely
(`deploy/systemd/viporder-web.service:91-92`), and PostgreSQL tables grow with
every lead. Add rotation and a retention policy before this carries real
traffic.

---

## 14. What you cannot do from here

These are genuinely external. None of them can be resolved by any command in
this document, and no step above should be reported as complete while one of
them blocks it.

| Blocked on | Why it is external | Where it is recorded |
|---|---|---|
| **VPS address and SSH access** | Owner-held. Nothing can be deployed. | `docs/PROJECT-STATUS.md:178` |
| **DNS control for `viporder.com.vn`** | Owner-held. TLS issuance via ACME HTTP-01 requires the name to point at the server, so step 8 cannot succeed before this exists. | `docs/PROJECT-STATUS.md:178`, `docs/DEPLOYMENT.md:52-53` |
| **The real KHAIBAO9610 registration contract** | Only the owner/provider can supply it. Until then `KHAIBAO9610_MODE=mock`, and the site must say so. | `docs/PROJECT-STATUS.md:177`, `deploy/env.production.example:76-86` |
| **`khachhang.viporder.com.vn`** | A third-party host. It is linked to, **never** proxied or modified here. | `deploy/nginx/viporder.com.vn.conf:13-14`, `backend/app/config.py:24`; `deploy/post-deploy-check.sh:120-134` |
| **Backup destination and restore drill** | Needs credentials and off-host storage. A backup that has never been restored is not a backup. | `docs/DEPLOYMENT.md:213-222` |
| **Production authorization** | A staging pass is not a go-live. The release PR is a candidate awaiting owner authorization. | `docs/PROJECT-STATUS.md:74` |

---

## 15. Known gaps at the verified revision

Recorded so the next operator does not rediscover them, and so that "the
runbook covered it" is never claimed for something that is not covered.

1. ~~**Compose nginx cannot start**~~ — **CLOSED** (`4445d60`): the snippet is
   mounted. This prose was written against an earlier revision and said "cannot
   start" for several revisions after it could.
2. ~~**Compose publishes the repository root as the web root**~~ — **CLOSED**
   (`4445d60`): the mount is an explicit allow list. **Still worth probing at
   staging**, and §11.2 now gives the commands, because the same class of defect
   was found AGAIN on the systemd path: its rsync used a deny list and published
   `README.md`, `package.json`, `playwright.config.js`, `tests/` and
   `node_modules/` — measured against real nginx. That path now uses
   `--files-from=deploy/published-files.txt`, checked by
   `tools/check_deploy_exposure.py`.
3. **The deployed site must be proved to have its CSS and JavaScript.**
   `rsync --files-from` CANCELS the `-r` implied by `-a`, so without an explicit
   `-r` the directories are created and their contents are not copied. Two curls
   in §11.2.
3. **`--workers 1` is pinned for correctness, not performance.** Raising it
   multiplies the effective rate limit, and the concurrency guarantees are only
   verified at one worker. `backend/tests/test_concurrency.py` must be re-run
   against the multi-worker topology first (6A).
4. **No log rotation** is committed for the unit's append-only log files (13).
5. **No browser E2E harness** is committed (11).
6. **Docker was never executed** for this project (`docs/PROJECT-STATUS.md:73`),
   and it was not available in the environment where this runbook was written —
   every `docker compose` command above is derived from
   `deploy/docker-compose.yml` and was **not** executed.
7. **`README.md` (repository root) is stale**: it lists `alembic/versions/ migrations 0001..0003`
   while five migrations exist (`0001`–`0005`). Trust the directory, not the
   README.

### The three gates to run after any edit to this document or to `deploy/`

```bash
python3 tools/check_site.py             # HTML, links, a11y, SEO, business link rules
python3 tools/check_nginx_config.py     # the add_header inheritance guard (7.4)
python3 tools/check_repo_hygiene.py     # secrets, tracked data, oversized files
```

All three must exit `0`. They do not validate nginx syntax or a live host — pair
them with `nginx -t` (7.5) and `deploy/post-deploy-check.sh` (10).
