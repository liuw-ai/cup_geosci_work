"""Controlled processing for official recruitment announcement attachments.

This module deliberately stops at a private review queue.  A PDF/XLSX row is
not a public job until an administrator has inspected the official announcement
and explicitly verified the candidate.  Network access is limited to a
registered source, its declared attachment hosts, and robots-permitted public
URLs; no login, CAPTCHA, proxy or browser fingerprint workaround is attempted.
"""

from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import mimetypes
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from job_hub.config import Settings
from job_hub.contracts import is_http_url
from job_hub.db import Database
from job_hub.government_artifacts import government_artifact_processing_block_reason
from job_hub.matching import (
    extract_deadline,
    extract_degree_levels,
    extract_major_tags,
    extract_published_date,
    score_relevance,
)
from job_hub.profiles import (
    PUBLICATION_STUDENT_ELIGIBLE,
    PUBLICATION_UNRESTRICTED_ELIGIBLE,
    evaluate_student_publication,
)
from job_hub.transport import (
    RequestPolicy,
    configure_session,
    create_session,
    request_exception_types,
)


USER_AGENT = "cupb-geoscience-job-hub-attachment-fetcher/0.1 (+official-public-source)"
PARSER_VERSION = "attachments-v3"
REQUEST_ERRORS = request_exception_types()

SUPPORTED_SUFFIXES = {
    ".pdf",
    ".csv",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".docx",
}
HTML_MARKERS = (b"<html", b"<!doctype html", b"<head", b"<script")

# Recruitment notices frequently attach application forms and examination
# workflow documents beside the real position table. Retain those files as
# official evidence, but keep them out of the position-review queue.
NON_POSITION_ATTACHMENT_MARKERS = (
    "报名表", "报名登记表", "应聘登记表", "资格审查", "资格复审", "准考证",
    "笔试", "面试", "成绩", "体检", "考察", "拟聘", "拟录用", "录用名单",
    "聘用名单", "录用公示", "放弃", "递补", "取消招聘", "核减岗位",
    "诚信承诺", "承诺书", "报考指南", "操作手册",
)
APPLICATION_ATTACHMENT_MARKERS = (
    "报名表", "报名登记表", "应聘登记表", "诚信承诺", "承诺书",
)
POSITION_TABLE_MARKERS = (
    "岗位表", "职位表", "岗位计划", "招聘计划", "需求计划", "职位一览", "岗位一览",
    "岗位和条件", "岗位条件", "招聘工作人员岗位",
)


def build_attachment_field_evidence(
    *,
    title: str,
    major: str | None,
    degree: str | None,
    location: str | None,
    artifact: dict[str, object],
    row: dict[str, object],
    row_text: str,
    extra_evidence: dict[str, object] | None = None,
) -> dict[str, str]:
    """Normalize one official table row to the shared publication schema.

    ``extra_evidence`` contains parser or reviewer fields such as recruitment
    count and position code.  Preserve them when a private candidate becomes
    public; dropping them here made the student page lose facts that were
    already extracted from the official row.
    """
    evidence: dict[str, str] = {
        "evidence_scope": "official_attachment_row",
        "岗位": title,
        "专业范围": str(major or "").strip(),
        "学历要求": str(degree or "").strip(),
        "table_row": f"{row.get('sheet_name')}!{row.get('row_number')}",
        "artifact_url": str(artifact.get("artifact_url") or "").strip(),
        "row_text": row_text[:2000],
    }
    if location:
        evidence["工作地点"] = location
    for key, value in (extra_evidence or {}).items():
        key_text = str(key).strip()
        value_text = str(value or "").strip()
        if (
            key_text
            and value_text
            and key_text not in {"evidence_scope", "岗位", "专业范围", "学历要求", "table_row", "artifact_url", "row_text"}
        ):
            evidence[key_text] = value_text
    return {key: value for key, value in evidence.items() if value}


class AttachmentProcessingError(RuntimeError):
    """A compliant attachment could not be downloaded or parsed."""


class AttachmentSkipped(AttachmentProcessingError):
    """The source is public but cannot be processed under current policy."""


@dataclass(frozen=True)
class AttachmentResult:
    artifact_id: int
    status: str
    bytes_downloaded: int = 0
    rows_extracted: int = 0
    candidates_created: int = 0
    candidates_rejected: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "status": self.status,
            "bytes_downloaded": self.bytes_downloaded,
            "rows_extracted": self.rows_extracted,
            "candidates_created": self.candidates_created,
            "candidates_rejected": self.candidates_rejected,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AttachmentQueueResult:
    created: int
    rejected: int
    detail: str


class OfficialAttachmentProcessor:
    """Download and parse one registered official attachment at a time."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        self.session = session or create_session(
            settings.http_transport_mode,
            headers=headers,
            client=settings.http_client,
        )
        if session is not None:
            configure_session(session, settings.http_transport_mode, headers=headers)
        self.request_policy = RequestPolicy(
            self.session,
            timeout=settings.request_timeout_seconds,
            retries=settings.http_retry_attempts,
            backoff_seconds=settings.http_backoff_seconds,
            max_backoff_seconds=settings.http_max_backoff_seconds,
            jitter_seconds=settings.http_jitter_seconds,
            transport_mode=settings.http_transport_mode,
        )
        self._robots: dict[str, RobotFileParser] = {}

    def discover_from_page(
        self,
        source_id: str,
        parent_url: str,
        *,
        html: str | None = None,
    ) -> list[dict[str, object]]:
        """Register file links from one approved public announcement page.

        Discovery never downloads an attachment. It only reads the page (or
        one public GET when ``html`` is omitted), checks the source host and
        robots policy, and places metadata in the private attachment ledger.
        """
        source = self.database.get_source(source_id)
        if source is None:
            raise ValueError("Source is not registered")
        if not is_http_url(parent_url):
            raise ValueError("parent_url must be a complete HTTP(S) URL")
        self._assert_official_hosts(source, parent_url, parent_url)
        self._robots_allowed(parent_url)
        if html is None:
            try:
                response = self.request_policy.request(
                    "GET",
                    parent_url,
                    timeout=self.settings.request_timeout_seconds,
                    allow_redirects=True,
                )
            except REQUEST_ERRORS as error:
                raise AttachmentProcessingError(
                    f"Announcement page request failed: {error}"
                ) from error
            if response.status_code in {401, 403, 407, 429}:
                raise AttachmentSkipped(
                    f"Announcement page access is restricted (HTTP {response.status_code})"
                )
            if response.status_code >= 400:
                raise AttachmentProcessingError(
                    f"Announcement page returned HTTP {response.status_code}"
                )
            if len(response.content) > self.settings.attachment_discovery_max_bytes:
                raise AttachmentSkipped("Announcement page exceeds the discovery size limit")
            self._assert_official_hosts(source, parent_url, response.url)
            self._robots_allowed(response.url)
            encoding = response.encoding
            if (
                not encoding
                or encoding.lower() in {"iso-8859-1", "ascii"}
            ) and response.apparent_encoding:
                encoding = response.apparent_encoding
            html = response.content.decode(encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        page_text = " ".join(soup.get_text(" ", strip=True).split())
        # Some government CMS pages place the announcement body inside a
        # script/JSON renderer.  BeautifulSoup intentionally excludes script
        # text from ``get_text``; retain a tag-stripped raw fallback so dates
        # and application windows are not silently lost from the artifact
        # lifecycle metadata.
        raw_visible_fallback = re.sub(r"<[^>]+>", " ", html_lib.unescape(html))
        page_text = " ".join(f"{page_text} {raw_visible_fallback}".split())
        page_metadata = {
            "employer": str(source.get("publisher") or ""),
            "category": str(source.get("category") or ""),
            "published_date": extract_published_date(page_text),
            "deadline_date": extract_deadline(page_text),
            "official_notice_title": (
                soup.title.get_text(" ", strip=True) if soup.title else ""
            ),
        }
        discovered: list[dict[str, object]] = []
        seen: set[str] = set()
        from urllib.parse import urljoin

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            artifact_url = urljoin(parent_url, href)
            if artifact_url in seen or not is_http_url(artifact_url):
                continue
            label = " ".join(anchor.get_text(" ", strip=True).split())
            suffix = Path(urlparse(artifact_url).path).suffix.lower()
            # Some official recruitment portals expose downloads through
            # tokenised endpoints (for example ``/front/download-<token>``)
            # without a filename extension.  A source may explicitly
            # register such a path pattern; the response content type is
            # still checked during the later download step.
            configured_patterns = source.get("config", {}).get(
                "attachment_url_patterns", []
            )
            tokenised_attachment = any(
                re.search(str(pattern), artifact_url, re.IGNORECASE)
                for pattern in configured_patterns
                if str(pattern).strip()
            )
            if suffix not in SUPPORTED_SUFFIXES and not tokenised_attachment:
                continue
            try:
                self._assert_official_hosts(source, parent_url, artifact_url)
                self._robots_allowed(artifact_url)
            except AttachmentSkipped:
                continue
            seen.add(artifact_url)
            kind, intent = self._discovered_artifact_kind(label, artifact_url)
            discovered.append(
                self.database.upsert_source_artifact(
                    {
                        "source_id": source_id,
                        "parent_url": parent_url,
                        "artifact_url": artifact_url,
                        "artifact_kind": kind,
                        "media_type": mimetypes.guess_type(artifact_url)[0],
                        "metadata": {
                            "display_name": label or Path(urlparse(artifact_url).path).name,
                            "discovered_from": parent_url,
                            "discovered_at": _utc_now(),
                            "discovery_only": True,
                            "attachment_intent": intent,
                            **page_metadata,
                        },
                    }
                )
            )
        return discovered

    def process(
        self,
        artifact_id: int,
        *,
        force_download: bool = False,
        extract: bool = True,
    ) -> AttachmentResult:
        """Download, hash, parse and queue candidates for one attachment."""
        artifact = self.database.get_source_artifact(artifact_id)
        if artifact is None:
            raise ValueError("Source artifact does not exist")
        manifest_block_reason = government_artifact_processing_block_reason(
            artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else None
        )
        if manifest_block_reason:
            # ``force_download`` deliberately cannot override manifest policy.
            # It only controls a redownload for an otherwise eligible artifact.
            self.database.update_source_artifact_processing(
                artifact_id,
                extraction_status="skipped",
                metadata_updates={
                    "processing_skip_reason": manifest_block_reason,
                    "processing_skipped_at": _utc_now(),
                },
            )
            return AttachmentResult(artifact_id, "skipped", detail=manifest_block_reason)
        try:
            if (
                artifact.get("extraction_status") in {"downloaded", "extracted"}
                and artifact.get("storage_path")
                and not force_download
            ):
                bytes_downloaded = self._stored_size(artifact)
            else:
                artifact, bytes_downloaded = self._download(artifact)
            if not extract:
                return AttachmentResult(
                    artifact_id=artifact_id,
                    status=str(artifact["extraction_status"]),
                    bytes_downloaded=bytes_downloaded,
                    detail="downloaded; extraction was not requested",
                )
            extracted = self._extract(artifact)
            rows = self.database.upsert_source_artifact_rows(
                artifact_id,
                extracted["rows"],
            )
            updated = self.database.update_source_artifact_processing(
                artifact_id,
                extraction_status="extracted",
                parser_version=PARSER_VERSION,
                metadata_updates=extracted["metadata"],
            )
            queue_result = self._reconcile_candidate_queue(updated, rows)
            return AttachmentResult(
                artifact_id=artifact_id,
                status="extracted",
                bytes_downloaded=bytes_downloaded,
                rows_extracted=len(rows),
                candidates_created=queue_result.created,
                candidates_rejected=queue_result.rejected,
                detail=_append_detail(
                    str(extracted["metadata"].get("extraction_note") or ""),
                    queue_result.detail,
                ),
            )
        except AttachmentSkipped as error:
            self._record_failure(artifact_id, "skipped", str(error))
            return AttachmentResult(artifact_id, "skipped", detail=str(error))
        except Exception as error:
            self._record_failure(artifact_id, "failed", str(error))
            raise AttachmentProcessingError(str(error)) from error

    def reconcile_candidates(self, artifact_id: int) -> AttachmentResult:
        """Reapply candidate gates to an extracted attachment without downloading it."""
        artifact = self.database.get_source_artifact(artifact_id)
        if artifact is None:
            raise ValueError("Source artifact does not exist")
        if str(artifact.get("extraction_status")) != "extracted":
            raise ValueError("Only extracted source artifacts can be reconciled")
        rows = self.database.list_source_artifact_rows(artifact_id, limit=500)
        queue_result = self._reconcile_candidate_queue(artifact, rows)
        return AttachmentResult(
            artifact_id=artifact_id,
            status="reconciled",
            rows_extracted=len(rows),
            candidates_created=queue_result.created,
            candidates_rejected=queue_result.rejected,
            detail=queue_result.detail,
        )

    def _download(
        self,
        artifact: dict[str, object],
    ) -> tuple[dict[str, object], int]:
        source = self.database.get_source(str(artifact["source_id"]))
        if source is None:
            raise AttachmentProcessingError("Attachment source is no longer registered")
        url = str(artifact["artifact_url"])
        parent_url = str(artifact["parent_url"])
        if not is_http_url(url) or not is_http_url(parent_url):
            raise AttachmentProcessingError("Attachment URLs must be complete HTTP(S) URLs")
        self._assert_official_hosts(source, parent_url, url)
        self._robots_allowed(parent_url)
        self._robots_allowed(url)
        try:
            response = self.request_policy.request(
                "GET",
                url,
                stream=True,
                allow_redirects=True,
                timeout=self.settings.request_timeout_seconds,
            )
        except REQUEST_ERRORS as error:
            raise AttachmentProcessingError(f"Attachment request failed: {error}") from error
        try:
            if response.status_code in {401, 403, 407, 429}:
                raise AttachmentSkipped(
                    f"Attachment access is restricted (HTTP {response.status_code}); no bypass attempted"
                )
            if response.status_code >= 400:
                raise AttachmentProcessingError(
                    f"Attachment request returned HTTP {response.status_code}"
                )
            self._assert_official_hosts(source, parent_url, response.url)
            self._robots_allowed(response.url)
            content_type = self._content_type(response.headers.get("Content-Type"))
            suffix = self._suffix(response.url, content_type)
            extensionless_allowlisted = self._extensionless_endpoint_allowed(
                source, response.url
            )
            if suffix not in SUPPORTED_SUFFIXES and not extensionless_allowlisted:
                raise AttachmentSkipped(
                    f"Unsupported attachment type: {content_type or 'unknown'}"
                )
            declared_length = response.headers.get("Content-Length")
            if declared_length and int(declared_length) > self.settings.attachment_max_bytes:
                raise AttachmentSkipped("Attachment exceeds the configured size limit")
            root = self.settings.managed_artifact_dir()
            root.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=root)
            total = 0
            digest = hashlib.sha256()
            try:
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self.settings.attachment_max_bytes:
                            raise AttachmentSkipped("Attachment exceeds the configured size limit")
                        if total == len(chunk) and self._looks_like_html(chunk):
                            raise AttachmentSkipped(
                                "Official endpoint returned an HTML/login page instead of a file"
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                hexdigest = digest.hexdigest()
                if suffix not in SUPPORTED_SUFFIXES:
                    suffix = self._sniff_suffix(Path(temporary_name))
                    if suffix not in SUPPORTED_SUFFIXES:
                        raise AttachmentSkipped(
                            "Allowlisted extensionless endpoint returned an unknown file format"
                        )
                relative_path = Path("sha256") / hexdigest[:2] / f"{hexdigest}{suffix}"
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    Path(temporary_name).unlink(missing_ok=True)
                else:
                    Path(temporary_name).replace(destination)
            except Exception:
                Path(temporary_name).unlink(missing_ok=True)
                raise
        finally:
            response.close()
        updated = self.database.update_source_artifact_processing(
            int(artifact["id"]),
            extraction_status="downloaded",
            media_type=content_type or str(artifact.get("media_type") or "") or None,
            content_sha256=hexdigest,
            storage_path=relative_path.as_posix(),
            parser_version=PARSER_VERSION,
            metadata_updates={
                "downloaded_url": response.url,
                "downloaded_bytes": total,
                "downloaded_at": _utc_now(),
                "content_type": content_type,
                "suffix": suffix,
            },
        )
        return updated, total

    def _extract(self, artifact: dict[str, object]) -> dict[str, object]:
        path = self._artifact_path(artifact)
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix == ".xls":
            return self._extract_legacy_workbook(path)
        if suffix in {".xlsx", ".xlsm"}:
            return self._extract_workbook(path)
        if suffix == ".csv":
            return self._extract_csv(path)
        if suffix == ".docx":
            return self._extract_docx(path)
        raise AttachmentSkipped(f"No parser is registered for {suffix}")

    def _extract_pdf(self, path: Path) -> dict[str, object]:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise AttachmentSkipped("PDF parser pypdf is not installed") from error
        try:
            reader = PdfReader(str(path))
            page_texts: list[str] = []
            page_modes: list[str] = []
            for page in reader.pages:
                try:
                    # Position-aware extraction keeps the visual columns of
                    # government position tables together.  Without it,
                    # pypdf emits one token per line and the position code,
                    # title, degree and major cannot be tied to one row.
                    extracted = page.extract_text(extraction_mode="layout") or ""
                    page_modes.append("layout")
                except (TypeError, ValueError):
                    # Older pypdf releases do not expose extraction_mode.
                    extracted = page.extract_text() or ""
                    page_modes.append("text")
                page_texts.append(extracted.strip())
        except Exception as error:
            raise AttachmentProcessingError(f"PDF text extraction failed: {error}") from error
        text = "\n\n".join(page_texts).strip()
        ocr_used = False
        extraction_note = "text layer extracted"
        if not text:
            if not self.settings.attachment_ocr_enabled:
                extraction_note = "PDF has no text layer; OCR is disabled"
            else:
                text, ocr_note = self._ocr_pdf(path, min(len(reader.pages), self.settings.attachment_ocr_max_pages))
                ocr_used = bool(text)
                extraction_note = ocr_note
        text = text[: self.settings.attachment_max_text_characters]
        text_storage_path = self._write_text_sidecar(path, text)
        rows = self._rows_from_pdf_pages(page_texts if not ocr_used else [text])
        if ocr_used:
            rows = [dict(row, extraction_confidence="low") for row in rows]
        if ocr_used:
            extraction_mode = "ocr"
        elif page_modes and all(mode == "layout" for mode in page_modes):
            extraction_mode = "layout"
        elif "layout" in page_modes:
            extraction_mode = "layout_with_text_fallback"
        else:
            extraction_mode = "text"
        return {
            "rows": rows[: self.settings.attachment_max_rows],
            "metadata": {
                "parser": "pypdf",
                "page_count": len(reader.pages),
                "text_characters": len(text),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "extraction_mode": extraction_mode,
                "extraction_note": extraction_note,
                "ocr_enabled": self.settings.attachment_ocr_enabled,
                "text_storage_path": text_storage_path,
            },
        }

    def _extract_workbook(self, path: Path) -> dict[str, object]:
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise AttachmentSkipped("Excel parser openpyxl is not installed") from error
        try:
            workbook_input = (
                io.BytesIO(path.read_bytes())
                if path.suffix.lower() == ".xls"
                else path
            )
            workbook = load_workbook(
                filename=workbook_input,
                read_only=True,
                data_only=True,
            )
        except Exception as error:
            raise AttachmentProcessingError(f"Excel extraction failed: {error}") from error
        rows: list[dict[str, object]] = []
        sheet_count = 0
        try:
            for worksheet in workbook.worksheets:
                sheet_count += 1
                raw_rows: list[list[str]] = []
                for values in worksheet.iter_rows(values_only=True):
                    cells = [self._cell_text(value) for value in values]
                    if any(cells):
                        raw_rows.append(cells)
                    if len(raw_rows) >= self.settings.attachment_max_rows + 1:
                        break
                if not raw_rows:
                    continue
                header, data_start = self._workbook_header_info(raw_rows)
                data_rows = raw_rows[data_start:] if header else raw_rows
                carry: dict[int, str] = {}
                for offset, values in enumerate(
                    data_rows,
                    start=data_start + 1 if header else 1,
                ):
                    values = self._expand_merged_values(values, header, carry)
                    row_cells = self._workbook_row_cells(values, header)
                    for block_index, cells in enumerate(row_cells, start=1):
                        if not cells:
                            continue
                        rows.append(
                            {
                                "sheet_name": worksheet.title,
                                "row_number": offset,
                                "row_kind": "tabular_parallel" if len(row_cells) > 1 else "tabular",
                                "cells": cells,
                                "row_text": "；".join(f"{key}：{value}" for key, value in cells.items()),
                                "extraction_confidence": "high",
                                "parallel_block": block_index if len(row_cells) > 1 else None,
                                "row_key_suffix": (
                                    f"parallel-{block_index}" if len(row_cells) > 1 else ""
                                ),
                            }
                        )
                    if len(rows) >= self.settings.attachment_max_rows:
                        break
                if len(rows) >= self.settings.attachment_max_rows:
                    break
        finally:
            workbook.close()
        return {
            "rows": rows,
            "metadata": {
                "parser": "openpyxl",
                "sheet_count": sheet_count,
                "row_count": len(rows),
                "extraction_mode": "workbook_rows",
                "extraction_note": "表格行已保存为私有待核验记录",
            },
        }

    def _extract_legacy_workbook(self, path: Path) -> dict[str, object]:
        """Extract legacy BIFF ``.xls`` files used by government portals.

        A large share of provincial recruitment tables still uses the binary
        Excel format.  Keep it on the same private review path as XLSX: rows
        are evidence candidates, not automatically published vacancies.
        """
        # A number of government CMS instances keep an ``.xls`` display name
        # while serving an OOXML workbook.  Detect the ZIP signature before
        # handing the bytes to xlrd, otherwise a valid table is reported as a
        # parser failure.
        if path.read_bytes()[:2] == b"PK":
            extracted = self._extract_workbook(path)
            metadata = dict(extracted["metadata"])
            metadata["parser"] = "openpyxl (mislabelled .xls)"
            metadata["extraction_note"] = (
                "文件扩展名为 .xls，但内容为 OOXML；已用 openpyxl 提取并保留原始哈希"
            )
            extracted["metadata"] = metadata
            return extracted
        try:
            import xlrd
        except ImportError as error:
            raise AttachmentSkipped("Legacy Excel parser xlrd is not installed") from error
        try:
            workbook = xlrd.open_workbook(filename=str(path), on_demand=True)
        except Exception as error:
            raise AttachmentProcessingError(
                f"Legacy Excel extraction failed: {error}"
            ) from error
        rows: list[dict[str, object]] = []
        sheet_count = 0
        try:
            for sheet in workbook.sheets():
                sheet_count += 1
                raw_rows: list[list[str]] = []
                for row_number in range(sheet.nrows):
                    values = [
                        self._cell_text(sheet.cell_value(row_number, column))
                        for column in range(sheet.ncols)
                    ]
                    if any(values):
                        raw_rows.append(values)
                    if len(raw_rows) >= self.settings.attachment_max_rows + 1:
                        break
                if not raw_rows:
                    continue
                header, data_start = self._workbook_header_info(raw_rows)
                data_rows = raw_rows[data_start:] if header else raw_rows
                carry: dict[int, str] = {}
                for offset, values in enumerate(
                    data_rows,
                    start=data_start + 1 if header else 1,
                ):
                    values = self._expand_merged_values(values, header, carry)
                    row_cells = self._workbook_row_cells(values, header)
                    for block_index, cells in enumerate(row_cells, start=1):
                        if not cells:
                            continue
                        rows.append(
                            {
                                "sheet_name": sheet.name,
                                "row_number": offset,
                                "row_kind": "tabular_parallel" if len(row_cells) > 1 else "tabular",
                                "cells": cells,
                                "row_text": "；".join(
                                    f"{key}：{value}" for key, value in cells.items()
                                ),
                                "extraction_confidence": "high",
                                "parallel_block": block_index if len(row_cells) > 1 else None,
                                "row_key_suffix": (
                                    f"parallel-{block_index}" if len(row_cells) > 1 else ""
                                ),
                            }
                        )
                    if len(rows) >= self.settings.attachment_max_rows:
                        break
                if len(rows) >= self.settings.attachment_max_rows:
                    break
        finally:
            release = getattr(workbook, "release_resources", None)
            if callable(release):
                release()
        return {
            "rows": rows,
            "metadata": {
                "parser": "xlrd",
                "sheet_count": sheet_count,
                "row_count": len(rows),
                "extraction_mode": "workbook_rows",
                "extraction_note": "XLS 表格行已保存为私有待核验记录",
            },
        }

    def _extract_csv(self, path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        all_rows = [
            [self._cell_text(cell) for cell in values]
            for values in reader
        ]
        all_rows = [row for row in all_rows if any(row)]
        header = self._workbook_header(all_rows[0]) if all_rows else []
        rows: list[dict[str, object]] = []
        for number, values in enumerate(all_rows[1:] if header else all_rows, start=2 if header else 1):
            cells = {
                (header[index] if header and index < len(header) and header[index] else f"列{index + 1}"): value
                for index, value in enumerate(values)
                if value
            }
            if cells:
                rows.append(
                    {
                        "sheet_name": "csv",
                        "row_number": number,
                        "row_kind": "tabular",
                        "cells": cells,
                        "row_text": "；".join(f"{key}：{value}" for key, value in cells.items()),
                        "extraction_confidence": "high",
                    }
                )
            if len(rows) >= self.settings.attachment_max_rows:
                break
        return {
            "rows": rows,
            "metadata": {
                "parser": "csv",
                "row_count": len(rows),
                "text_characters": len(text),
                "extraction_mode": "workbook_rows",
                "extraction_note": "CSV 行已保存为私有待核验记录",
            },
        }

    def _extract_docx(self, path: Path) -> dict[str, object]:
        try:
            from docx import Document
        except ImportError as error:
            raise AttachmentSkipped("DOCX parser python-docx is not installed") from error
        document = Document(str(path))
        rows: list[dict[str, object]] = []
        table_count = 0
        for table_index, table in enumerate(document.tables, start=1):
            headers: list[str] = []
            for cell_index, cell in enumerate(table.rows[0].cells if table.rows else [], start=1):
                header = " ".join(cell.text.split()) or f"列{cell_index}"
                if header in headers:
                    header = f"{header}#{headers.count(header) + 1}"
                headers.append(header)
            if not headers:
                continue
            table_count += 1
            for row_number, table_row in enumerate(table.rows[1:], start=2):
                values = [" ".join(cell.text.split()) for cell in table_row.cells]
                if not any(values):
                    continue
                cells = {
                    headers[index] if index < len(headers) else f"列{index + 1}": value
                    for index, value in enumerate(values)
                    if value
                }
                rows.append(
                    {
                        "sheet_name": f"table-{table_index}",
                        "row_number": row_number,
                        "row_kind": "tabular",
                        "cells": cells,
                        "row_text": "；".join(value for value in values if value),
                        "extraction_confidence": "high",
                    }
                )
                if len(rows) >= self.settings.attachment_max_rows:
                    break
            if len(rows) >= self.settings.attachment_max_rows:
                break

        # Some official Word notices encode the position conditions as plain
        # paragraphs rather than a table. Keep that fallback, but do not add
        # the surrounding notice prose when a real table was extracted.
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        if not table_count:
            rows = [
                {
                    "sheet_name": "docx",
                    "row_number": index,
                    "row_kind": "text_table",
                    "cells": {"正文": value},
                    "row_text": value,
                    "extraction_confidence": "medium",
                }
                for index, value in enumerate(
                    paragraphs[: self.settings.attachment_max_rows], start=1
                )
            ]
        return {
            "rows": rows,
            "metadata": {
                "parser": "python-docx",
                "row_count": len(rows),
                "table_count": table_count,
                "paragraph_count": len(paragraphs),
                "extraction_mode": "tables" if table_count else "paragraphs",
                "extraction_note": (
                    "DOCX 表格逐行保存为私有待核验记录"
                    if table_count
                    else "DOCX 段落已保存为私有待核验记录"
                ),
            },
        }

    def _queue_candidates(
        self,
        artifact: dict[str, object],
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for row in rows:
            candidate = self._candidate_from_row(artifact, row)
            if candidate is None:
                continue
            candidates.append(
                self.database.upsert_artifact_job_candidate(candidate)
            )
        return candidates

    def _reconcile_candidate_queue(
        self,
        artifact: dict[str, object],
        rows: list[dict[str, object]],
    ) -> AttachmentQueueResult:
        """Keep only position-table rows eligible for this student service.

        This gate intentionally applies before human review.  A reviewer should
        see only rows that already contain a job title, a professional condition
        (or an explicit unrestricted-major condition), and a degree condition.
        The public publication gate remains stricter and runs again at publish
        time with the same row-level evidence.
        """
        rejection_reason = self._attachment_queue_rejection_reason(artifact, rows)
        if rejection_reason:
            rejected = self.database.reject_unreviewed_artifact_candidates(
                int(artifact["id"]),
                reason=rejection_reason,
            )
            self._record_queue_decision(
                artifact,
                status="rejected_non_position_attachment",
                reason=rejection_reason,
                retained_rows=0,
                rejected_rows=rejected,
            )
            return AttachmentQueueResult(0, rejected, rejection_reason)

        candidates = self._queue_candidates(artifact, rows)
        accepted_row_ids = [
            int(candidate["artifact_row_id"])
            for candidate in candidates
            if candidate.get("artifact_row_id") is not None
        ]
        rejected = self.database.reject_unreviewed_artifact_candidates(
            int(artifact["id"]),
            reason=(
                "Phase 62 候选净化：该附件行未同时提供岗位、专业/不限专业和学历"
                "的地学院学生可核验条件。原始附件与行证据已保留。"
            ),
            keep_artifact_row_ids=accepted_row_ids,
        )
        detail = (
            "已按岗位、专业/不限专业、学历和地学院学生范围完成候选净化；"
            f"保留 {len(candidates)} 条，拒绝 {rejected} 条未复核候选。"
        )
        self._record_queue_decision(
            artifact,
            status="eligible_position_rows",
            reason=detail,
            retained_rows=len(candidates),
            rejected_rows=rejected,
        )
        return AttachmentQueueResult(
            len(candidates),
            rejected,
            detail,
        )

    def _record_queue_decision(
        self,
        artifact: dict[str, object],
        *,
        status: str,
        reason: str,
        retained_rows: int,
        rejected_rows: int,
    ) -> None:
        self.database.update_source_artifact_processing(
            int(artifact["id"]),
            extraction_status=str(artifact["extraction_status"]),
            metadata_updates={
                "candidate_queue_status": status,
                "candidate_queue_reason": reason,
                "candidate_queue_retained_rows": retained_rows,
                "candidate_queue_rejected_rows": rejected_rows,
                "candidate_queue_checked_at": _utc_now(),
            },
        )

    @staticmethod
    def _discovered_artifact_kind(label: str, artifact_url: str) -> tuple[str, str]:
        context = f"{label} {Path(urlparse(artifact_url).path).name}".casefold()
        marker = next(
            (item for item in NON_POSITION_ATTACHMENT_MARKERS if item.casefold() in context),
            None,
        )
        if marker:
            kind = (
                "application_material"
                if any(item.casefold() in context for item in APPLICATION_ATTACHMENT_MARKERS)
                else "supporting_document"
            )
            return kind, f"non_position_attachment:{marker}"
        if any(item.casefold() in context for item in POSITION_TABLE_MARKERS):
            return "position_table", "position_table_hint"
        return "announcement_attachment", "unclassified_attachment"

    @staticmethod
    def _attachment_queue_rejection_reason(
        artifact: dict[str, object],
        rows: list[dict[str, object]],
    ) -> str | None:
        metadata = artifact.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        label = str(metadata.get("display_name") or "")
        kind, intent = OfficialAttachmentProcessor._discovered_artifact_kind(
            label, str(artifact.get("artifact_url") or "")
        )
        if str(artifact.get("artifact_kind") or "") in {
            "application_material",
            "supporting_document",
        } or kind in {"application_material", "supporting_document"}:
            return (
                "Phase 62 附件用途识别：该文件被识别为"
                f"{intent.split(':', 1)[-1]}类流程/材料附件，不是职位表。"
            )

        # A file name alone is not sufficient evidence of a position table.
        # Require a structured row with a job/position field plus distinct
        # professional and degree fields before any row can reach review.
        for row in rows:
            cells = row.get("cells")
            if not isinstance(cells, dict):
                continue
            keys = " ".join(str(key) for key in cells)
            condition_text = " ".join(
                str(value)
                for key, value in cells.items()
                if any(marker in str(key) for marker in ("岗位条件", "任职条件", "资格条件"))
            )
            has_title = any(item in keys for item in ("岗位", "职位"))
            has_major = any(item in keys for item in ("专业", "学科")) or bool(
                condition_text and extract_major_tags(condition_text)
            )
            has_degree = any(item in keys for item in ("学历", "学位", "面向对象")) or bool(
                condition_text and extract_degree_levels(condition_text)
            )
            if has_title and has_major and has_degree:
                return None
        return (
            "Phase 62 附件用途识别：未发现同时包含岗位/职位、专业和学历字段的"
            "职位表结构；原始附件与行证据已保留。"
        )

    def _candidate_from_row(
        self,
        artifact: dict[str, object],
        row: dict[str, object],
    ) -> dict[str, object] | None:
        cells = row.get("cells") or {}
        if not isinstance(cells, dict):
            return None
        text = str(row.get("row_text") or "").strip()
        title = self._field(
            cells,
            ("岗位名称", "岗位", "职位", "招聘岗位", "岗位名称（岗位）", "需求岗位"),
        )
        position_code = self._field(
            cells,
            ("职位代码", "岗位代码", "职位编号", "岗位编号", "代码"),
        )
        employer = self._field(
            cells,
            ("用人单位", "招聘单位", "单位名称", "单位", "招聘机构", "人才需求单位"),
        )
        location = self._field(cells, ("工作地点", "工作区域", "工作城市", "所在地", "地点"))
        degree = self._field(
            cells, ("学历", "学历要求", "学历层次", "学历及学位", "学位要求", "面向对象")
        )
        major = self._field(
            cells,
            (
                "专业",
                "专业要求",
                "需求专业",
                "专业范围",
                "所学专业",
                "专业类别",
                "专业名称",
            ),
        )
        published = self._field(cells, ("发布日期", "发布时间", "公告日期", "发布日"))
        deadline = self._field(cells, ("报名截止", "截止日期", "报名截止日期", "截止时间", "报名时间"))
        # Some official enterprise tables consolidate professional and degree
        # requirements into one ``岗位条件`` column.  Keep the original
        # condition text as row-level evidence, but only promote it when the
        # shared taxonomy can identify a major or a supported degree.
        condition = self._field(
            cells,
            (
                "岗位条件",
                "岗位要求",
                "任职条件",
                "任职要求",
                "任职资格",
                "资格条件",
                "招聘条件",
                "应聘条件",
                "专业及学历要求",
                "工作经验",
                "工作年限",
            ),
        )
        if condition:
            if not major and extract_major_tags(condition):
                major = condition
            if not degree and extract_degree_levels(condition):
                degree = condition
        if title and major and title in {"专业技术", "专业技术岗", "专业技术岗位"}:
            title = f"{title}（{major[:100]}）"
        relevant_text = " ".join(filter(None, (title, major, degree, text)))
        # Do not infer a job row from arbitrary document prose.  The three
        # values below must all be extracted from the same structured row.
        if not title or not major or not degree:
            return None
        metadata = artifact.get("metadata") or {}
        source = self.database.get_source(str(artifact.get("source_id") or "")) or {}
        employer = employer or str(
            metadata.get("employer")
            or source.get("publisher")
            or "官方公告单位"
        )
        published = published or str(metadata.get("published_date") or "") or None
        deadline = deadline or str(metadata.get("deadline_date") or "") or None
        score, _, major_tags = score_relevance(
            relevant_text,
            "A",
            str(artifact.get("metadata", {}).get("category") or "能源、工程与地学拓展"),
            qualification_evidence=True,
        )
        field_evidence = build_attachment_field_evidence(
            title=title,
            major=major,
            degree=degree,
            location=location,
            artifact=artifact,
            row=row,
            row_text=text,
        )
        if condition:
            field_evidence["岗位资格条件"] = condition
        if position_code:
            field_evidence["职位代码"] = position_code
        headcount_key = None
        headcount = self._field(
            cells,
            ("招聘人数", "需求人数", "计划人数", "人数", "计划数"),
        )
        if not headcount:
            # The CCGC table labels the numeric demand column ``备注``.  It
            # is retained with its original label so administrators can
            # verify the interpretation against the official total row.
            headcount = self._field(cells, ("备注",))
            headcount_key = "备注"
        if headcount and re.fullmatch(r"\d+(?:\.0+)?", headcount):
            field_evidence["招聘人数"] = str(int(float(headcount)))
            if headcount_key:
                field_evidence["招聘人数原字段"] = headcount_key
        publication = evaluate_student_publication(
            {
                "source_id": artifact["source_id"],
                "category": str(metadata.get("category") or ""),
                "title": title,
                "location": location,
                "field_evidence": field_evidence,
            }
        )
        if publication.status not in {
            PUBLICATION_STUDENT_ELIGIBLE,
            PUBLICATION_UNRESTRICTED_ELIGIBLE,
        } and publication.label != "待补岗位地点":
            return None
        return {
            "artifact_row_id": row["id"],
            "source_id": artifact["source_id"],
            "official_page_url": artifact["parent_url"],
            "title": title,
            "employer": employer,
            "application_url": None,
            "location": location,
            "published_date": _normalize_date(published),
            "deadline_date": _normalize_date(deadline),
            "degree_levels": extract_degree_levels(degree or relevant_text),
            "major_tags": major_tags,
            "summary": text[:420],
            "description": text[: self.settings.attachment_max_text_characters],
            "field_evidence": field_evidence,
            "relevance_score": score,
            "review_status": "needs_review",
            # Keep this empty: a non-empty note is reserved for a human's
            # post-extraction verification decision, not the parser's status.
            "review_note": "",
        }

    @staticmethod
    def _field(cells: dict[object, object], aliases: tuple[str, ...]) -> str | None:
        normalized = {
            re.sub(r"[\s:：（）()【】\[\]]", "", str(key)).lower(): str(value).strip()
            for key, value in cells.items()
            if str(value).strip()
        }
        for alias in aliases:
            key = re.sub(r"[\s:：（）()【】\[\]]", "", alias).lower()
            if key in normalized:
                return normalized[key][:500]
        return None

    @staticmethod
    def _workbook_header(row: list[str]) -> list[str]:
        if not row:
            return []
        keywords = ("岗位", "职位", "单位", "专业", "学历", "地点", "报名", "截止")
        score = sum(any(keyword in cell for keyword in keywords) for cell in row)
        if score < 1:
            return []
        return [cell or f"列{index + 1}" for index, cell in enumerate(row)]

    @classmethod
    def _workbook_row_cells(
        cls,
        values: list[str],
        header: list[str],
    ) -> list[dict[str, str]]:
        """Preserve parallel position blocks in one spreadsheet row.

        Some official workbooks place two compact position tables side by side.
        Turning that row directly into a dictionary silently overwrites the
        second ``岗位/专业/学历`` header.  Split only when each repeated block
        has the three fields needed for a student-review candidate; ordinary
        tables keep the legacy one-row representation.
        """

        if not header:
            cells = {
                f"列{index + 1}": value
                for index, value in enumerate(values)
                if value
            }
            return [cells] if cells else []

        def normalized(value: str) -> str:
            return re.sub(r"[\\s:：()（）/]+", "", str(value or "")).lower()

        title_aliases = {
            normalized(item)
            for item in ("岗位名称", "招聘岗位", "岗位", "职位名称", "招聘职位")
        }
        major_aliases = {
            normalized(item)
            for item in ("专业", "专业要求", "所需专业", "需求专业", "专业范围")
        }
        degree_aliases = {
            normalized(item)
            for item in ("学历", "学历要求", "学历学位", "学位要求", "面向对象")
        }
        title_positions = [
            index for index, value in enumerate(header)
            if normalized(value) in title_aliases
        ]
        if len(title_positions) < 2:
            return [
                {
                    (header[index] if index < len(header) and header[index] else f"列{index + 1}"): value
                    for index, value in enumerate(values)
                    if value
                }
            ]

        starts = title_positions
        blocks: list[tuple[int, int]] = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(header)
            labels = {normalized(item) for item in header[start:end]}
            if labels & major_aliases and labels & degree_aliases:
                blocks.append((start, end))
        if len(blocks) < 2:
            return [
                {
                    (header[index] if index < len(header) and header[index] else f"列{index + 1}"): value
                    for index, value in enumerate(values)
                    if value
                }
            ]

        prefix_end = blocks[0][0]
        result: list[dict[str, str]] = []
        for start, end in blocks:
            cells: dict[str, str] = {}
            indices = list(range(0, prefix_end)) + list(range(start, min(end, len(values))))
            for index in indices:
                if index >= len(values) or not values[index]:
                    continue
                key = header[index] if index < len(header) and header[index] else f"列{index + 1}"
                if key in cells:
                    key = f"{key}#{index + 1}"
                cells[key] = values[index]
            if cells:
                result.append(cells)
        return result or [{}]

    @classmethod
    def _workbook_header_info(cls, rows: list[list[str]]) -> tuple[list[str], int]:
        """Find a possibly multi-row header and return its first data index."""
        for index, row in enumerate(rows[:8]):
            header = cls._workbook_header(row)
            if not header:
                continue
            header_score = sum(
                any(
                    keyword in cell
                    for keyword in ("岗位", "职位", "单位", "专业", "学历", "地点", "报名", "截止", "学位")
                )
                for cell in header
                if cell
            )
            end = index + 1
            while end < len(rows):
                candidate = rows[end]
                score = sum(
                    any(
                        keyword in cell
                        for keyword in ("岗位", "职位", "单位", "专业", "学历", "地点", "报名", "截止", "学位")
                    )
                    for cell in candidate
                    if cell
                )
                # A data row can contain the word “专业”; continuation rows
                # usually expose at least two field labels and no job code.
                has_code = any(re.search(r"20\d{2}\d+", cell) for cell in candidate if cell)
                # Once a first header row already exposes several independent
                # field labels, the next row is data even if its employer and
                # major values happen to contain words such as “单位” or
                # “专业”.  The old rule merged those values into the header
                # when a portal used a short non-year position code (A-001).
                if score < 2 or has_code or header_score >= 3:
                    break
                for column, value in enumerate(candidate):
                    if not value:
                        continue
                    parent = header[column] if column < len(header) else ""
                    if not parent or parent.startswith("列"):
                        header[column] = value
                    elif value not in parent:
                        header[column] = value
                end += 1
            return header, end
        return [], 0

    @staticmethod
    def _expand_merged_values(
        values: list[str],
        header: list[str],
        carry: dict[int, str],
    ) -> list[str]:
        """Forward-fill only organizational columns merged in official tables."""
        expanded = list(values)
        carry_columns = {"主管部门", "招聘单位", "用人单位", "招聘机构", "单位名称"}
        for index, value in enumerate(expanded):
            label = header[index] if index < len(header) else ""
            if value:
                carry[index] = value
            elif label in carry_columns and index in carry:
                expanded[index] = carry[index]
        return expanded

    @staticmethod
    def _cell_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.date().isoformat()
        return str(value).strip()

    @classmethod
    def _rows_from_pdf_pages(cls, page_texts: list[str]) -> list[dict[str, object]]:
        """Turn positioned PDF text into logical position-table rows.

        Government PDFs frequently wrap a single position over several visual
        lines.  The old line-by-line implementation lost that relationship
        and therefore produced no usable candidate even when the official
        table clearly contained a matching geoscience role.  A position code
        is the most stable row boundary across provincial templates, so lines
        are grouped from one code to the next.  The original text is retained
        verbatim as evidence; inferred fields are only hints for the private
        review queue.
        """
        rows: list[dict[str, object]] = []
        for page_number, page in enumerate(page_texts, start=1):
            lines = [line.rstrip() for line in page.splitlines() if line.strip()]
            if not lines:
                continue
            groups: list[tuple[int, list[str]]] = []
            current: list[str] = []
            current_number = 1
            for line_number, line in enumerate(lines, start=1):
                code_match = cls._pdf_position_code(line)
                # A repeated table header may contain no code and should stay
                # with neither the preceding nor the following position.
                if code_match:
                    if current and cls._pdf_group_has_position(current):
                        groups.append((current_number, current))
                    current = [line.strip()]
                    current_number = line_number
                elif current:
                    if not cls._pdf_table_header(line):
                        current.append(line.strip())
                elif not cls._pdf_table_header(line):
                    # Some official PDFs omit position codes. Preserve those
                    # lines using the safe legacy behavior.
                    groups.append((line_number, [line.strip()]))
            if current and cls._pdf_group_has_position(current):
                groups.append((current_number, current))

            for line_number, group in groups:
                row_text = "\n".join(group)
                cells = cls._pdf_cells(group)
                rows.append(
                    {
                        "sheet_name": f"page-{page_number}",
                        "row_number": line_number,
                        "row_kind": "text_table",
                        "cells": cells or {"正文": row_text},
                        "row_text": row_text,
                        "extraction_confidence": "medium" if len(cells) > 1 else "low",
                    }
                )
        return rows

    @staticmethod
    def _pdf_position_code(line: str) -> str | None:
        """Return a likely official position code from a table line.

        Position tables commonly prefix the code with a serial number (for
        example ``1  202602101``), so requiring the code at column zero loses
        every row in that layout.  Prefer nine-digit year-coded identifiers;
        only then use the stricter legacy start-of-line fallback.
        """
        # Allow spaces between the code's digits, but do not collapse the
        # serial number immediately before it (``1  202602101``).
        year_code = re.search(
            r"(?<!\d)((?:19|20)(?:[\s　]*\d){7})(?!\d)",
            line,
        )
        if year_code:
            return re.sub(r"[\s　]+", "", year_code.group(1))
        compact = re.sub(r"(?<=\d)[\s　]+(?=\d)", "", line.strip())
        match = re.match(r"^(\d{6,12})(?=\s|　|$)", compact)
        return match.group(1) if match else None

    @staticmethod
    def _pdf_table_header(line: str) -> bool:
        normalized = re.sub(r"\s+", "", line)
        keywords = ("岗位代码", "职位代码", "岗位名称", "招聘单位", "学历", "专业", "招聘人数")
        return sum(keyword in normalized for keyword in keywords) >= 2

    @classmethod
    def _pdf_group_has_position(cls, lines: list[str]) -> bool:
        return bool(lines and cls._pdf_position_code(lines[0]))

    @classmethod
    def _pdf_cells(cls, lines: list[str]) -> dict[str, str]:
        """Extract conservative semantic hints from one grouped PDF row."""
        text = " ".join(lines)
        normalized = re.sub(r"\s+", " ", text).strip()
        compact_text = re.sub(r"\s+", "", text)
        cells: dict[str, str] = {}
        code = cls._pdf_position_code(lines[0])
        if code:
            cells["职位代码"] = code

        degree_match = re.search(
            r"(博士研究生|硕士研究生|本科及以上|硕士及以上|本科以上|大专及以上|研究生|博士|硕士|本科|大专)",
            compact_text,
        )
        if degree_match:
            cells["学历要求"] = degree_match.group(1)

        major_terms = (
            "地质资源与地质工程", "资源勘查工程", "矿产普查与勘探", "环境地质工程",
            "人文地理与城乡规划", "地质学", "地质工程", "地球物理学", "地球化学",
            "水文地质", "工程地质", "勘查技术与工程", "测绘工程",
        )
        major_hits = [term for term in major_terms if term in compact_text]
        if major_hits:
            cells["专业要求"] = "、".join(dict.fromkeys(major_hits))

        title_terms = (
            r"地质(?:专业技术人员|技术人员|工程师|勘查人员)",
            # PDF column extraction can split “地质技术人员” as
            # “地质技术人” + “员” on a later visual line.  Keep the
            # normalized title conservative while retaining the row for review.
            r"地质技术人(?:员)?",
            r"资源勘查(?:工程师|技术人员)",
            r"矿产(?:普查|勘查)(?:技术人员|工程师)",
            r"勘查技术与工程(?:技术人员)",
        )
        title_found = False
        for pattern in title_terms:
            title_match = re.search(pattern, compact_text)
            if title_match:
                title = title_match.group(0)
                if title == "地质技术人":
                    title = "地质技术人员"
                cells["岗位名称"] = title
                title_found = True
                break
        if not title_found and major_hits:
            # A layout PDF can separate the title characters across visual
            # columns. Keep the row reviewable without inventing a public
            # title; an administrator must confirm the exact title first.
            cells["岗位名称"] = "职位表岗位（待核验）"
        # Keep the table's visual columns available to a human reviewer even
        # when no semantic label could be inferred.
        parts = [part.strip() for part in re.split(r"\s{2,}|\t+", lines[0]) if part.strip()]
        for index, part in enumerate(parts):
            cells.setdefault(f"列{index + 1}", part)
        cells["正文"] = normalized
        return cells

    def _ocr_pdf(self, path: Path, page_count: int) -> tuple[str, str]:
        if page_count < 1:
            return "", "PDF has no pages for OCR"
        with tempfile.TemporaryDirectory(prefix="job-hub-ocr-") as directory:
            prefix = str(Path(directory) / "page")
            command = [
                "pdftoppm",
                "-f",
                "1",
                "-l",
                str(page_count),
                "-r",
                "160",
                "-png",
                str(path),
                prefix,
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=180)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                return "", f"OCR rasterization unavailable: {error}"
            texts: list[str] = []
            for image in sorted(Path(directory).glob("page-*.png")):
                try:
                    result = subprocess.run(
                        ["tesseract", str(image), "stdout", "-l", self.settings.attachment_ocr_language],
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    return "", f"OCR engine unavailable: {error}"
                texts.append(result.stdout.decode("utf-8", errors="replace"))
            return "\n\n".join(texts)[: self.settings.attachment_max_text_characters], "OCR fallback extracted text with low confidence"

    def _artifact_path(self, artifact: dict[str, object]) -> Path:
        storage_path = str(artifact.get("storage_path") or "")
        if not storage_path:
            raise AttachmentProcessingError("Attachment has no managed storage path")
        root = self.settings.managed_artifact_dir().resolve()
        path = (root / storage_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise AttachmentProcessingError("Attachment storage path escapes managed directory") from error
        if not path.is_file():
            raise AttachmentProcessingError("Managed attachment file does not exist")
        return path

    def _stored_size(self, artifact: dict[str, object]) -> int:
        return self._artifact_path(artifact).stat().st_size

    def _write_text_sidecar(self, artifact_path: Path, text: str) -> str | None:
        if not text:
            return None
        root = self.settings.managed_artifact_dir().resolve()
        sidecar = artifact_path.with_suffix(artifact_path.suffix + ".txt")
        try:
            sidecar.relative_to(root)
        except ValueError as error:  # pragma: no cover - storage path is contract validated
            raise AttachmentProcessingError(
                "Extracted text path escapes managed directory"
            ) from error
        sidecar.write_text(text, encoding="utf-8")
        return sidecar.relative_to(root).as_posix()

    def _record_failure(self, artifact_id: int, status: str, detail: str) -> None:
        try:
            self.database.update_source_artifact_processing(
                artifact_id,
                extraction_status=status,
                parser_version=PARSER_VERSION,
                metadata_updates={"last_error": detail[:2000], "last_error_at": _utc_now()},
            )
        except Exception:
            # Preserve the original processing error; the DB record is best effort.
            pass

    def _robots_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(root)
        if parser is None:
            parser = RobotFileParser()
            robots_url = f"{root}/robots.txt"
            try:
                response = self.request_policy.request(
                    "GET",
                    robots_url,
                    timeout=min(self.settings.request_timeout_seconds, 10),
                    allow_redirects=True,
                )
            except REQUEST_ERRORS as error:
                raise AttachmentSkipped(f"Unable to verify robots.txt for {root}: {error}") from error
            if response.status_code == 404:
                parser.parse([])
            elif response.ok:
                parser.parse(response.text.splitlines())
            else:
                raise AttachmentSkipped(
                    f"Unable to verify robots.txt for {root}: HTTP {response.status_code}"
                )
            self._robots[root] = parser
        if not parser.can_fetch(USER_AGENT, url):
            raise AttachmentSkipped(f"robots.txt does not permit attachment access: {url}")

    @staticmethod
    def _assert_official_hosts(source: dict[str, object], parent_url: str, artifact_url: str) -> None:
        config = source.get("config") or {}
        if not isinstance(config, dict):
            raise AttachmentProcessingError("Registered source config is invalid")
        hosts = {
            str(value).lower().strip()
            for value in config.get("allowed_hosts", [])
            if str(value).strip()
        }
        homepage_host = urlparse(str(source.get("homepage_url") or "")).hostname
        if homepage_host:
            hosts.add(homepage_host.lower())
        hosts.update(
            str(value).lower().strip()
            for value in config.get("attachment_allowed_hosts", [])
            if str(value).strip()
        )
        for label, value in (("parent", parent_url), ("attachment", artifact_url)):
            host = urlparse(value).hostname
            if not host or host.lower() not in hosts:
                raise AttachmentSkipped(f"{label} URL host is outside the official source allowlist")

    @staticmethod
    def _content_type(value: str | None) -> str | None:
        if not value:
            return None
        return value.split(";", 1)[0].strip().lower() or None

    @staticmethod
    def _suffix(url: str, content_type: str | None) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in SUPPORTED_SUFFIXES:
            return suffix
        mapping = {
            "application/pdf": ".pdf",
            "text/csv": ".csv",
            "application/csv": ".csv",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/msword": ".doc",
        }
        return mapping.get(content_type or "", suffix or ".bin")

    @staticmethod
    def _extensionless_endpoint_allowed(source: dict[str, object], url: str) -> bool:
        config = source.get("config") or {}
        patterns = config.get("attachment_url_patterns", []) if isinstance(config, dict) else []
        return any(
            re.search(str(pattern), url, re.IGNORECASE)
            for pattern in patterns
            if str(pattern).strip()
        )

    @staticmethod
    def _sniff_suffix(path: Path) -> str:
        header = path.read_bytes()[:8]
        if header.startswith(b"%PDF-"):
            return ".pdf"
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return ".xls"
        if header.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                if any(name.startswith("xl/") for name in names):
                    return ".xlsx"
                if any(name.startswith("word/") for name in names):
                    return ".docx"
            except (OSError, zipfile.BadZipFile):
                return ".bin"
        return ".bin"

    @staticmethod
    def _looks_like_html(chunk: bytes) -> bool:
        sample = chunk.lstrip().lower()[:256]
        return any(sample.startswith(marker) for marker in HTML_MARKERS)


def process_pending_attachments(
    database: Database,
    processor: OfficialAttachmentProcessor,
    *,
    limit: int = 500,
    retry_failed: bool = False,
    source_ids: set[str] | None = None,
) -> dict[str, object]:
    """Process a bounded, oldest-first slice of the private attachment queue.

    ``registered`` files need a controlled download and extraction.  A file
    that was downloaded in a previous interrupted run is also eligible so the
    parser can resume without another network request.  Failed files are only
    retried when an administrator explicitly asks for it; ``skipped`` files
    commonly represent robots or access-policy decisions and must not be
    retried implicitly by the daily worker.

    The returned items are operational metadata only.  No candidate is
    published here; ``OfficialAttachmentProcessor.process`` stops at the
    private review queue by design.
    """
    bounded_limit = max(1, min(int(limit), 500))
    eligible_statuses = {"registered", "downloaded"}
    if retry_failed:
        eligible_statuses.add("failed")
    artifacts = database.list_source_artifacts(
        limit=bounded_limit,
        oldest_first=True,
        extraction_statuses=eligible_statuses,
    )
    source_scoped_artifacts = [
        artifact
        for artifact in artifacts
        if (
            source_ids is None
            or str(artifact.get("source_id") or "") in source_ids
        )
    ]
    policy_skipped = [
        artifact
        for artifact in source_scoped_artifacts
        if government_artifact_processing_block_reason(
            artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else None
        )
    ]
    selected = [
        artifact for artifact in source_scoped_artifacts if artifact not in policy_skipped
    ]
    summary: dict[str, object] = {
        "selected": len(selected),
        "processed": 0,
        "extracted": 0,
        "skipped": 0,
        "failed": 0,
        "rows_extracted": 0,
        "candidates_created": 0,
        "candidates_rejected": 0,
        "retry_failed": bool(retry_failed),
        "policy_skipped": len(policy_skipped),
        "policy_skip_items": [
            {
                "artifact_id": int(artifact["id"]),
                "source_id": str(artifact.get("source_id") or ""),
                "status": "skipped",
                "detail": government_artifact_processing_block_reason(
                    artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else None
                ),
            }
            for artifact in policy_skipped
        ],
        "items": [],
    }
    items = summary["items"]
    assert isinstance(items, list)
    for artifact in selected:
        artifact_id = int(artifact["id"])
        summary["processed"] = int(summary["processed"]) + 1
        try:
            result = processor.process(artifact_id)
            result_payload = result.as_dict()
            status = str(result_payload.get("status") or "")
            if status == "extracted":
                summary["extracted"] = int(summary["extracted"]) + 1
            elif status == "skipped":
                summary["skipped"] = int(summary["skipped"]) + 1
            elif status == "failed":
                summary["failed"] = int(summary["failed"]) + 1
            summary["rows_extracted"] = int(summary["rows_extracted"]) + int(
                result_payload.get("rows_extracted") or 0
            )
            summary["candidates_created"] = int(summary["candidates_created"]) + int(
                result_payload.get("candidates_created") or 0
            )
            summary["candidates_rejected"] = int(summary["candidates_rejected"]) + int(
                result_payload.get("candidates_rejected") or 0
            )
            items.append(
                {
                    "artifact_id": artifact_id,
                    "source_id": str(artifact.get("source_id") or ""),
                    **result_payload,
                }
            )
        except (AttachmentProcessingError, ValueError) as error:
            summary["failed"] = int(summary["failed"]) + 1
            items.append(
                {
                    "artifact_id": artifact_id,
                    "source_id": str(artifact.get("source_id") or ""),
                    "status": "failed",
                    "error": str(error)[:500],
                }
            )
    return summary


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_detail(existing: str, value: str) -> str:
    """Join bounded operational details without storing remote document text."""
    return "; ".join(item for item in (existing, value) if item)[:1_000]


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", value)
    if not match:
        return value[:50]
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
