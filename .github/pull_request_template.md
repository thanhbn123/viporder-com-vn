## Gate / CR

<!-- Which gate does this PR advance? e.g. G02 -->

Gate:
Issue:

## What changed

<!-- Concrete list. No "intended state" — describe what is in the diff. -->

## Verification (evidence, not claims)

<!--
Paste the actual command and its actual result. A reviewer must be able to
re-run it. If something was NOT verified, say so explicitly.
-->

```
$ python tools/check_site.py
$ python tools/check_repo_hygiene.py
$ cd backend && python -m pytest -q
```

## Drift check

- [ ] Re-fetched `origin` before opening this PR
- [ ] `develop` merge-base unchanged, or rebased and re-tested
- [ ] CI ran on this PR's **head SHA** (quoted in the CI log)

## Security checklist

- [ ] No credential, token, key or `.env` value added
- [ ] No customer / lead data added
- [ ] Registration passwords are not persisted, logged or traced
- [ ] New external input is validated server-side
- [ ] New outbound call has a timeout and a failure path that keeps the lead

## Honesty checklist

- [ ] No status claimed as PASS / MERGED / DEPLOYED without measured output
- [ ] Anything not done is listed under "Not done / follow-ups"
- [ ] Documentation matches the tree, not the plan

## Not done / follow-ups

<!-- Explicit gaps. Leave empty only if there genuinely are none. -->
