"""Add Phase 529 private official recovery entries without replacing the source ledger."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


SOURCE_PATH = Path("data/sources.json")
BACKUP_PATH = Path(
    os.environ.get(
        "PHASE529_BACKUP_PATH",
        str(SOURCE_PATH.with_name("sources.phase529-prechange.json")),
    )
)

RECOVERY_ENTRIES = [
    {
        "id": "gxib-official-recovery",
        "name": "中国科学院广西植物研究所官方恢复入口（待核验）",
        "publisher": "中国科学院广西植物研究所",
        "homepage_url": "https://www.gxib.cn/",
        "source_type": "manual",
        "category": "事业单位与科研院所",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "allowed_hosts": ["www.gxib.cn", "gxib.cn"],
            "fallback_role": "official_recovery",
            "automation_status": "candidate_only",
            "note": "仅作为第三方线索正文中出现的广西植物研究所官方域名候选；未完成 robots、公告和附件复核，不参与采集或发布。",
        },
    },
    {
        "id": "gxust-official-recovery",
        "name": "广西科技大学官方恢复入口（待核验）",
        "publisher": "广西科技大学",
        "homepage_url": "https://www.gxust.edu.cn/",
        "source_type": "manual",
        "category": "高校与事业单位",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "allowed_hosts": ["www.gxust.edu.cn", "gxust.edu.cn"],
            "fallback_role": "official_recovery",
            "automation_status": "candidate_only",
            "note": "仅作为第三方线索正文中出现的广西科技大学官方域名候选；未完成 robots、公告和岗位表复核，不参与采集或发布。",
        },
    },
    {
        "id": "hunan-hrss-official-recovery",
        "name": "湖南省人力资源和社会保障厅官方恢复入口（待核验）",
        "publisher": "湖南省人力资源和社会保障厅",
        "homepage_url": "https://rst.hunan.gov.cn/",
        "source_type": "manual",
        "category": "事业单位与政府招聘",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "allowed_hosts": ["rst.hunan.gov.cn"],
            "fallback_role": "official_recovery",
            "automation_status": "candidate_only",
            "note": "仅作为第三方线索正文中出现的湖南省人社厅官方域名候选；未完成公告、岗位表和当前性复核，不参与采集或发布。",
        },
    },
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"missing source ledger: {SOURCE_PATH}")
    original_digest = digest(SOURCE_PATH)
    if BACKUP_PATH.exists() and digest(BACKUP_PATH) != original_digest:
        raise SystemExit(f"conflicting existing backup: {BACKUP_PATH}")
    data = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    by_id = {str(item.get("id")): item for item in data}

    for entry in RECOVERY_ENTRIES:
        existing = by_id.get(entry["id"])
        if existing is not None and existing != entry:
            raise SystemExit(f"conflicting existing entry: {entry['id']}")
        if existing is None:
            data.append(entry)

    cea = by_id.get("cea-2027-recruitment")
    if cea is None:
        raise SystemExit("missing cea-2027-recruitment")
    allowed_hosts = cea.setdefault("config", {}).setdefault("allowed_hosts", [])
    attachment_hosts = cea["config"].setdefault("attachment_allowed_hosts", [])
    for host in ("www.cea-igp.ac.cn", "cea-igp.ac.cn"):
        if host not in allowed_hosts:
            allowed_hosts.append(host)
        if host not in attachment_hosts:
            attachment_hosts.append(host)

    if not BACKUP_PATH.exists():
        shutil.copy2(SOURCE_PATH, BACKUP_PATH)
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=SOURCE_PATH.parent, delete=False
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, SOURCE_PATH)
    print(
        json.dumps(
            {
                "source_path": str(SOURCE_PATH),
                "backup_path": str(BACKUP_PATH),
                "original_sha256": original_digest,
                "updated_sha256": digest(SOURCE_PATH),
                "entry_count": len(data),
                "added_ids": [entry["id"] for entry in RECOVERY_ENTRIES if entry["id"] not in by_id],
                "cea_allowed_hosts": allowed_hosts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
