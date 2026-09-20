from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_hub.contracts import (
    ContractValidationError,
    validate_employer_registry,
    validate_source_artifact,
    validate_source_registry,
)
from job_hub.employers import load_employer_registry
from job_hub.sources import SourceCollectionError, load_source_registry


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_versioned_source_and_employer_registries_satisfy_the_contract() -> None:
    for filename in ("sources.json", "provincial_sources.json"):
        payload = json.loads((PROJECT_ROOT / "data" / filename).read_text(encoding="utf-8"))
        assert validate_source_registry(payload)
    employers = json.loads(
        (PROJECT_ROOT / "data" / "employer_registry.json").read_text(encoding="utf-8")
    )
    assert validate_employer_registry(employers)
    assert load_employer_registry()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("homepage_url", "not-a-url", "homepage_url"),
        ("source_type", "browser_bypass", "source_type"),
        ("source_tier", "C", "source_tier"),
        ("category", "", "category"),
    ],
)
def test_source_contract_rejects_invalid_required_values(
    field: str,
    value: object,
    message: str,
) -> None:
    record = {
        "id": "official-test",
        "name": "测试来源",
        "publisher": "测试单位",
        "homepage_url": "https://official.example.edu.cn/jobs",
        "source_type": "manual",
        "category": "三桶油与油服",
        "source_tier": "A",
    }
    record[field] = value

    with pytest.raises(ContractValidationError, match=message):
        validate_source_registry([record])


def test_source_loader_preserves_its_existing_error_boundary(tmp_path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "bad",
                    "name": "坏来源",
                    "publisher": "测试",
                    "homepage_url": "https://official.example.edu.cn",
                    "source_type": "unsupported",
                    "category": "测试",
                    "source_tier": "A",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourceCollectionError, match="Invalid source registry"):
        load_source_registry(str(path))


def test_employer_contract_rejects_invalid_parent_alias_and_domain() -> None:
    root = {
        "id": "root",
        "canonical_name": "测试集团",
        "parent_id": None,
        "parent_name": None,
        "category": "测试",
        "employer_type": "测试单位",
        "affiliation": "测试体系",
        "aliases": ["测试集团"],
        "official_domains": ["example.edu.cn"],
    }
    child = {
        **root,
        "id": "child",
        "canonical_name": "测试子公司",
        "parent_id": "missing",
        "parent_name": "不存在集团",
        "aliases": [""],
        "official_domains": ["not a domain"],
    }

    with pytest.raises(ContractValidationError, match="aliases"):
        validate_employer_registry([root, child])

    child["aliases"] = ["测试子公司"]
    with pytest.raises(ContractValidationError, match="official_domains"):
        validate_employer_registry([root, child])

    child["official_domains"] = ["child.example.edu.cn"]
    with pytest.raises(ContractValidationError, match="unknown parent_id"):
        validate_employer_registry([root, child])


def test_artifact_contract_rejects_invalid_hash_and_unmanaged_storage_path() -> None:
    artifact = {
        "source_id": "official-test",
        "parent_url": "https://official.example.edu.cn/notice/1",
        "artifact_url": "https://official.example.edu.cn/files/jobs.pdf",
        "artifact_kind": "position_table",
        "content_sha256": "not-a-hash",
    }
    with pytest.raises(ContractValidationError, match="SHA-256"):
        validate_source_artifact(artifact)

    artifact["content_sha256"] = "a" * 64
    artifact["storage_path"] = "../outside/jobs.pdf"
    with pytest.raises(ContractValidationError, match="relative path"):
        validate_source_artifact(artifact)

    artifact["storage_path"] = "..\\outside\\jobs.pdf"
    with pytest.raises(ContractValidationError, match="relative path"):
        validate_source_artifact(artifact)
