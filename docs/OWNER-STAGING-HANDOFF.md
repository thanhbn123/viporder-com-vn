# Owner staging handoff — VIPORDER.COM.VN

> ## Status: NOTHING DEPLOYED
> There is no staging host, no IP address, no DNS record and no credential
> anywhere in this repository. **This document is a procedure, not a report.**
> Nothing in it has been run against a real host. If you are reading it after a
> deployment and looking for evidence that the deployment happened, the evidence
> is not here — it is the terminal transcript you kept.
>
> Written against `docs/final-packs` @ `ae6c53a` (`origin/develop` @ `ae6c53a`).
> Every `file:line` anchor below was read at that revision.

**Who this is for.** You own the domain and you are paying for the server. You
are not an engineer. Every command below is meant to be copied and pasted whole.
Each step says three things:

* **Do** — the command(s).
* **Success looks like** — the exact output that means you may continue.
* **If it fails** — what the failure means and what to do.

**Do not skip a "success looks like" check.** Every one of them exists because
something in this project once silently did the wrong thing and reported success.
The most expensive of those is recorded at `docs/STAGING-RUNBOOK.md:244-266`: an
env file that fails to parse, exits **0**, and silently drops three keys.

**How much can you do before you have the server?** Nothing past step 0. Steps
1–16 all run on the host.

---

## zsh notes — read once

Your shell is zsh. The blocks below are written for it. Three consequences:

1. **`read -s -p` does not work in zsh.** It accepts zero characters and then
   reports a wrong password — this already cost one whole diagnostic session in
   this project (`CLAUDE.md` §12.2). Where a secret is read, this document uses
   `stty -echo` + plain `read`, never `read -s -p`.
2. **Paste the preamble block as one block.** An unquoted `SHA=<...>` is a
   redirection, not an assignment.
3. **Do not add `set -e` or `set -u` to the preamble.** In an interactive shell
   they persist and will break commands you run later. The preamble only assigns
   variables, on purpose (`docs/STAGING-RUNBOOK.md:78-80`). Where a step must stop
   the run, it prints a refusal line and tells you to stop rather than calling
   `exit` — `exit` in an interactive zsh closes your terminal window.

---

## 0. The shell preamble — paste this once

Replace every `<<FILL IN ...>>` before pasting. The full list of what you must
supply, and why, is in the table at the end of this document.

```zsh
# ---- replace every <<FILL IN ...>> below, keep the quotes ----
SHA="<<FILL IN: 40-hex commit SHA to deploy>>"
STAGING_HOST="<<FILL IN: the hostname you will browse, e.g. staging.example>>"
STAGING_IP="<<FILL IN: public IPv4 of the host>>"
TLS_CONTACT_EMAIL="<<FILL IN: an email you read>>"
DB_PASSWORD="<<FILL IN: the PostgreSQL password you will set in step 4>>"
ADMIN_API_TOKEN="<<FILL IN or leave empty: empty disables the admin retry route>>"
TEST_PHONE_9="<<FILL IN: 9 digits, first digit must be 2 3 5 7 8 or 9>>"
ROLLBACK_SHA="<<FILL IN on a re-deploy; leave empty on a first deploy>>"

# ---- these are the paths the shipped config expects; do not change them ----
SRC=/srv/viporder/src
VENV=/srv/viporder/venv
APP=/srv/viporder/app
SITE=/srv/viporder/site
ENVFILE=/etc/viporder/viporder.env
REPO_URL="https://github.com/thanhbn123/viporder-com-vn.git"

printf 'SHA=[%s] HOST=[%s] IP=[%s]\n' "$SHA" "$STAGING_HOST" "$STAGING_IP"
```

**Success looks like** those three values printed back exactly as you typed them.

**If it fails:** if they print empty, you pasted with the placeholders still in
or you are not in the shell you think you are. Nothing later will work; fix this
first.

**Where the SHA comes from.** It is the 40-character value printed in CI on the
line `Verified revision: <sha>` — CI checks the **pull-request head SHA**, not a
merge commit and not a branch name (`.github/workflows/ci.yml:31-38`). If you do
not have that line, you do not have a release to deploy yet.

---

## 1. Server prerequisites

**Source of truth:** `STAGING_HOST_REQUIREMENTS.md` §2, §3, §4, §5, §9.

| Item | Requirement |
|---|---|
| OS | Debian 12 or Ubuntu 22.04+, 64-bit (`docs/DEPLOYMENT.md:45`) |
| CPU | 2 vCPU recommended; 1 is the floor |
| RAM | 2 GB recommended |
| Disk | 20 GB minimum, 40 GB recommended |
| Python | 3.11 or 3.12 (`docs/DEPLOYMENT.md:46`) |
| Web server | nginx 1.20 or newer (`docs/DEPLOYMENT.md:48`) |
| Database | PostgreSQL 14+; 16 recommended (`deploy/docker-compose.yml:20`) |
| Inbound ports | **80 and 443 only** |
| Must NOT be reachable | **8000** (application) and **5432** (database) |
| Outbound | 443 to `github.com`, to check out the release |

### Docker is NOT required

`STAGING_HOST_REQUIREMENTS.md` §4 answers this directly: *"Is Docker required?
No — and the recommended path does not use it."* The repository ships two paths
and they are alternatives — *"Use one or the other, not both"*
(`deploy/docker-compose.yml:4-5`). **This procedure uses the systemd path
throughout.** That is not only a preference: systemd is the path this project has
actually exercised, and `docs/STAGING-RUNBOOK.md:376` names it "6A. systemd
(recommended)". It also means **Node.js, npm and the Playwright browsers are not
needed on this host** (`STAGING_HOST_REQUIREMENTS.md` §8).

### Do

```zsh
# On the host.
sudo cat /etc/os-release | head -3
uname -m
getconf _NPROCESSORS_ONLN
free -m | head -2
df -h / | tail -1
```

**Success looks like** Debian 12 / Ubuntu 22.04 or newer; `x86_64` or `aarch64`;
2 or more CPUs; **≥ 1900 MB** total memory; at least **20G** available on `/`.

### Then install the packages

```zsh
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3.12 python3.12-venv python3.12-dev \
  nginx certbot \
  postgresql postgresql-client rsync ca-certificates curl

python3.12 --version
nginx -v
psql --version
```

**Read this about the package list.** `docs/STAGING-RUNBOOK.md:150-156` installs
`postgresql-client` and **not** `postgresql` (the server), but its own step 4.2
then runs `sudo -u postgres psql`, which needs a PostgreSQL **server** on that
host. If PostgreSQL is not already installed, the list above adds it. This
document is deliberately not copying that list literally.

**Success looks like** `Python 3.12.x`, `nginx version: nginx/1.20` or newer, and
a `psql` version line. `python3.12` may be called `python3` on your distribution
— use whichever exists, and keep it consistent for the rest of the run.

**If it fails:** `Unable to locate package python3.12` means the distribution is
older than Debian 12 / Ubuntu 22.04, or the package is named `python3.11`. Check
`docs/DEPLOYMENT.md:45-46` — 3.11 is acceptable. If `nginx -v` reports older than
1.20, upgrade it before continuing; the shipped config uses `http2 on;`
(`deploy/nginx/viporder.com.vn.conf:60,75`), which older nginx does not accept.

### Service account and directories

```zsh
sudo useradd --system --home /srv/viporder --shell /usr/sbin/nologin viporder \
  || echo "user viporder already exists"
sudo install -d -o viporder -g viporder /srv/viporder/app /srv/viporder/site
sudo install -d -o viporder -g viporder /var/lib/viporder /var/log/viporder
sudo install -d -m 0750 -o root -g viporder /etc/viporder
sudo install -d -o root -g root /srv/viporder
```

`/var/lib/viporder` and `/var/log/viporder` are the **only** writable paths the
service is granted (`deploy/systemd/viporder-web.service:84`); everything else is
read-only under `ProtectSystem=strict` (line 67).

**Success looks like** all four `install` commands exit silently, and:

```zsh
id viporder
sudo stat -c '%a %U:%G %n' /etc/viporder
```

prints a `viporder` system user, and `750 root:viporder /etc/viporder`.

**If it fails:** `useradd: user 'viporder' already exists` is handled by the `||`
branch and is fine. A permission error means you are not using `sudo`.

---

## 2. `git clone` + check out the **exact SHA**

### Why a pinned SHA, and never `git pull` on a serving directory

The SHA **is** the deployment's identity. `/srv/viporder/RELEASE` records it
(`docs/STAGING-RUNBOOK.md:129-140`), rollback means checking out the previous
value of that file, and every claim about "what is running" is a claim about that
SHA.

`git pull` moves the working tree to **whatever the branch tip is at that
moment**. Three things break at once:

1. The code being served no longer matches the SHA you recorded, so no later
   command can tell you what is running.
2. The rollback target becomes ambiguous — "previous SHA" stops meaning anything.
3. A pull can leave a merge state or a dirty tree, and **a deployment from a
   dirty tree is not the revision you recorded** (`docs/DEPLOYMENT.md` §9:
   *"Code from an uncommitted working tree"*).

This is the same assertion CI makes before it trusts a revision
(`.github/workflows/ci.yml:50-54`): check out the SHA, then prove `rev-parse HEAD`
equals it and `status --porcelain` is empty.

### 2.1 Clone

```zsh
sudo git clone "$REPO_URL" "$SRC"
git -C "$SRC" remote get-url origin
```

**Success looks like** the second command prints exactly `$REPO_URL`. This is a
public repository and the remote is the only thing deciding what code you get, so
check it rather than assuming (`docs/STAGING-RUNBOOK.md:99-106`).

### 2.2 Check out the SHA and prove it

```zsh
sudo git -C "$SRC" checkout --detach "$SHA"

git -C "$SRC" rev-parse HEAD          # must print exactly $SHA
git -C "$SRC" status --porcelain      # must print NOTHING
```

**Success looks like** `rev-parse` prints your SHA character-for-character, and
`status --porcelain` prints nothing at all.

**If it fails:** if `status --porcelain` prints **anything**, stop. Do not
continue and do not "clean it up" — re-clone. If `rev-parse` prints a different
SHA, the checkout failed and you are about to deploy the wrong revision.

### 2.3 Record the release, and read the old one first

Read the previous value **before** overwriting it — that value is your rollback
target:

```zsh
sudo cat /srv/viporder/RELEASE 2>/dev/null || echo "(no previous release — first deploy)"
echo "$SHA" | sudo tee /srv/viporder/RELEASE
```

**Success looks like** the first command prints the previous SHA, or the
`(no previous release — first deploy)` line. Copy the previous SHA into
`ROLLBACK_SHA` in your preamble if this is a re-deploy.

### 2.4 Build the virtualenv, `app/` and `site/` from this exact revision

```zsh
sudo python3.12 -m venv "$VENV"
sudo "$VENV/bin/pip" install --upgrade pip
sudo "$VENV/bin/pip" install -r "$SRC/backend/requirements.txt"
sudo chown -R viporder:viporder "$VENV"
"$VENV/bin/python" -c "import fastapi, uvicorn; print('fastapi', fastapi.__version__, 'uvicorn', uvicorn.__version__)"

sudo rsync -a --delete "$SRC/backend/" "$APP/"

# Publish ONLY what the site is, by name. `-r` IS REQUIRED.
# `--files-from` CANCELS the `-r` implied by `-a`. Without an explicit `-r` the
# directories are created and their CONTENTS are not copied -- producing a site
# that returns 200 for `/` and 404 for every stylesheet and script it needs.
sudo rsync -a -r --delete --files-from="$SRC/deploy/published-files.txt" \
  "$SRC/" "$SITE/"
sudo chown -R viporder:viporder "$APP" "$SITE"
```

**The `-r` is not decoration.** `deploy/published-files.txt` names five entries —
`index.html`, `404.html`, `robots.txt`, `sitemap.xml`, `static` — and `static` is
a **directory**. `--files-from` cancels the recursive flag that `-a` implies, so
without `-r` you get an empty `static/` directory and a site with no CSS and no
JavaScript. Step 13 exists to catch exactly this.

**Success looks like** the `pip install` ends with `Successfully installed …`, the
`python -c` line prints two versions, and:

```zsh
ls "$SITE" | grep -E '^(backend|docs|deploy|tools)$' || echo "OK: web root is static-only"
```

prints `OK: web root is static-only`.

**If it fails:** if that `ls` prints any of those four names, **stop — you are
about to publish the application's source and internal documents over the
public internet.** Re-run the `rsync` with the `-r` flag present and check
`deploy/published-files.txt` was copied from the pinned SHA.

---

## 3. Environment file from `deploy/env.production.example`

```zsh
sudo install -m 0640 -o root -g viporder \
  "$SRC/deploy/env.production.example" "$ENVFILE"
sudo vi "$ENVFILE"
sudo chmod 0640 "$ENVFILE"
sudo chown root:viporder "$ENVFILE"
sudo stat -c '%a %U:%G %n' "$ENVFILE"
```

**Success looks like** `640 root:viporder /etc/viporder/viporder.env`.

### Everything the template contains, and what you must decide

The template has **24 keys**. Every one of them is read by the application — that
is enforced by a test, not by a comment (`deploy/env.production.example:11-13`;
`backend/tests/test_env_templates.py`). **You do not add keys.** You change these:

| Key | What you set | Why |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://viporder:<<the password you set in step 4>>@127.0.0.1:5432/viporder` | The template's default is SQLite under `/var/lib/viporder/` (`deploy/env.production.example:57-58`). SQLite is permitted for staging only (`docs/DEPLOYMENT.md:47`). |
| `ADMIN_API_TOKEN` | `<<FILL IN>>` **or leave empty** | **Empty means the admin retry route returns 404 — disabled, not merely unauthorised** (`deploy/env.production.example:94-98`). |

**Leave every other key at its template value.** In particular:

| Key | Value | Do not change because |
|---|---|---|
| `APP_ENV` | `production` | Hides `/api/docs`, `/redoc` and `/openapi.json` |
| `ENABLE_API_DOCS` | *(empty)* | Docs served outside production, hidden in it |
| `AUTO_CREATE_SCHEMA` | `false` | `true` makes the app create tables with `create_all` and **bypass Alembic**, letting the live schema drift from the migration history (`deploy/env.production.example:33-40`) |
| `CORS_ALLOW_ORIGINS` | *(empty)* | Empty = CORS disabled, same-origin only. Never `*` |
| `TRUST_PROXY_HEADERS` | `yes` | See the note below |
| `KHAIBAO9610_MODE` | `mock` | The real provider contract has not been supplied (issue #4). Changing this does **not** make registration real |
| `KHAIBAO9610_ENABLE_REAL_CALLS` | `no` | A real outbound call needs **both** this and the mode set to `http`. That second switch exists so a real call cannot happen by accident (`deploy/env.production.example:76-85`) |
| `MOCK_PROVIDER_BEHAVIOUR` | `success` | Changes what the mock answers; read the comment before changing it |

`TRUST_PROXY_HEADERS` is worth one sentence, because it looks like the control
and is not: both shipped deployment paths start uvicorn with `--proxy-headers`,
which rewrites the client address **before the application sees the request**.
The operative control is uvicorn's `--forwarded-allow-ips`, pinned to
`127.0.0.1` in the systemd unit (`deploy/systemd/viporder-web.service:53`). See
`deploy/nginx/README.md:108-114`.

### Do NOT `source` the environment file

Measured in `docs/STAGING-RUNBOOK.md:244-266`. The template contains a value with
parentheses and spaces (`KHAIBAO9610_USER_AGENT`). Sourcing it as a shell script
prints a syntax error, **exits 0**, and silently leaves three keys unset
(`KHAIBAO9610_USER_AGENT`, `MOCK_PROVIDER_BEHAVIOUR`, `ADMIN_API_TOKEN`). Your
admin token would be dropped and the retry route would 404 for reasons nothing
reports.

systemd is unaffected — `EnvironmentFile=` is parsed by systemd itself, not by a
shell (`deploy/systemd/viporder-web.service:25`). Where a value is needed on the
command line, this document extracts that single key with `grep`/`cut`.

**Success looks like** step 5 prints your DSN with the password masked.

**If it fails:** `stat` showing `644` or `root:root` means the install did not
apply the mode/owner. Re-run the three `chmod`/`chown` lines. A
world-readable env file on a host that also serves the public site is a release
blocker.

---

## 4. PostgreSQL role and database

```zsh
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE ROLE viporder LOGIN;"
sudo -u postgres psql -c "CREATE DATABASE viporder OWNER viporder;"
sudo -u postgres psql -c '\password viporder'
```

Use `\password` rather than `PASSWORD '...'` on a command line: an inline
password ends up in your shell history and in the PostgreSQL log
(`docs/STAGING-RUNBOOK.md:282-284`). `\password` prompts without echoing and asks
you to type it twice. Type the **same** `DB_PASSWORD` you put in step 0.

**Success looks like:**

```zsh
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='viporder'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='viporder'"
```

prints `1` twice.

**If it fails:** `psql: command not found` or `could not connect` means the
PostgreSQL **server** package is not installed — this is the gap described in
step 1. `role "viporder" already exists` is harmless on a re-run; use
`ALTER ROLE viporder WITH PASSWORD '...'` at the `psql` prompt instead.

Only if you deliberately chose SQLite for staging: skip this step and leave
`DATABASE_URL` at its template value. Record that deviation.

---

## 5. Migrations, and how to verify the revision

Migrations run **before** the service starts. `AUTO_CREATE_SCHEMA=false` means
nothing creates the tables for you.

### Why the DSN is named explicitly

`backend/alembic/env.py:31-38` resolves the database URL in this order:
(1) `DATABASE_URL` from the process environment, (2) `sqlalchemy.url` from
`backend/alembic.ini` — deliberately blank, (3) the application default
`sqlite:///./var/viporder.db`.

So a bare `alembic upgrade head` on the server **does not fail**. It creates
`/srv/viporder/app/var/viporder.db`, reports success, and leaves your real
database untouched (`docs/STAGING-RUNBOOK.md:294-306`). Pass the DSN.

### Do

```zsh
DB_URL="$(sudo grep -E '^DATABASE_URL=' "$ENVFILE" | tail -1 | cut -d= -f2-)"
printf 'DATABASE_URL read from %s is [%s]\n' "$ENVFILE" "$DB_URL"
printf 'migrating: %s\n' "$(printf '%s' "$DB_URL" | sed -E 's#(//[^:]+):[^@]+@#\1:***@#')"
```

**Success looks like** the bracketed value is a real DSN starting
`postgresql+psycopg://`, and the masked line shows `:***@`.

**If it fails:** an empty `[]` means the key is missing or misspelled in the env
file. **Stop here** — fix step 3. Running the migration with an empty DSN is the
silent-SQLite failure described above.

```zsh
sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && exec '$VENV/bin/alembic' upgrade head"

sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && '$VENV/bin/alembic' current"
```

**Success looks like** the `current` command prints exactly the head of the
migration chain, which at this SHA is:

```
0005_live_phone_rule (head)
```

The chain is `0001_create_leads` → `0002_consent_and_fingerprint` →
`0003_stored_response` → `0004_phone_claim` → `0005_live_phone_rule`
(`backend/alembic/versions/`, verified by listing the directory).

**If it fails:** **do not accept "no output" as success** — that is the whole
point of running `current`. `No such file or directory` for `alembic` means the
venv is not built or `$VENV` is wrong. `ModuleNotFoundError: psycopg` means
`pip install` did not complete. If `current` prints a revision **below** `0005`,
the upgrade did not run to head; re-run it and read the error.

---

## 6. The application, as a systemd service, with `--workers 1`

```zsh
sudo cp "$SRC/deploy/systemd/viporder-web.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viporder-web
sudo systemctl status viporder-web --no-pager
```

### Why `--workers 1` — this is correctness, not thrift

The rate limiter keeps its counters **in process memory**. Each worker enforces
the limit independently, so with N workers the effective limit is
`N × RATE_LIMIT_ATTEMPTS`. At `--workers 2`, a registration limit of 10 becomes
20 in practice. For a registration form, a correct limit matters more than a
second worker — and one process is not the bottleneck here, the database and the
upstream provider are.

- The flag is pinned at `deploy/systemd/viporder-web.service:51`.
- The reasoning is in the unit file itself at
  `deploy/systemd/viporder-web.service:31-38` and in
  `deploy/nginx/README.md:102-106`.
- Raise it **only** after moving the limiter to a shared store, and after
  re-running `backend/tests/test_concurrency.py` against that topology
  (`docs/STAGING-RUNBOOK.md:387-397`). The concurrency guarantees are only
  verified at one worker.

Do not edit the unit file on the server. If it needs changing, it changes in the
repository and you deploy a new SHA.

### Prove it is listening on loopback only, and on one worker

```zsh
sudo ss -ltnp | grep 8000
sudo systemctl show viporder-web -p NRestarts -p ActiveState -p MainPID
ps -o pid,ppid,command -p "$(systemctl show -p MainPID --value viporder-web)"
```

**Success looks like** `127.0.0.1:8000` (never `0.0.0.0:8000` or `[::]:8000`),
`ActiveState=active`, `NRestarts=0`, and a process line containing
`--workers 1`.

**If it fails:** `0.0.0.0:8000` means the application is directly reachable from
the internet, where `--forwarded-allow-ips` can no longer protect it. Stop and
fix the unit before opening the firewall. `NRestarts` climbing means the process
is crash-looping — read `/var/log/viporder/app.err.log` (step 15) before doing
anything else.

---

## 7. nginx — install the files, install **exactly one** upstream, `nginx -t`

### 7.1 Install the four files

The source → destination table is `deploy/nginx/README.md:5-10`:

```zsh
sudo install -d /etc/nginx/snippets /etc/nginx/upstreams
sudo cp "$SRC/deploy/nginx/viporder-security-headers.conf" /etc/nginx/snippets/
sudo cp "$SRC/deploy/nginx/proxy_params_viporder"          /etc/nginx/proxy_params_viporder
```

### 7.2 Install exactly ONE upstream

`upstream-systemd.conf` and `upstream-compose.conf` are **alternatives**. Both
must land on the same path, because the site config includes that path by name
(`deploy/nginx/viporder.com.vn.conf:35`):

```
include /etc/nginx/upstreams/viporder-upstream.conf;
```

You are on the systemd path, so:

```zsh
sudo cp "$SRC/deploy/nginx/upstream-systemd.conf" /etc/nginx/upstreams/viporder-upstream.conf
```

The address is `127.0.0.1:8000`
(`deploy/nginx/upstream-systemd.conf:11`). The compose variant says `app:8000`
because it is a sibling container. Because the address is an `include`, getting
this wrong **fails `nginx -t` loudly** instead of silently proxying into the
void — measured in `docs/STAGING-RUNBOOK.md:578-590`.

### 7.3 Install the site config

```zsh
sudo cp "$SRC/deploy/nginx/viporder.com.vn.conf" /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/viporder.com.vn.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
python3 "$SRC/tools/check_nginx_config.py"
```

**Success looks like** `check_nginx_config.py` exits `0` with
`nginx-config: 10 location block(s), 0 error(s)`. It enforces the
`add_header`-inheritance rule as a program rather than a comment
(`tools/check_nginx_config.py`; `deploy/nginx/README.md:73-84`). Its own scope
line says it does **not** validate nginx syntax — that is what `nginx -t` is for.

### 7.4 If `$STAGING_HOST` is not `viporder.com.vn`, you must edit the config

The checked-in config names **exactly** `viporder.com.vn` and
`www.viporder.com.vn` (`deploy/nginx/viporder.com.vn.conf:43,62,77`). A different
hostname is not a DNS-only change. Six edits, all in
`/etc/nginx/sites-available/viporder.com.vn.conf`:

| # | Line | Change |
|---|---|---|
| 1 | `:43` | the port-80 `server_name` |
| 2 | `:62` | the `www` 443 server block's `server_name` |
| 3 | `:77` | the apex 443 server block's `server_name` |
| 4 | `:64-65` | the two `ssl_certificate…` paths in the `www` block |
| 5 | `:80-81` | the two `ssl_certificate…` paths in the apex block |
| 6 | `:51`, `:67` | the two `return 301 https://viporder.com.vn$request_uri;` lines — otherwise staging bounces your visitors to production |

Record this as a deliberate deviation from the deployed SHA. Do not push the
edited file back to the repository.

### 7.5 `nginx -t` — and what it must say **before** TLS exists

```zsh
sudo nginx -t
```

**Before step 8 issues the certificate, this command MUST FAIL**, with a message
like:

```
nginx: [emerg] cannot load certificate "/etc/letsencrypt/live/…/fullchain.pem": BIO_new_file() failed
nginx: configuration file /etc/nginx/nginx.conf test failed
```

**That specific failure is the correct result at this point in the sequence.** The
two TLS server blocks point at a certificate that does not exist yet
(`deploy/nginx/viporder.com.vn.conf:64-65,80-81`). TLS comes next precisely so
that this config can parse.

**If it fails with anything else — stop:**

| Message | Meaning |
|---|---|
| `host not found in upstream "app:8000"` | You installed the **compose** upstream. Replace it with `upstream-systemd.conf` (7.2). |
| `open() "…/upstreams/viporder-upstream.conf" failed` | You installed **no** upstream. Install exactly one (7.2). |
| `open() "…/snippets/viporder-security-headers.conf" failed` | The snippet is missing. Re-run 7.1. This is a fatal `[emerg]`, not a warning — nginx will not start. |
| `unknown directive "http2"` | nginx is older than 1.20. Go back to step 1. |

**Success looks like** — after step 8 — `syntax is ok` and `test is successful`.

**What `nginx -t` cannot tell you:** that the security headers are actually
returned. Only a request to `/` shows that (step 9). The inheritance trap is
described at `docs/STAGING-RUNBOOK.md:518-567`: one `add_header Cache-Control …`
inside a `location` silently cancels **every** server-level header for that
location. That bug shipped once — on `/` and on `/static/style.css`, the six
headers went from **6/6 to 0/6**, while `curl -I` on any other path looked
perfect (`deploy/nginx/README.md:53-58`).

---

## 8. TLS

Order matters: the certificate must exist before the site config parses, and the
initial issuance needs port 80 free.

```zsh
sudo install -d /var/www/certbot
sudo systemctl stop nginx
sudo certbot certonly --standalone --non-interactive --agree-tos \
  -m "$TLS_CONTACT_EMAIL" \
  -d "$STAGING_HOST"
```

**Success looks like** `Congratulations! Your certificate and chain have been
saved at: /etc/letsencrypt/live/<your host>/fullchain.pem`.

**If you changed `$STAGING_HOST` (7.4):** certbot wrote to
`/etc/letsencrypt/live/$STAGING_HOST/`, but the config still points at
`/etc/letsencrypt/live/viporder.com.vn/`. Edit the four `ssl_certificate` paths
to match. Then:

```zsh
sudo nginx -t
sudo systemctl start nginx
```

**Success looks like** `syntax is ok` / `test is successful`, then `start` exits
silently. Verify the certificate is the one being served:

```zsh
openssl s_client -servername "$STAGING_HOST" -connect "$STAGING_IP:443" </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -dates
```

**Success looks like** a subject containing your hostname and a `notAfter` in the
future.

**Then prove renewal works, today, not in two months:**

```zsh
sudo certbot renew --dry-run
systemctl list-timers certbot.timer --no-pager
```

**Success looks like** `Congratulations, all simulated renewals succeeded` and a
listed next-run time.

**If it fails:** fix renewal **now**. A certificate that cannot renew is a
scheduled outage, not a future problem (`docs/STAGING-RUNBOOK.md:655-656`). The
two most common causes are the ACME path not being reachable over plain HTTP
(`deploy/nginx/viporder.com.vn.conf:46-48`) and an actual `AAAA` record pointing
at a host that does not answer on IPv6 — the config listens on IPv6
(`:42,75`), so a half-configured AAAA record is a real failure mode
(`STAGING_HOST_REQUIREMENTS.md:246-249`).

**Watch item, read from the config and not measured here:** the server block sets
`ssl_stapling on; ssl_stapling_verify on;` (`:86-87`) but declares no `resolver`
directive. OCSP stapling needs a resolver to fetch the response, so stapling may
be silently inactive. Check rather than assume:

```zsh
openssl s_client -servername "$STAGING_HOST" -connect "$STAGING_IP:443" -status </dev/null 2>/dev/null \
  | grep -A1 "OCSP response"
```

### The HSTS value you are now serving, and what it does and does not pin

`deploy/nginx/viporder-security-headers.conf:45` sends:

```
Strict-Transport-Security: max-age=31536000
```

**`includeSubDomains` is deliberately NOT there** (the reasoning is in that
file's own comment, lines 29-43, and in `docs/SECURITY.md` §5.2). What this
means for you:

* `max-age=31536000` pins **exactly the hostname it was served from** to HTTPS for
  one year, in every browser that loaded it. So **do not serve staging on
  `viporder.com.vn` itself** — you would pin every visitor's browser to HTTPS on
  the production name for a year, and no rollback releases them.
* Because `includeSubDomains` is withheld, `khachhang.viporder.com.vn` — the
  existing customer portal — is **not** covered by this header. That is the point
  of withholding it.
* See "What is NOT proven yet" at the end: the application tier still emits
  `includeSubDomains` on its own responses (`backend/app/middleware.py:253`),
  and the two tiers have not both been measured together on a live host.

---

## 9. `/health`

Directly on the host first, before DNS:

```zsh
curl -sS "http://127.0.0.1:8000/api/v1/health" | python3 -m json.tool
```

Through nginx, testing the real `server_name` against the host's IP **without
touching DNS** — this is the trick that validates the shipped config before
cutover:

```zsh
curl -sS --resolve "$STAGING_HOST:443:$STAGING_IP" \
  "https://$STAGING_HOST/api/v1/health" | python3 -m json.tool
```

**Success looks like** a body of this shape
(`backend/app/routers/health.py:23-38`):

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

`status` is `ok` **only when the database is reachable**; the response is `503`
when it is not.

Now confirm the whole header set is present on `/` — the path that once lost all
six:

```zsh
curl -sI --resolve "$STAGING_HOST:443:$STAGING_IP" "https://$STAGING_HOST/" | grep -Ei \
  'strict-transport-security|x-content-type-options|x-frame-options|referrer-policy|permissions-policy|content-security-policy' | wc -l
```

**Success looks like** `6`. Anything less means the `include` in the matching
`location` is missing, **no matter what `nginx -t` said**.

**If it fails:** `503` means the database is unreachable — re-check step 4 and
the DSN in step 3. `502 Bad Gateway` from nginx means the app is not listening on
`127.0.0.1:8000` (step 6). A count below 6 means you are looking at a config
whose `location = /` lost its `include` line — stop and fix it, because the four
other locations will look correct while the homepage ships with no headers at
all.

---

## 10. Registration — a **MOCK** test

**Read this first: `KHAIBAO9610_MODE=mock` is not working registration.** No live
call has ever been made from this repository, and the real provider contract has
not been supplied (issue #4; `docs/KHAIBAO9610-INTEGRATION.md`). The website must
not be described as taking real registrations. This step proves the
**validation, the lead store, and the plumbing** — not the provider.

### 10.1 The safe probe — creates nothing

```zsh
mkdir -p "$HOME/viporder-runbook"
cd "$SRC" && ./deploy/post-deploy-check.sh "https://$STAGING_HOST" \
  | tee "$HOME/viporder-runbook/post-deploy-$(date +%Y%m%dT%H%M%S).log"
```

That script measures the site **from the outside, over the public internet**,
which is the only vantage point that matters (`deploy/post-deploy-check.sh:5-8`).
It deliberately sends an **invalid** registration payload, so it proves
validation is live without creating a real customer (`:14-15,152-162`).

**Success looks like** `PASS=n  FAIL=0  WARN=n` and `DEPLOYMENT VERIFIED`, exit
`0`.

**Know what this probe does not test.** Its payload is
`{"full_name":"","phone":"not-a-phone","password":"short","consent":false}`
(`deploy/post-deploy-check.sh:156`) — it omits `accept_terms` altogether. So a
`422` from it proves *a* validation gate is live, but it does **not** by itself
prove the `accept_terms` gate is. Step 10.2 covers that.

**If it fails:** two results need *reading*, not ticking (`:159`, `:133`):

* `WARN rate limited (429)` on the registration probe means the limit is working
  and **validation was not exercised** on that run. Re-run from a different
  source, or accept that validation is unproven by this run. **Do not read 429 as
  success.**
* A `WARN` on the customer portal means a **third-party host** returned something
  unexpected. Investigate it; do not "hotfix" it from your server — you do not
  own that host.

### 10.2 A real submission against MOCK

Use a phone number you own. Valid values, from the real validation rules: name
2–120 characters (`backend/app/schemas.py:76`), phone = `+84` + 9 national digits
whose first digit is one of `2 3 5 7 8 9` (`backend/app/phone.py:22`), password
≥ 8 characters (`backend/app/schemas.py:29`).

**There are TWO consent fields and both must be `true`.** The API requires
`consent` (`backend/app/schemas.py:97`, validator at `:178-182`) **and**
`accept_terms` (`:120`, validator at `:185-189`). They are deliberately not
merged — `consent` is this service's own record of what wording the customer
agreed to and when; `accept_terms` is what is forwarded to the provider
(`backend/app/schemas.py:103-108`). The page's own JavaScript sends both from the
same checkbox so they cannot disagree (`static/js/register.js:635-650`). A
payload with only one of them returns **422**.

```zsh
curl -sS -o /tmp/reg.json -w 'registration HTTP %{http_code}\n' \
  -X POST "https://$STAGING_HOST/api/v1/registrations" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: owner-runbook-$TEST_PHONE_9" \
  -d "{\"full_name\":\"Khach Kiem Thu\",\"phone\":\"+84$TEST_PHONE_9\",\"password\":\"matkhau123\",\"accept_terms\":true,\"consent\":true}"
python3 -m json.tool /tmp/reg.json
```

**Success looks like** a status of **201** (provider confirmed), **202** (provider
unreachable, lead retained as `PENDING`) or **409** (this phone already
registered) — `docs/PROJECT-STATUS.md:63`. With `MOCK_PROVIDER_BEHAVIOUR=success`
you should see 201.

Note what 201 does **not** prove: `docs/KHAIBAO9610-INTEGRATION.md:157-160`
records that the real `POST /frontend/v1/register` **does not return the customer
code**, so a real test cannot be verified end-to-end from the response anyway.
Never treat a 201 alone as "registration works".

### 10.3 Corroborate in the database — the row is the evidence

A success screen is the application's claim about itself. The row is the evidence
(`docs/STAGING-RUNBOOK.md:799-802`):

```zsh
PSQL_URL="${DB_URL/postgresql+psycopg/postgresql}"
psql "$PSQL_URL" -c "SELECT id, phone, registration_status, external_customer_code, created_at
                       FROM leads WHERE phone = '+84$TEST_PHONE_9'
                       ORDER BY created_at DESC LIMIT 5;"
```

**Success looks like** exactly one row for that phone, with a
`registration_status` consistent with the HTTP status you got.

**If it fails:** zero rows with a 201 means the API answered but the lead was not
stored. Stop and treat it as a blocker.

**Clean up before re-testing.** Migration `0005` enforces one live lead per phone
(`backend/alembic/versions/0005_live_phone_rule.py:14-19`), so that phone can
never register again until the row is removed. Use a fresh phone per run, or
delete the row deliberately — and only ever with the `phone =` predicate:

```zsh
psql "$PSQL_URL" -c "DELETE FROM leads WHERE phone = '+84$TEST_PHONE_9';"
```

Never run that against production.

---

## 11. Tracking GET — warehouse import and package sealing

These two public lookups are separate from registration and worth checking on
their own: **a working registration says nothing about whether the READ
endpoints are reachable.** Both sit behind the same double opt-in as
registration (`KHAIBAO9610_MODE=http` **and**
`KHAIBAO9610_ENABLE_REAL_CALLS=yes`).

**With the default mock configuration they answer `503 PROVIDER_UNAVAILABLE` —
and that is the correct, honest response, not a fault.** Check which mode you are
in, reading it from the running service rather than the file:

```zsh
sudo systemctl show viporder-web -p Environment | tr ' ' '\n' \
  | grep -E 'KHAIBAO9610_(MODE|ENABLE_REAL_CALLS)'
```

### 11.1 The two rules that trip people up

1. **The keyword must be the FULL tracking code.** Measured 2026-10-02:
   `KY4001103376087` → **404**, but `KY4001103376087-2-4-|s` → **200**. A partial
   code is not found, and that is the upstream's behaviour, not a bug here.
2. **The pipe must be percent-encoded.** `|` is `%7C`. An unencoded pipe in a URL
   is not the same request, and a shell may treat it as a pipe.

```zsh
# Warehouse import. Full code, pipe as %7C.
curl -sS -o /tmp/wh.json -w 'warehouse HTTP %{http_code}\n' \
  "https://$STAGING_HOST/api/tracking/warehouse-imports/KY4001103376087-2-4-%7Cs"
python3 -m json.tool /tmp/wh.json | head -20

# Package sealing.
curl -sS -o /tmp/seal.json -w 'sealing  HTTP %{http_code}\n' \
  "https://$STAGING_HOST/api/tracking/package-sealings/A1918106"
python3 -m json.tool /tmp/seal.json | head -20

# A keyword that must NOT be accepted: rejected locally, no provider call.
curl -sS -o /dev/null -w 'hostile  HTTP %{http_code} (must be 400)\n' \
  "https://$STAGING_HOST/api/tracking/warehouse-imports/%3Cscript%3E"
```

**Success looks like** `200` with `{"result":"found",…}` when live mode is on;
**`503` with a Vietnamese message when it is off, which is normal here**; and
**`400` for the hostile keyword, always**, because that check runs before any
network call (`docs/STAGING-RUNBOOK.md:897-899`).

**What to record:** the status **and the body's key names**. The two upstream
envelopes **differ** — `warehouse-imports` is a bare object, `package-sealings`
is wrapped in `{"data": {...}}` — and a check that only looks at the status will
not notice if that changed (`docs/STAGING-RUNBOOK.md:901-904`).

**If it fails:** a `404` on a full code means the keyword was truncated or the
pipe was encoded wrongly. A `502` is a provider-side error, distinct from `503`.
A `200` for the hostile keyword is a **security** finding — stop and report it,
because the local guard `sanitise_keyword` should have rejected it before any
call (`backend/app/tracking.py`).

---

## 12. Source-exposure checks — **any 200 is a release blocker**

The deploy publishes an **allow list** (`deploy/published-files.txt`). A previous
revision published the whole repository minus a few names, and everything added
later became public: measured against real nginx, `/package.json`,
`/playwright.config.js`, `/tests/e2e/*.spec.js` and the entire `node_modules/`
tree were all fetchable. `node_modules/` alone is thousands of files, several of
which ship their own metadata, and `package-lock.json` hands over the exact
dependency tree to look up CVEs against.

Run this loop verbatim. It is the loop from `docs/STAGING-RUNBOOK.md` §11.2.

```zsh
for p in /backend/app/config.py /docs/SECURITY.md /.git/config /.env \
         /docker-compose.yml /requirements.txt /README.md /package.json \
         /package-lock.json /playwright.config.js /tests/e2e/helpers.js /node_modules; do
  printf '%-38s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$STAGING_HOST$p")"
done
```

**Success looks like** every one of those twelve lines printing `403` or `404`.
**A `200` is a release blocker** — do not proceed, do not "note it for later".

Then confirm the files that **should** be public still are:

```zsh
for p in / /robots.txt /sitemap.xml /static/js/register.js /static/css/style.css; do
  printf '%-30s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$STAGING_HOST$p")"
done
```

**Success looks like** five lines of `200`.

Guard the same thing statically before you even deploy — this is part of the gate
set in step 17 and it is checked by `tools/check_deploy_exposure.py`:

```zsh
python3 tools/check_deploy_exposure.py
```

**If it fails:** a `200` on any deny-list path means the publish step was run
without `--files-from` (or with a hand-written exclude list). Re-run the `rsync`
from step 2.4 exactly as written, with `-r`, and re-test. Record the finding — it
is a real exposure, not a cosmetic one.

---

## 13. CSS / JS presence check — the `--files-from` trap

`rsync --files-from` **cancels** the `-r` implied by `-a`. Without an explicit
`-r`, the directories are created and their contents are not copied — producing a
site that returns `200` for `/` and `404` for every asset it needs
(`docs/STAGING-RUNBOOK.md:941-945`). **The homepage looking fine in a browser
that has cached assets is exactly how this hides.**

This is the complete published asset list at this SHA (`find static -type f`),
not a sample of two:

```zsh
for p in /static/css/style.css \
         /static/img/apple-touch-icon.png /static/img/favicon-16.png \
         /static/img/favicon-32.png /static/img/favicon.svg \
         /static/img/og-viporder.png \
         /static/js/analytics-attribution.js /static/js/analytics.js \
         /static/js/app.js /static/js/contact-links.js \
         /static/js/register-errors.js /static/js/register-validation.js \
         /static/js/register.js /static/js/tracking-search.js \
         /static/js/tracking.js; do
  printf '%-44s %s\n' "$p" "$(curl -sS -o /dev/null -w '%{http_code}' "https://$STAGING_HOST$p")"
done
```

**Success looks like** fifteen lines of `200`.

**If it fails:** any `404` here means the publish `rsync` ran without `-r`. Re-run
step 2.4 with the flag present, then repeat this loop. Also confirm the server
itself has the files, which separates "not copied" from "not served":

```zsh
ls "$SITE/static/css/style.css" "$SITE/static/js/register.js"
sudo nginx -T 2>/dev/null | grep -n 'root '
```

---

## 14. Browser E2E against staging

### Read this before you plan the test

**The committed browser suite does not test staging.** `tests/e2e/` exists —
eight spec files plus helpers, driven by `@playwright/test` 1.56.1 — and it is
CI-enforced (gate **G12A**, `docs/PROJECT-STATUS.md:68`). But
`playwright.config.js` **hardcodes** `baseURL: http://127.0.0.1:8123` and starts
its own two `webServer` entries (`tools/dev_server.py` plus the backend on
`127.0.0.1:8124`). There is no environment variable to point it at a remote host.
So it proves **the code**, not **your host**. Do not report a green local run as
staging acceptance.

Two things you can honestly do:

### 14.1 The checks a command line cannot make — do these by hand

Do these in a real browser against `https://$STAGING_HOST/`. This is the
procedure from `docs/STAGING-RUNBOOK.md` §11.2:

| # | Action | Expected result |
|---|---|---|
| 1 | Open `https://$STAGING_HOST/` | `200`; page renders; no mixed-content warning in the console |
| 2 | Click every "Đăng nhập" / "Kiểm tra đơn" control | Each opens `https://khachhang.viporder.com.vn`. **Never** a `viporder.com.vn` URL |
| 3 | Submit the form with a valid name, phone `not-a-phone`, password `short`, consent **unticked** | Field-level errors; the request returns **422** and **no** lead is created |
| 4 | Submit twice, quickly, with the same data | **One** lead, not two — the `Idempotency-Key` path |
| 5 | *(optional, writes data)* Submit valid data with consent ticked | **201** if the provider confirmed, **202** if it was unreachable and the lead was kept |
| 6 | Re-submit the same test phone with a **new** idempotency key | **409**, and the response body contains **no** existing customer code |
| 7 | At 360 px width (device toolbar) | Both primary actions reachable with no horizontal scroll |

Row 2 is a **business rule**: the login is a *link*, never a proxy. It is
enforced for the static HTML by `tools/check_site.py`, and the nginx config
states it at `deploy/nginx/viporder.com.vn.conf:13-14` — the portal is a
different host and is never proxied here.

**Success looks like** all seven rows as described. Record the SHA next to the
evidence — `git -C "$SRC" rev-parse HEAD` — because a browser pass with no
recorded revision proves nothing about a revision.

### 14.2 Optionally prove a real browser engine rendered the page

This exercises the page's JavaScript, which `curl` does not. Measured on macOS
with Chrome 154.0.8037.95: `--dump-dom` **writes the rendered DOM but the process
does not exit by itself**, so it must be bounded — and the bound must kill
exactly the PID it started, **never match processes by name**
(`docs/STAGING-RUNBOOK.md:830-851`; the rule behind it is `CLAUDE.md` §16.7).

```zsh
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"   # adjust per OS
PROFILE="$(mktemp -d)"
OUT="$(mktemp)"
"$CHROME" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir="$PROFILE" --virtual-time-budget=6000 \
  --dump-dom "https://$STAGING_HOST/" >"$OUT" 2>/dev/null &
CPID=$!
for _ in $(seq 1 25); do kill -0 "$CPID" 2>/dev/null || break; sleep 1; done
kill -0 "$CPID" 2>/dev/null && kill "$CPID"      # bounded: kill the exact pid
sleep 1; kill -0 "$CPID" 2>/dev/null && kill -9 "$CPID"
wait "$CPID" 2>/dev/null
grep -c "khachhang.viporder.com.vn" "$OUT"
rm -rf "$PROFILE" "$OUT"
```

**Success looks like** a count of `1` or more.

**Do not install Node.js, npm or the Playwright browsers on the staging host**
(`STAGING_HOST_REQUIREMENTS.md` §8). Run these from your own machine.

---

## 15. Log inspection

The unit writes to **files**, not to the journal
(`deploy/systemd/viporder-web.service:91-92`) — so `journalctl` shows unit
lifecycle only, not requests:

```zsh
sudo systemctl status viporder-web --no-pager
sudo journalctl -u viporder-web -n 50 --no-pager      # start/stop/restart only

sudo tail -f /var/log/viporder/app.log                # stdout
sudo tail -f /var/log/viporder/app.err.log            # stderr — errors land here
```

nginx, per the `access_log`/`error_log` directives at
`deploy/nginx/viporder.com.vn.conf:108-109`:

```zsh
sudo tail -f /var/log/nginx/viporder.access.log
sudo tail -f /var/log/nginx/viporder.error.log        # level: warn
```

**Read the request path from the outside in** — this is the order that finds
failures fastest: nginx access log → nginx error log → application log →
database. A `401`/`409`/`422` with a `200` before it is a different problem from
a `502`, and the access log tells you which you have before you read any code
(`docs/STAGING-RUNBOOK.md:1061-1064`).

Watch for the rate-limit rejections this configuration can produce — **they are
expected behaviour, not a bug:**

```zsh
sudo grep -c " 429 " /var/log/nginx/viporder.access.log
```

Two limiters apply: nginx's own zones — `10r/m` on
`location = /api/v1/registrations` and `120r/m` on `^~ /api/`
(`deploy/nginx/viporder.com.vn.conf:25-26,115-131`) — and the application's
in-process limiter (`RATE_LIMIT_ATTEMPTS`). **The application's is the
authority**; nginx's protects the upstream socket
(`deploy/nginx/viporder.com.vn.conf:23-24`).

**Success looks like** `ActiveState=active`; no traceback in `app.err.log`; no
`[emerg]` or `[alert]` line in the nginx error log; a `429` count you can explain
from your own testing above.

**Rotation gap — record it as a known gap, do not discover it later.** **No
`logrotate` configuration is committed.** The unit appends to
`/var/log/viporder/app.log` and `app.err.log` indefinitely
(`deploy/systemd/viporder-web.service:91-92`), and PostgreSQL grows with every
lead (`docs/STAGING-RUNBOOK.md:1087-1091`). Configure rotation and a retention
policy **before** this carries real traffic.

---

## 16. Rollback

### 16.1 Read the rollback target first

```zsh
sudo cat /srv/viporder/RELEASE
```

That is the previous SHA. If the file did not exist, this was the first deploy
and there is nothing to roll back **to** — the honest rollback is to stop the
service:

```zsh
sudo systemctl stop viporder-web
```

### 16.2 Code rollback

```zsh
sudo git -C "$SRC" checkout --detach "$ROLLBACK_SHA"
git -C "$SRC" rev-parse HEAD                    # must equal $ROLLBACK_SHA
git -C "$SRC" status --porcelain                # must print nothing

sudo "$VENV/bin/pip" install -r "$SRC/backend/requirements.txt"
sudo rsync -a --delete "$SRC/backend/" "$APP/"
# Publish ONLY what the site is, by name. `-r` IS REQUIRED: --files-from cancels
# the -r implied by -a, so without it you would deploy a site with no CSS or JS.
sudo rsync -a -r --delete --files-from="$SRC/deploy/published-files.txt" \
  "$SRC/" "$SITE/"
sudo chown -R viporder:viporder "$APP" "$SITE"

sudo systemctl restart viporder-web
sudo systemctl reload nginx
echo "$ROLLBACK_SHA" | sudo tee /srv/viporder/RELEASE

cd "$SRC" && ./deploy/post-deploy-check.sh "https://$STAGING_HOST"
```

**A rollback is a deployment and is verified the same way**
(`deploy/post-deploy-check.sh:173`). **Success looks like** `FAIL=0` and
`DEPLOYMENT VERIFIED` again, plus the step 13 asset loop returning fifteen `200`s
— the rollback republishes `site/`, so it can reintroduce the `-r` trap.

### 16.3 Alembic downgrade — and what it does *not* undo

```zsh
sudo -u viporder env DATABASE_URL="$DB_URL" \
  bash -c "cd '$APP' && '$VENV/bin/alembic' downgrade -1"
```

**Going back one revision is not the same as restoring data.** Reversing a
migration undoes *schema*, and several of these migrations drop columns that hold
real customer data. Head is `0005_live_phone_rule`, and each step down is
cumulative (`docs/STAGING-RUNBOOK.md:991-1013`):

| Command | Reverses | What is actually lost |
|---|---|---|
| `downgrade -1` | `0005` | Nothing. Index-only: drops `uq_leads_live_phone` and recreates the two older indexes |
| `downgrade -2` | `0004` | The `in_flight_at` column — in-flight claim state |
| `downgrade -3` | `0003` | `response_status`, `response_body` — the stored provider responses |
| `downgrade -4` | `0002` | `consent_given_at`, `consent_version`, `request_fingerprint` — **the evidence a customer consented, and the idempotency binding** |
| `downgrade -5` | `0001` | `op.drop_table("leads")` — **every lead** |

**`downgrade -1` is the only one this procedure treats as routine.** Anything
below it destroys data that cannot be regenerated — a registered customer's code
cannot be reissued from the website (`docs/DEPLOYMENT.md:224-227`).

Take a backup **before** any downgrade below `-1`:

```zsh
PSQL_URL="${DB_URL/postgresql+psycopg/postgresql}"
sudo install -d -m 0700 /var/backups/viporder
pg_dump "$PSQL_URL" -Fc \
  -f "/var/backups/viporder/leads-before-downgrade-$(date +%Y%m%dT%H%M%S).dump"
```

Restore from a dump, not from a migration, when the data is what you lost.

**Success looks like** `alembic current` printing the revision you intended, and
step 16.2's check passing.

**If it fails:** never re-run `downgrade` twice hoping it works. Stop, take the
`pg_dump`, and treat it as a data-restoration problem, not a migration problem.

---

## 17. The five gates — run these after any edit to `docs/` or `deploy/`

```zsh
cd "$SRC"
python3 tools/check_site.py
python3 tools/check_nginx_config.py
python3 tools/check_repo_hygiene.py
python3 tools/check_compose.py
python3 tools/check_deploy_exposure.py
```

All five must exit `0`. Their declared scopes matter — read the `scope:` line each
one prints. None of them validates nginx syntax (`nginx -t` does that) and none of
them has ever touched a live host.

---

## Values you must supply

Nothing in this table may be guessed. **If a value is unknown, stop at that
step.**

| Placeholder | What it is | Where the constraint comes from |
|---|---|---|
| `$SHA` | The **40-hex commit** to deploy. Must be the revision CI verified — not a branch name, not a tag | CI checks the PR head SHA: `.github/workflows/ci.yml:31-38` |
| `$STAGING_HOST` | The hostname you will browse | The checked-in config names **exactly** `viporder.com.vn` and `www.viporder.com.vn` (`deploy/nginx/viporder.com.vn.conf:43,62,77`). A different name means the six edits in step 7.4 |
| `$STAGING_IP` | Public IPv4 of the host. Used to test before DNS moves | `STAGING_HOST_REQUIREMENTS.md` §1 #2 |
| `$TLS_CONTACT_EMAIL` | Email registered with Let's Encrypt; receives expiry warnings | certbot requirement |
| `$DB_PASSWORD` | Password for the PostgreSQL role | `deploy/env.production.example:57-58`; set with `\password` in step 4 |
| `$ADMIN_API_TOKEN` | **Optional.** Empty means the admin retry route returns **404** — disabled, not merely unauthorised | `deploy/env.production.example:94-98`; `backend/app/config.py:128` |
| `$TEST_PHONE_9` | 9 digits you own, first digit `2 3 5 7 8 9` | `backend/app/phone.py:15-16,22` |
| `$ROLLBACK_SHA` | The 40-hex commit currently live. Read it **before** deploying | step 16.1 |
| `$REPO_URL` | Git remote | `deploy/systemd/viporder-web.service:15` |
| **SSH access to the host** | Key preferred over a password; account must have `sudo`/root | `STAGING_HOST_REQUIREMENTS.md` §7 |
| **DNS control for the hostname** | Needed before TLS: ACME HTTP-01 requires the name to resolve to the host | `docs/PROJECT-STATUS.md:178` |
| **A decision on where the host lives** | Because `viporder.com.vn`, `www` and `khachhang.viporder.com.vn` all resolve to `103.159.50.70` (measured 2026-10-02, `docs/DNS-CUTOVER.md:121-146`), and the apex certificate does not cover `khachhang`. Staging on a subdomain is covered in step 8. | `docs/DNS-CUTOVER.md` §2.1, §2.3 |

The password and the token must **not** go into the repository, into a chat
message, or into shell history. `\password` at the PostgreSQL prompt does not echo
and does not reach shell history.

### Also not supplied by this document, because it must be chosen

* **The host, its provider, and its control panel.** No provider and no panel is
  named anywhere in this repository, and none has been chosen
  (`STAGING_HOST_REQUIREMENTS.md:21-23`).
* **The firewall.** This document tells you which ports must be open (80, 443)
  and which must **not** be reachable (8000, 5432), and gives you `ss` to verify.
  It deliberately does not name a firewall tool.

---

## What is NOT proven yet

Written this way on purpose. An unticked box is fine; a ticked box without
evidence is not (`docs/GO-LIVE-CHECKLIST.md:3-5`).

1. **No staging host exists and nothing has been deployed.** There is no VPS, no
   IP, no DNS record and no credential in this repository. Every command above is
   a procedure written against `ae6c53a`, not a transcript.
2. **The real KHAIBAO9610 provider contract is absent** (issue #4). `MOCK` is not
   working registration, and this site must not claim it is
   (`docs/PROJECT-STATUS.md`, gate G04 = `BLOCKED_EXTERNAL`).
3. **Docker has never been executed for this project.** If you take the compose
   path instead, `STAGING_HOST_REQUIREMENTS.md` §4 requires you to read further:
   the derived requirements there are `[derived]` from the compose file's syntax,
   never run. This document's systemd path is the exercised one.
4. **More than one uvicorn worker has never been exercised.** The concurrency
   guarantees are verified within one process only
   (`docs/PROJECT-STATUS.md:94`).
5. **The nginx configuration has never run on a real host.** What *is* verified is
   local: config syntax parses, the six security headers are present on `/`, a
   static asset and `robots.txt`, and there is exactly one `Cache-Control` per
   response — all on macOS with nginx 1.31.6. **Not verified:** production-host
   behaviour, TLS/OCSP stapling with a real certificate, HTTP/2 negotiation, and
   the `/api/` proxy path with a backend running (`deploy/nginx/README.md:86-94`).
6. **OCSP stapling may be silently inactive** — `ssl_stapling on` is declared with
   no `resolver` directive (step 8). Read from the config; not measured.
7. **No backup has ever been restored.** A backup that has never been restored is
   not a backup (`docs/DEPLOYMENT.md:224-227`).
8. **No log rotation is committed** (step 15).
9. **HSTS has not been measured end to end on a live host, and the two tiers
   disagree in the configuration.** The nginx snippet withholds
   `includeSubDomains` (`deploy/nginx/viporder-security-headers.conf:45`), but the
   **application** emits `max-age=31536000; includeSubDomains` on its own
   responses (`backend/app/middleware.py:253`), and there is no
   `proxy_hide_header Strict-Transport-Security` anywhere in `deploy/`. nginx
   `add_header` adds to a proxied response rather than replacing it, so the
   configuration implies **two** `Strict-Transport-Security` headers on `/api/*`
   responses. **Nobody has measured that response.** If you intend to rely on
   `includeSubDomains` being absent — and the recoverability argument in
   `docs/PRODUCTION-CUTOVER.md` does — measure it first:
   `curl -sSI "https://$STAGING_HOST/api/v1/health" | grep -ci '^strict-transport-security'`
   and read the values, not just the count. Reported as an open item, not as a
   defect, because it has not been observed.
10. **The publish path has never been executed against real nginx.** The
    `--files-from` allow list is checked statically by
    `tools/check_deploy_exposure.py` and the asset list by step 13, but the
    `rsync` itself has not run on a host.
11. **No screenshots of a deployed staging candidate exist.** The eleven
    screenshots in `docs/visual-acceptance/` were rendered by the CI browser gate
    and approved by the owner on 2026-10-02 — they are of the *repository*, not of
    a deployed host (`docs/PROJECT-STATUS.md:92`).
12. **The committed browser E2E suite does not run against staging** (step 14).
    Pointing it at a remote host requires a config change that has not been made.
