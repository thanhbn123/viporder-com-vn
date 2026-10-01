# Deployment — VIPORDER.COM.VN

> **Status: configuration prepared, NOTHING DEPLOYED.**
> There is no VPS address, no DNS access and no credentials in this repository.
> This document describes the *target* architecture and the exact steps, and
> every step is written so it can be executed and verified by someone with
> access. It is not a claim that any of it has been run.

---

## 1. Target architecture

```
Internet
  │
  ├─ viporder.com.vn ─────────► Nginx (TLS, headers, rate limit)
  │                                ├─ /            static site  /srv/viporder/site
  │                                └─ /api/        → 127.0.0.1:8000 (uvicorn)
  │                                                    ├─ lead store (PostgreSQL)
  │                                                    └─ KHAIBAO9610 adapter (outbound)
  │
  └─ khachhang.viporder.com.vn ► the EXISTING legacy portal
                                  (NOT served, NOT proxied, NOT modified here)
```

Registration path:

```
browser → viporder.com.vn/api/v1/registrations
        → validate → persist lead (PENDING)
        → KHAIBAO9610 adapter
            → success : REGISTERED + external customer code
            → failure : FAILED / PENDING, lead RETAINED
```

Login path: a link, not a proxy. `viporder.com.vn` never handles customer
credentials; it sends the browser to `https://khachhang.viporder.com.vn`.

---

## 2. Prerequisites

| Item | Value |
|---|---|
| OS | Debian 12 / Ubuntu 22.04+ |
| Python | 3.11 or 3.12 |
| Database | PostgreSQL 14+ (SQLite is acceptable for staging only) |
| Web server | Nginx 1.20+ |
| TLS | Let's Encrypt via certbot |
| DNS | `viporder.com.vn` and `www.viporder.com.vn` → the server |

**Blocked until the owner supplies:** the VPS address, SSH access, and DNS
control. See `docs/PROJECT-STATUS.md` §5.

---

## 3. Filesystem layout

```
/srv/viporder/
  app/            backend application (from this repository's backend/)
  site/           static site (from this repository's index.html + static/)
  venv/           Python virtualenv
/var/lib/viporder/    SQLite database (when not using PostgreSQL)
/var/log/viporder/    application logs
/etc/viporder/viporder.env    secrets, mode 0640 root:viporder
```

---

## 4. First deployment (systemd path)

Every command is meant to be run **on the server**, from a checkout at a
**known, tracked commit**. Record that SHA — it is the deployment's identity.

### 4.1 Record the release

```bash
RELEASE_SHA=<sha from the merged release PR>
git fetch --all
git checkout "$RELEASE_SHA"
echo "$RELEASE_SHA" > /srv/viporder/RELEASE
```

### 4.2 Service account and directories

```bash
useradd --system --home /srv/viporder --shell /usr/sbin/nologin viporder
install -d -o viporder -g viporder /srv/viporder/{app,site}
install -d -o viporder -g viporder /var/lib/viporder /var/log/viporder
install -d -m 0750 -o root -g viporder /etc/viporder
```

### 4.3 Application

```bash
python3 -m venv /srv/viporder/venv
/srv/viporder/venv/bin/pip install -r backend/requirements.txt
rsync -a --delete backend/ /srv/viporder/app/
chown -R viporder:viporder /srv/viporder/app
```

### 4.4 Static site

```bash
rsync -a --delete \
  --exclude '.git' --exclude 'backend' --exclude 'deploy' \
  --exclude 'tools' --exclude 'docs' --exclude 'var' \
  ./ /srv/viporder/site/
chown -R viporder:viporder /srv/viporder/site
```

### 4.5 Secrets

```bash
install -m 0640 -o root -g viporder \
  deploy/env.production.example /etc/viporder/viporder.env
$EDITOR /etc/viporder/viporder.env
#   DATABASE_URL, SECRET_KEY, ADMIN_API_TOKEN
#   KHAIBAO9610_MODE stays "mock" until issue #4 is resolved
chmod 0640 /etc/viporder/viporder.env
```

### 4.6 Database

```bash
sudo -u viporder /srv/viporder/venv/bin/alembic upgrade head
```

### 4.7 Service

```bash
cp deploy/systemd/viporder-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now viporder-web
systemctl status viporder-web --no-pager
```

### 4.8 Nginx and TLS

```bash
cp deploy/nginx/proxy_params_viporder /etc/nginx/proxy_params_viporder
install -d /etc/nginx/upstreams
# systemd host: the app is on loopback. (Compose uses upstream-compose.conf.)
cp deploy/nginx/upstream-systemd.conf /etc/nginx/upstreams/viporder-upstream.conf
cp deploy/nginx/viporder.com.vn.conf /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/viporder.com.vn.conf /etc/nginx/sites-enabled/
nginx -t
certbot --nginx -d viporder.com.vn -d www.viporder.com.vn
systemctl reload nginx
```

### 4.9 Post-deployment verification

Do not report success from `systemctl status` alone — that only says a process
exists. Run the external check:

```bash
./deploy/post-deploy-check.sh https://viporder.com.vn
```

It verifies over the public internet: health endpoint, TLS, security headers,
the customer-portal link target, `robots.txt`, `sitemap.xml`, and that a
registration attempt is validated (not that it creates a real customer).

---

## 5. Redeployment

```bash
RELEASE_SHA=<new tracked sha>
git fetch --all && git checkout "$RELEASE_SHA"
/srv/viporder/venv/bin/pip install -r backend/requirements.txt
rsync -a --delete backend/ /srv/viporder/app/
sudo -u viporder /srv/viporder/venv/bin/alembic upgrade head
rsync -a --delete --exclude '.git' --exclude 'backend' --exclude 'deploy' \
  --exclude 'tools' --exclude 'docs' --exclude 'var' ./ /srv/viporder/site/
chown -R viporder:viporder /srv/viporder/app /srv/viporder/site
systemctl restart viporder-web
systemctl reload nginx
echo "$RELEASE_SHA" > /srv/viporder/RELEASE
./deploy/post-deploy-check.sh https://viporder.com.vn
```

**Never edit files on the server by hand.** If a hotfix is needed, it goes
through a branch and a PR, and the server gets the tracked SHA.

---

## 6. Rollback

The previous SHA is always known (`/srv/viporder/RELEASE` before the deploy).

```bash
git checkout <previous_sha>
rsync -a --delete backend/ /srv/viporder/app/
rsync -a --delete --exclude '.git' --exclude 'backend' --exclude 'deploy' \
  --exclude 'tools' --exclude 'docs' --exclude 'var' ./ /srv/viporder/site/
systemctl restart viporder-web
echo "<previous_sha>" > /srv/viporder/RELEASE
./deploy/post-deploy-check.sh https://viporder.com.vn
```

**Database migrations are the one thing a code rollback does not undo.** Keep
every migration backward compatible with the previous release (additive
columns, no destructive changes in the same release), and never drop a column
in the release that stops using it.

---

## 7. Backups

| What | How | Frequency |
|---|---|---|
| Lead database | `pg_dump` (or `.backup` for SQLite) to off-host storage | hourly, 30-day retention |
| `/etc/viporder/viporder.env` | encrypted, off-host | on change |
| Nginx + systemd units | in this repository | on change |

The lead store is the only irreplaceable data on this server: a registered
customer's code cannot be regenerated from the website. **A backup that has
never been restored is not a backup** — run a restore drill at least once
before going live.

---

## 8. Container alternative

`deploy/Dockerfile` and `deploy/docker-compose.yml` provide the same stack with
PostgreSQL and Nginx in containers. Use one path or the other. The compose file
does not publish the database port, and puts the app behind Nginx exactly as the
systemd path does.

---

## 9. What must NOT be deployed

* `KHAIBAO9610_MODE=http` without the verified contract (issue #4) — this would
  send real customer data to an unverified endpoint.
* Any `.env` value committed to the repository.
* The `var/` directory, any `*.db` file, or any lead export.
* Code from an uncommitted working tree.
