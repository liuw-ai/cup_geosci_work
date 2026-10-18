"""Third-party position-table contracts for Phase 3.

The adapters in this module normalize B-level tables into the existing
government-table candidate shape.  They never publish a row and never treat
the third-party URL as an application URL.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
from html.parser import HTMLParser

from job_hub.government_table_parser import parse_position_table_rows
from job_hub.government_table_parser import load_position_table_with_report
from job_hub.phase3_policy import normalize_trust_grade


BATCH_CANDIDATE_SOURCES = frozenset(
    {"offcn-discovery", "fenbi-discovery", "huatu-discovery", "gongkaoleida-discovery", "gongkaozixun-discovery"}
)
ADAPTER_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "third_party_table_adapters.json"
THIRD_PARTY_TABLE_USER_AGENT = "job-hub-phase4-table-review/1.0"


class ThirdPartyTableAccessLimited(ValueError):
    """Raised when a third-party table cannot be fetched under access policy."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = next((str(value or "").strip() for key, value in attrs if key.lower() == "href"), "")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []


def load_table_adapters(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the reviewed B-level page-to-table adapter contract."""
    payload = json.loads((path or ADAPTER_CONFIG_PATH).read_text(encoding="utf-8"))
    adapters = payload.get("adapters") if isinstance(payload, dict) else None
    if not isinstance(adapters, list):
        raise ValueError("third-party table adapter registry must contain adapters")
    result: dict[str, dict[str, Any]] = {}
    for item in adapters:
        if not isinstance(item, dict) or str(item.get("source_id") or "") not in BATCH_CANDIDATE_SOURCES:
            raise ValueError("third-party adapter has an unapproved source_id")
        source_id = str(item["source_id"])
        if source_id in result:
            raise ValueError(f"duplicate third-party adapter: {source_id}")
        result[source_id] = dict(item)
    if set(result) != set(BATCH_CANDIDATE_SOURCES):
        raise ValueError("all B-level batch sources require a table adapter")
    return result


def discover_table_links(
    html: str,
    *,
    page_url: str,
    source_id: str,
    adapter_registry: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract candidate table downloads from a reviewed aggregator page.

    This is a discovery step only.  Every result remains ``candidate_only``;
    the official URL field is deliberately empty until a reviewer binds the
    row to a government or employer announcement.
    """
    adapters = adapter_registry or load_table_adapters()
    adapter = adapters.get(source_id)
    if adapter is None:
        raise ValueError("source has no reviewed B-level table adapter")
    page_host = (urlparse(page_url).hostname or "").lower().rstrip(".")
    allowed_hosts = {str(value).lower().rstrip(".") for value in adapter.get("allowed_hosts", [])}
    if page_host not in allowed_hosts:
        raise ValueError("discovery page host is not allowed for this source")
    patterns = tuple(str(value).lower() for value in adapter.get("table_link_patterns", []))
    parser = _LinkParser()
    parser.feed(str(html or ""))
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, text in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").lower().rstrip(".")
        label = " ".join((text, href)).lower()
        suffix = Path(parsed.path).suffix.lower()
        if parsed.scheme not in {"http", "https"} or host not in allowed_hosts:
            continue
        if not (suffix in {".xls", ".xlsx", ".xlsm", ".csv", ".tsv", ".pdf"} or any(pattern in label for pattern in patterns)):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append({
            "source_id": source_id,
            "discovery_page_url": page_url,
            "table_url": absolute,
            "label": text.strip() or absolute,
            "trust_grade": "B",
            "publication_status": "candidate_only",
            "official_url": None,
            "review_required": bool(adapter.get("manual_review_required", True)),
        })
    return found


def normalize_third_party_table(
    rows: Iterable[dict[str, Any]],
    *,
    source_id: str,
    third_party_table_url: str,
    official_notice_url: str = "",
    official_table_url: str = "",
    batch_id: str,
    position_type: str,
    province: str,
    deadline_date: str = "",
    deadline_policy: str = "fixed_date",
    location: str = "",
    sheet_name: str = "Sheet1",
) -> dict[str, Any]:
    """Parse a B-level table as candidates with explicit provenance."""

    if source_id not in BATCH_CANDIDATE_SOURCES:
        raise ValueError("source is not approved for B-level batch candidates")
    if not str(third_party_table_url or "").startswith(("http://", "https://")):
        raise ValueError("third_party_table_url must be an HTTP(S) URL")
    parsed = parse_position_table_rows(
        rows,
        batch_id=batch_id,
        source_id=source_id,
        position_type=position_type,
        province=province,
        official_notice_url=official_notice_url,
        official_attachment_url=official_table_url,
        deadline_date=deadline_date,
        deadline_policy=deadline_policy,
        location=location,
        sheet_name=sheet_name,
    )
    parsed["provenance"] = {
        "trust_grade": normalize_trust_grade("B"),
        "source_id": source_id,
        "third_party_table_url": third_party_table_url,
        "official_notice_url": official_notice_url or None,
        "official_table_url": official_table_url or None,
        "publication_status": "candidate_only",
        "application_url": None,
    }
    parsed["publication_gate"] = (
        "official_notice_or_table_required_before_student_publication"
    )
    return parsed


def _bounded_download(url: str, *, timeout: int, max_bytes: int) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": THIRD_PARTY_TABLE_USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # nosec B310: reviewed URL is operator supplied
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(min(1024 * 1024, max_bytes - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"third-party table exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks), content_type


def _robots_permit(url: str, *, timeout: int) -> dict[str, Any]:
    """Require a verified robots policy before downloading a third-party file.

    A robots denial or an unverifiable policy is an access limitation, not an
    invitation to retry through another transport. A missing robots document
    (HTTP 404) follows the project-wide policy and permits the URL.
    """

    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(root, "/robots.txt")
    request = Request(robots_url, headers={"User-Agent": THIRD_PARTY_TABLE_USER_AGENT})
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        with urlopen(request, timeout=min(timeout, 10)) as response:  # nosec B310: reviewed host
            status = int(getattr(response, "status", response.getcode()))
            body = response.read(512_000).decode("utf-8", errors="replace")
    except HTTPError as error:
        if int(error.code) == 404:
            return {
                "robots_url": robots_url,
                "robots_status": 404,
                "robots_allowed": True,
                "robots_checked_at": checked_at,
                "robots_detail": "robots.txt not found; access allowed by policy",
            }
        raise ThirdPartyTableAccessLimited(
            f"unable to verify robots.txt for {root}: HTTP {error.code}"
        ) from error
    except OSError as error:
        raise ThirdPartyTableAccessLimited(
            f"unable to verify robots.txt for {root}: {error.__class__.__name__}"
        ) from error

    if not 200 <= status < 300:
        raise ThirdPartyTableAccessLimited(
            f"unable to verify robots.txt for {root}: HTTP {status}"
        )
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.splitlines())
    allowed = bool(parser.can_fetch(THIRD_PARTY_TABLE_USER_AGENT, url))
    evidence = {
        "robots_url": robots_url,
        "robots_status": status,
        "robots_allowed": allowed,
        "robots_checked_at": checked_at,
        "robots_detail": "robots.txt permits access" if allowed else "robots.txt disallows access",
    }
    if not allowed:
        raise ThirdPartyTableAccessLimited(
            f"robots.txt does not permit third-party table access: {url}"
        )
    return evidence


_TABLE_SUFFIXES = {
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".tsv",
    ".pdf",
    ".docx",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
}


def _validate_table_url(source_id: str, third_party_table_url: str) -> dict[str, Any]:
    if source_id not in BATCH_CANDIDATE_SOURCES:
        raise ValueError("source is not approved for B-level batch candidates")
    parsed_url = urlparse(str(third_party_table_url or ""))
    adapter = load_table_adapters().get(source_id)
    allowed_hosts = {
        str(value).lower().rstrip(".")
        for value in (adapter or {}).get("allowed_hosts", [])
    }
    if parsed_url.scheme not in {"http", "https"} or (
        (parsed_url.hostname or "").lower().rstrip(".") not in allowed_hosts
    ):
        raise ValueError("third-party table URL host is not allowed for this source")
    return parsed_url


def _normalize_table_bytes(
    body: bytes,
    *,
    content_type: str,
    suffix: str,
    source_id: str,
    third_party_table_url: str,
    batch_id: str,
    position_type: str,
    province: str,
    official_notice_url: str = "",
    official_table_url: str = "",
    official_application_url: str = "",
    discovery_page_url: str = "",
    deadline_date: str = "",
    location: str = "",
    storage_dir: Path | None = None,
    capture_mode: str = "network_download",
    robots_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize retained bytes through one auditable path for network/file input."""
    digest = hashlib.sha256(body).hexdigest()
    normalized_suffix = suffix.lower()
    if normalized_suffix not in _TABLE_SUFFIXES:
        normalized_suffix = mimetypes.guess_extension(content_type) or ".bin"
    with tempfile.TemporaryDirectory(prefix="phase4-third-party-") as temp_dir:
        path = Path(temp_dir) / f"table{normalized_suffix}"
        path.write_bytes(body)
        loaded = load_position_table_with_report(path)
    parsed = normalize_third_party_table(
        loaded["rows"],
        source_id=source_id,
        third_party_table_url=third_party_table_url,
        official_notice_url=official_notice_url,
        official_table_url=official_table_url,
        batch_id=batch_id,
        position_type=position_type,
        province=province,
        deadline_date=deadline_date,
        location=location,
    )
    parsed["download"] = {
        "sha256": digest,
        "bytes": len(body),
        "content_type": content_type,
        "captured_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_id": source_id,
        "batch_id": batch_id,
        "table_url": third_party_table_url,
        "discovery_page_url": discovery_page_url or None,
        "retained_path": None,
        "capture_mode": capture_mode,
    }
    if robots_evidence:
        parsed["download"].update(robots_evidence)
    if storage_dir is not None:
        storage_dir.mkdir(parents=True, exist_ok=True)
        retained = storage_dir / f"{batch_id}-{digest[:16]}{normalized_suffix}"
        retained.write_bytes(body)
        parsed["download"]["retained_path"] = str(retained)
    parsed["candidates"] = table_candidates(
        parsed,
        source_id=source_id,
        third_party_table_url=third_party_table_url,
        official_notice_url=official_notice_url,
        official_table_url=official_table_url,
        official_application_url=official_application_url,
        discovery_page_url=discovery_page_url,
    )
    return parsed


def table_candidates(
    parsed: dict[str, Any],
    *,
    source_id: str,
    third_party_table_url: str,
    official_notice_url: str = "",
    official_table_url: str = "",
    official_application_url: str = "",
    discovery_page_url: str = "",
) -> list[dict[str, Any]]:
    """Convert normalized rows into private, auditable candidate-lead records."""

    candidates: list[dict[str, Any]] = []
    batch = parsed.get("batch") if isinstance(parsed.get("batch"), dict) else {}
    download = parsed.get("download") if isinstance(parsed.get("download"), dict) else {}
    evidence = {
        "source_id": source_id,
        "third_party_table_url": third_party_table_url,
        "discovery_page_url": discovery_page_url or None,
        "batch_id": batch.get("id"),
        "sha256": download.get("sha256"),
        "bytes": download.get("bytes"),
        "content_type": download.get("content_type"),
        "captured_at": download.get("captured_at"),
        "retained_path": download.get("retained_path"),
    }
    for row in batch.get("rows", []):
        if not isinstance(row, list) or len(row) < 8:
            continue
        position_code, employer, title, major, degree, headcount, locator, location, *_ = row
        row_url = f"{third_party_table_url}#row-{str(locator)}"
        candidates.append(
            {
                "lead_provider": source_id,
                "lead_url": row_url,
                "discovery_source_id": source_id,
                "title": str(title or "").strip(),
                "employer_hint": str(employer or "").strip(),
                "location_hint": str(location or "").strip(),
                "province_hint": str(batch.get("province") or "").strip() or None,
                "official_url": official_notice_url or None,
                "official_application_url": official_application_url or None,
                "official_table_url": official_table_url or None,
                "third_party_table_url": third_party_table_url,
                "trust_grade": "B",
                "verification_note": "第三方职位表批量候选；必须回溯官方公告/岗位表并完成人工核验后才能发布。",
                "metadata": {
                    "batch_id": batch.get("id"),
                    "position_code": str(position_code or "").strip(),
                    "major": str(major or "").strip(),
                    "degree": str(degree or "").strip(),
                    "headcount": headcount,
                    "locator": str(locator or "").strip(),
                    "publication_status": "candidate_only",
                    "official_notice_url": official_notice_url or None,
                    "official_table_url": official_table_url or None,
                    "application_url": official_application_url or None,
                    "table_evidence": evidence,
                },
            }
        )
    return candidates


def fetch_and_normalize_third_party_table(
    *,
    source_id: str,
    third_party_table_url: str,
    batch_id: str,
    position_type: str,
    province: str,
    official_notice_url: str = "",
    official_table_url: str = "",
    official_application_url: str = "",
    discovery_page_url: str = "",
    deadline_date: str = "",
    location: str = "",
    timeout: int = 30,
    max_bytes: int = 25_000_000,
    storage_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch a complete third-party table and return review-only candidates.

    The downloaded bytes are hashed and optionally retained for audit.  No row
    is published and the third-party URL is never used as an application URL.
    """

    parsed_url = _validate_table_url(source_id, third_party_table_url)
    robots_evidence = _robots_permit(third_party_table_url, timeout=timeout)
    body, content_type = _bounded_download(
        third_party_table_url, timeout=timeout, max_bytes=max_bytes
    )
    return _normalize_table_bytes(
        body,
        content_type=content_type,
        suffix=Path(parsed_url.path).suffix.lower(),
        source_id=source_id,
        third_party_table_url=third_party_table_url,
        batch_id=batch_id,
        position_type=position_type,
        province=province,
        official_notice_url=official_notice_url,
        official_table_url=official_table_url,
        official_application_url=official_application_url,
        discovery_page_url=discovery_page_url,
        deadline_date=deadline_date,
        location=location,
        storage_dir=storage_dir,
        robots_evidence=robots_evidence,
    )


def ingest_third_party_table_file(
    *,
    table_path: Path,
    source_id: str,
    third_party_table_url: str,
    batch_id: str,
    position_type: str,
    province: str,
    official_notice_url: str = "",
    official_table_url: str = "",
    official_application_url: str = "",
    discovery_page_url: str = "",
    deadline_date: str = "",
    location: str = "",
    max_bytes: int = 25_000_000,
    storage_dir: Path | None = None,
) -> dict[str, Any]:
    """Process an operator-provided real table without fetching a blocked site.

    The original third-party URL is still required and host-checked.  The file
    is only evidence for a private candidate queue; it never becomes an
    official URL or a publication authorization.
    """
    parsed_url = _validate_table_url(source_id, third_party_table_url)
    path = Path(table_path)
    if not path.is_file():
        raise ValueError(f"provided third-party table does not exist: {path}")
    body = path.read_bytes()
    if len(body) > max_bytes:
        raise ValueError(f"third-party table exceeds {max_bytes} bytes")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _normalize_table_bytes(
        body,
        content_type=content_type,
        suffix=path.suffix.lower() or Path(parsed_url.path).suffix.lower(),
        source_id=source_id,
        third_party_table_url=third_party_table_url,
        batch_id=batch_id,
        position_type=position_type,
        province=province,
        official_notice_url=official_notice_url,
        official_table_url=official_table_url,
        official_application_url=official_application_url,
        discovery_page_url=discovery_page_url,
        deadline_date=deadline_date,
        location=location,
        storage_dir=storage_dir,
        capture_mode="operator_provided_file",
        robots_evidence={
            "robots_checked_at": None,
            "robots_allowed": None,
            "robots_detail": "operator-provided file; no network fetch performed",
        },
    )


__all__ = [
    "BATCH_CANDIDATE_SOURCES",
    "ThirdPartyTableAccessLimited",
    "fetch_and_normalize_third_party_table",
    "ingest_third_party_table_file",
    "normalize_third_party_table",
    "discover_table_links",
    "load_table_adapters",
    "table_candidates",
]
