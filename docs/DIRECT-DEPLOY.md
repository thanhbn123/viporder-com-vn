# Direct deploy — VIPORDER.COM.VN

> **Decided 07/10/2026, approved by the owner.** GitHub Actions is **no longer**
> the deployment gate. GitHub keeps three jobs: store the code, store the Git
> history, cut tags.
>
> **Measured state at the time of writing (07/10/2026, 19:0x +0700):**
>
> | | |
> |---|---|
> | Deploy scripts | **PRESENT** — `deploy/`, 75/75 local test cases pass |
> | Local checks | **PASS** — 614 passed · 12 skipped; ruff clean; `node --test` 0 fail |
> | Staging deployed with this suite | **NO** — there is no staging host at all; see §9 |
> | Production | **NOT DEPLOYED**, no host |
>
> `STAGING_HOST_REQUIREMENTS.md` still says *"NOTHING OBTAINED, NOTHING DEPLOYED"*,
> and that is still true. This document describes **how to deploy** and **what has
> been measured**. Where something has not been measured it says so.

---

## 1. The flow

```
LOCAL CODE → LOCAL TEST → VPS STAGING → STAGING VERIFY
           → OWNER ACCEPTANCE → VPS PRODUCTION → PRODUCTION VERIFY
           → GIT PUSH / TAG
```

The five commands the owner needs:

```bash
./deploy/staging.sh                 # local checks → artifact → staging
./deploy/verify.sh staging          # acceptance; ONLY this writes the production pass
./deploy/production.sh              # takes only a release that holds a pass for this SHA
./deploy/verify.sh production       # acceptance, including a probe from the Internet
./deploy/rollback.sh production     # back to the previous release
```

Plus:

```bash
./deploy/backup.sh production            # standalone backup
DEPLOY_DRY_RUN=1 ./deploy/staging.sh     # print the plan, touch no server
```

### Three gates, all fail-closed

| Gate | Refuses | On refusal |
|---|---|---|
| Clean worktree | uncommitted code — code with no SHA | stops; **never** stashes or deletes anything of yours |
| Staging pass for this SHA | a release nobody accepted | stops; a pass for a *different* SHA is refused too |
| Artifact digest | production built from a different source | stops when the digest differs |

A dry run prints `CHƯA LÀM` (not done) for every step it did not perform. This
matters: the first dry run of the sibling project printed `PASS — shipped to the
server` while connecting to nothing, which is exactly the class of fault where the
thing doing the reporting is the thing that is not honest.

---

## 2. Running the checks locally

`deploy/deploy.conf` lists exactly what runs, and it is the same set CI runs —
minus Playwright (§8). Measured on this machine (Mac mini, 07/10/2026):

| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check .` **inside `backend/`** | clean, 65 files |
| `tools/check_repo_hygiene.py`, `check_site.py`, `check_deploy_exposure.py`, `check_compose.py`, `check_nginx_config.py` | all pass |
| `pytest -q` | 614 passed · 12 skipped |
| `node --test tools/js/*.test.js` | 0 fail |

> **`ruff` must run inside `backend/`, not at the repository root.** CI sets
> `working-directory: backend` and the ruff configuration lives in
> `backend/pyproject.toml`. Run `ruff check .` from the root instead and it lints
> `tools/` **without that configuration**: measured 24 "errors" and 12 files
> "needing reformatting" that CI has never seen — every one a false positive.
> A check whose real scope differs from its assumed scope reports confidently
> about the wrong thing.

Docker is **not installed on this machine**, so nothing here has built an image or
run the compose stack. That gap is old and recorded: `docs/PROJECT-STATUS.md`
("the stack has never been run").

---

## 3. Layout on the server

```
<APP_ROOT>/<environment>/
├── releases/<release_id>/     the whole repo tree for one release (+ release.json)
├── current  →  releases/…     the RUNNING release
├── previous →  releases/…     the one before (rollback target)
├── shared/.env                SECRETS — a person puts these there, no script does
├── backups/<stamp>/           database dumps + metadata
└── history.log                deploy ledger, append-only
```

`release_id` is `YYYYMMDD-HHMMSS-<sha7>`, e.g. `20261007-182500-a31df21`.

The release directory is also the **compose context**: `deploy/docker-compose.yml`
mounts the published site files from it, so switching release switches both the
application and the static site, and `deploy/published-files.txt` stays the single
statement of what is published.

Secrets are reached through a symlink `releases/<id>/deploy/.env → shared/.env`, so
changing release never loses configuration and never copies a secret twice.

**One-time work, done by a person, not by a script:** put
`<APP_ROOT>/<env>/shared/.env` in place (template: `deploy/env.production.example`).
`require_shared_env` refuses to continue without it rather than starting an
application with half its configuration.

---

## 4. Staging and production run DIFFERENT stacks — deliberately

| | staging | production |
|---|---|---|
| compose services | `db` + `app` | `db` + `app` + `nginx` |
| exposure | `127.0.0.1:18080` | ports 80/443 |
| TLS | none | yes, certs in `deploy/certs` |

Why: `docker-compose.yml` binds 80 and 443 by name, and the nginx configuration
needs certificates. A staging host shared with another project would collide on
those ports and has no certificate. So staging runs the application alone, behind
nothing.

> **Read the consequence, do not skip it:** the staging pass therefore says
> **nothing** about nginx, TLS, security headers, proxy-level rate limiting, or the
> published-file list. Those are verified in `verify.sh production` — step 12 of
> `production.sh` — and that is where a proxy-level regression would be caught, not
> earlier. `production.sh` prints this warning when it reads the pass.

---

## 5. Backups

`./deploy/backup.sh <env>` writes into `<APP_ROOT>/<env>/backups/<stamp>/`:

| | |
|---|---|
| database | `pg_dump -Fc` + `.sha256`, **and `pg_restore --list` must read it back** |
| persistent data | `shared/var/` if present |
| configuration | **variable NAMES only** (`env-keys.txt`) |
| metadata | running `release.json`, last 50 lines of `history.log`, `compose images` |

Two deliberate choices:

- **Secret values are not backed up here.** Backing up secrets must be a decision
  with its own storage, not a side effect of a nightly job.
- **The dump is read back, not just created.** A corrupt dump is still a file with
  a size, and it would otherwise surface at the worst possible moment.

On the first deploy there is no database yet; the script says so and exits 0
rather than pretending it backed something up.

**Limit that remains:** backups sit on the same host. Losing the host loses both.

---

## 6. Rollback and the database

`./deploy/rollback.sh <env>` points `current` back at `previous`, brings that
release up with compose, health-checks it, and then **confirms `current` really is
the target** — if it cannot confirm, it says the rollback is not finished.

> **This rolls back CODE, not the DATABASE SCHEMA. Deliberately.**
>
> `alembic downgrade` is only safe when that migration was *written* to be
> reversible, and the existence of a `downgrade()` function does not establish
> that. A wrong downgrade loses data, and no rollback repairs that.
>
> So the rule is **backward-compatible migrations**: old code must run against the
> new schema. In practice — add nullable (or defaulted) columns; do not drop or
> rename a column the previous release still reads in the same change; split the
> drop into a later release once nothing uses it.
>
> `production.sh` counts new files in `backend/alembic/versions` against the running
> release and **says so**; `rollback.sh` warns when the target has fewer migrations
> than the running release. Both report rather than act, because that judgement
> cannot be derived from a file count.

---

## 7. GitHub sync

After production has been accepted:

```bash
git push origin HEAD
git tag -a "v<release_id>" -m "release <release_id>" && git push origin --tags
```

**A failed push does not make the deploy a failure** if the running release has
been verified. But report `GITHUB_SYNC_PENDING = YES` and **do not lose local
history** — the branch and tag stay, push again when access returns.

**Secrets:** never commit `.env`; no token, password or private key goes to GitHub.
Secrets live in `shared/.env` **on the server**. No script in `deploy/` reads a
secret back to the workstation or prints one — there are test cases for both.

---

## 8. GitHub Actions — what stays, what moves

Reviewed 07/10/2026. One workflow, `.github/workflows/ci.yml`, 11 jobs. Triggers
were already scoped to `main`/`develop`, so there was no duplicate-run waste to
remove here.

| Job | Class | Decision |
|---|---|---|
| `head` (resolve verified revision) | **A — keep** | every other job checks out the SHA it resolves |
| `repo-hygiene` | **A — keep** | cheap |
| `secret-scan` (gitleaks) | **A — keep** | catches what cannot be caught after a push |
| `site-checks` (HTML, links, a11y, SEO) | **A — keep** | cheap, pure python |
| `js-tests` (`node --test`) | **A — keep** | seconds |
| `backend-tests` (**matrix 3.11 + 3.12**) | **A — keep BOTH** | see note below |
| `backend-tests-postgres` | **A — keep** | the production data path |
| `backend-lint` (ruff) | **A — keep** | cheap |
| `backend-security` (bandit) | **A — keep** | cheap |
| `dependency-audit` (pip-audit) | **A — keep** | cheap |
| `browser-e2e` (npm ci + Playwright chromium) | **B — manual** | heaviest job; `if: github.event_name == 'workflow_dispatch'` |

**Why the 3.11 + 3.12 matrix stays even though it is two jobs.** It is tempting to
cut 3.11, and the brief does ask for large matrices to go. But `docs/DEPLOYMENT.md:46`
lists **"Python 3.11 or 3.12"** as supported, and `docs/STAGING-RUNBOOK.md` tells the
operator 3.11 is acceptable on the host. Dropping 3.11 from CI would quietly remove
the only evidence behind a promise the documentation still makes. The matrix is also
cheap: no service containers, pip cached. If the owner decides production is 3.12
only, change the documents first and the matrix second.

**No workflow is deleted. No workflow is disabled.** `browser-e2e` runs any time
from Actions → CI → Run workflow; remove the `if:` line to restore it permanently.
Its coverage moves to `npm run test:e2e` locally and to `deploy/verify.sh`, which
measures the running release — including that `/package.json`,
`/playwright.config.js`, `/backend/app/main.py` and `/deploy/docker-compose.yml`
return 404. That last check exists because those paths once returned **200** over
HTTPS (`deploy/published-files.txt` records the measurement), and CI cannot test it
at all.

---

## 9. Why no staging deploy happened in this round

Three facts, each measured rather than assumed:

1. **This project has no staging host.** Not "the credentials are missing" — no
   host, no IP, no DNS record has been obtained. `STAGING_HOST_REQUIREMENTS.md`
   states it, and nothing in the repository contradicts it.
2. **This machine has no Docker** (`command -v docker` is empty), so the compose
   stack could not be built or run here even against a local host.
3. **This machine has no staging SSH key** (`ls ~/.ssh` shows no `*staging*`).

So `deploy/deploy.conf` leaves every host field empty, and every script stops at
`require_host` with a message naming the file to fill in. Empty is the honest
value; a guessed host would turn work not done into work that looks done.

What unblocks a real staging deploy:

| Needed | Who |
|---|---|
| a staging host (see `STAGING_HOST_REQUIREMENTS.md`) | owner |
| Docker + compose on that host | provisioning |
| an SSH key for it, on the machine that will run the deploy | owner |
| `deploy/deploy.local.conf` filled in | one edit, see the example file |
| `shared/.env` on the host | a person, once |

---

## 10. Troubleshooting

| Symptom | Look here first |
|---|---|
| `cây làm việc KHÔNG sạch` | `git status`. The script does not stash for you, deliberately |
| a local check fails | run that one command alone for full output; check `ruff` runs in `backend/` |
| `require_host` stops | `deploy/deploy.local.conf` — see §9 |
| `thiếu shared/.env` | it belongs on the **server**, from `deploy/env.production.example` |
| health never reaches 200 | `docker compose logs --tail 60 app`; `RestartCount > 0` means it is crashing and being restarted, not running |
| `không có phiếu staging PASS nào cho SHA` | run `verify.sh staging` on that exact commit |
| `mã băm gói LỆCH` | HEAD moved since acceptance — stage again |
| `pg_restore --list KHÔNG đọc được` | the dump is corrupt. **Do not deploy** |

When a deploy fails: **do not edit production directly.** Roll back, fix locally,
test, stage, deploy again.
