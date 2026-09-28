# Phase 71 Quality Report

## Local verification

```text
python -m pytest -q: 352 passed
docker compose -f docker-compose.browser.yml config --quiet: passed
```

The new regression test checks that both browser workers use the dedicated
browser image and that the Compose file no longer depends on a writable wheel
cache. The existing full suite remains green.

## Production acceptance

Production deployment is pending because the current SSH key is no longer
accepted by `81.70.62.174` (`Permission denied (publickey,password)`). The
browser-image change is therefore not reported as deployed until the server
owner restores the deploy key or supplies an authorized account.

## Scope boundary

This phase removes one engineering blocker. It does not inflate the job count,
does not import historical civil-service rows, and does not treat a source
failure as a successful scan. The 75/100 business threshold still requires
real production capture, broader domestic official sources, and a working
public DNS/HTTPS name.
