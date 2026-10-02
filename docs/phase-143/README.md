# Phase 143: CGS announcement-window deadline parsing

## Purpose

中国地质调查局部分官方公告使用“报名时间。公告发布之日起至 YYYY 年 M 月 D 日 HH：MM”格式。此前该格式未被识别，导致已过期的候选岗位停留在 `needs_review`，生命周期清理无法准确执行。

## Change

- `job_hub/matching.py` now recognizes this format only when it is anchored to an explicit application marker (`报名`、`申请`、`网申`、`投递`或`应聘`).
- The extracted deadline remains the date portion; the time-of-day is intentionally not used for the date-level expiry gate.
- No source rows, job rows, or attachments are deleted.

## Verification

- Matching and attachment tests: `50 passed`
- Full suite: `519 passed, 2 skipped`
- `compileall` and `git diff --check`: passed

## Production action

Build `Dockerfile.phase143-overlay` from the validated `cupb-geoscience-job-hub:latest` image, restart only `web` and `worker`, retain the existing SQLite volume, then refresh CGS artifacts 1792 and 1794 and run expiry cleanup. This phase must not publish historical CGS rows; it should only move rows with the now-proven 2026-03-04 deadline to `expired`.
