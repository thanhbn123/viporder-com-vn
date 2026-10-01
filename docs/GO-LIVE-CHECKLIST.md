# Go-live checklist — VIPORDER.COM.VN

Print this. Tick each box **only** with the evidence named next to it. An
unticked box is fine; a ticked box without evidence is not.

Release candidate: `develop` → `main` PR head SHA: `____________________`

---

## A. Release identity

- [ ] Release PR is `develop` → `main` and is **the only** change between the
      verified `main` and the new `main`.
      *Evidence:* `git log --oneline main..develop`
- [ ] Release PR head SHA recorded above and unchanged since CI last ran.
      *Evidence:* CI log line `Verified revision: <sha>`
- [ ] Every required CI job green **on that SHA**.
      *Evidence:* `gh pr checks <n>`
- [ ] No `main`/`develop` commit was made outside a PR.
      *Evidence:* `git log --first-parent main` shows merge commits only

## B. Functional acceptance (staging)

- [ ] Registration happy path returns `201` and the customer code is displayed.
- [ ] Registration with the provider unavailable returns `202`, shows the
      pending message, **and the lead exists in the database**.
      *Evidence:* row count before/after in the lead store
- [ ] Duplicate phone returns `409` and the response body contains **no**
      existing customer code.
- [ ] Double-clicking submit creates exactly **one** lead.
      *Evidence:* two identical requests with the same `Idempotency-Key`
- [ ] Invalid input returns `422` with field-level messages.
- [ ] Every "Đăng nhập / Kiểm tra đơn" control opens
      `https://khachhang.viporder.com.vn`.
- [ ] Mobile: the two primary actions are reachable without horizontal scroll
      at 360 px width.

## C. Security

- [ ] No credential in the repository, in issue/PR text, or in any log.
      *Evidence:* `python3 tools/check_repo_hygiene.py` and the gitleaks CI job
- [ ] Registration password appears in **no** database row, log line, or error
      trace. *Evidence:* the automated `test_no_password_leak` test output
- [ ] Security headers present on the live site.
      *Evidence:* `deploy/post-deploy-check.sh` output
- [ ] `KHAIBAO9610_MODE` on the server is the intended value, and if it is
      `mock`, the website does **not** claim real registration works.
      *Evidence:* `grep KHAIBAO9610_MODE /etc/viporder/viporder.env`
- [ ] `ADMIN_API_TOKEN` set (or deliberately empty so the retry route is 404).

## D. Data

- [ ] Database migrations applied: `alembic upgrade head` clean.
- [ ] A backup runs and **has been restored once** into a scratch database.
      *Evidence:* restore output
- [ ] `.env` on the server is mode `0640`, owned `root:viporder`, and outside
      the web root.

## E. Deployment

- [ ] `deploy/systemd/viporder-web.service` active, `NRestarts=0` after 10 min.
      *Evidence:* `systemctl show viporder-web -p NRestarts -p ActiveState`
- [ ] `deploy/post-deploy-check.sh` exits 0 against the live domain.
- [ ] Rollback SHA recorded: `____________________`
- [ ] TLS certificate valid for > 30 days and auto-renewal timer enabled.
- [ ] Application logs are being written and are rotating.

## F. Honesty

- [ ] `docs/PROJECT-STATUS.md` gate table matches reality.
- [ ] Remaining blockers are listed and are genuinely external.
- [ ] **KHAIBAO9610 integration status is stated truthfully.** If issue #4 is
      still open, the release notes must say registration is MOCK and real
      customer codes are not being issued by this site.
- [ ] Nothing is described as DEPLOYED or PRODUCTION READY that has not been
      measured on the live domain.

---

**Signed off by:** __________________  **Date:** ____________

If any box in section F cannot be ticked, the release is not finished —
regardless of how green everything else is.
