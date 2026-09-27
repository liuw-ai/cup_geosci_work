# Delivery Notes

## 版本

- 分支：`phase/56-sinopec-live-capture`
- 回退标签：`phase-56-review`

## 验收命令

```bash
python -m pytest -q
python -m job_hub.cli sinopec-capture \
  --path data/verified/sinopec-geoscience-20260928.json \
  --require-complete --require-scan-complete
docker compose -f docker-compose.yml -f docker-compose.browser.yml config --quiet
```
