# Phase 116: Extensionless official attachment endpoints

## Finding

The Sichuan Personnel Examination Network serves official position tables at
URLs such as `https://www.scpta.com.cn/front/download-<token>`. These links
have no `.xls`, `.xlsx`, or `.pdf` suffix. The source page and host were
already official and accessible, but the attachment discovery step discarded
the links before the private evidence queue.

## Fix

The attachment contract now supports a source-scoped
`attachment_url_patterns` allowlist. A configured tokenised path is registered
as a candidate without guessing its type; the download stage still validates
the response content type and rejects HTML/unsupported files. Only the
Sichuan source registers `/front/download-`, so arbitrary extensionless links
from other sources remain ignored.

The attachment-purpose classifier also recognises the official wording
“岗位和条件要求一览表” as a position table instead of a generic
announcement attachment.

## Verification

- The server source discovery now finds the Sichuan official announcement.
- The source registry includes both `dkj.sc.gov.cn` and
  `www.scpta.com.cn` for detail and attachment evidence.
- Local Python compilation and registry validation pass.
- A regression test covers an extensionless allowlisted download endpoint.
- The registered 2026 announcement is already closed; no student-facing row
  is activated by this change. Discovered files remain private until download,
  extraction, row-level evidence review, and lifecycle checks succeed.

## Acceptance

This fixes a concrete attachment-ingestion blocker but does not claim a new
current vacancy or 75/100. The next check is to rebuild the server worker with
this parser, discover the three official Sichuan attachments, and verify that
closed rows stay out of the public corpus.
