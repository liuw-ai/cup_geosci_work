from __future__ import annotations

from dataclasses import dataclass

from job_hub.attachments import AttachmentProcessingError, AttachmentResult, process_pending_attachments


@dataclass
class FakeDatabase:
    artifacts: list[dict[str, object]]

    def list_source_artifacts(
        self,
        *,
        limit: int,
        oldest_first: bool,
        extraction_statuses: set[str],
    ) -> list[dict[str, object]]:
        assert oldest_first is True
        assert extraction_statuses
        return self.artifacts[:limit]


class FakeProcessor:
    def __init__(self, outcomes: dict[int, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[int] = []

    def process(self, artifact_id: int) -> AttachmentResult:
        self.calls.append(artifact_id)
        outcome = self.outcomes[artifact_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_batch_processes_registered_and_downloaded_rows_without_retrying_failed() -> None:
    database = FakeDatabase(
        [
            {"id": 3, "source_id": "source-c", "extraction_status": "registered"},
            {"id": 2, "source_id": "source-b", "extraction_status": "downloaded"},
            {"id": 1, "source_id": "source-a", "extraction_status": "failed"},
            {"id": 4, "source_id": "source-d", "extraction_status": "extracted"},
        ]
    )
    processor = FakeProcessor(
        {
            3: AttachmentResult(3, "extracted", rows_extracted=4, candidates_created=1),
            2: AttachmentResult(2, "skipped", detail="robots denied"),
        }
    )

    summary = process_pending_attachments(database, processor, limit=2)

    assert processor.calls == [3, 2]
    assert summary["selected"] == 2
    assert summary["extracted"] == 1
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert summary["rows_extracted"] == 4
    assert summary["candidates_created"] == 1


def test_batch_retry_failed_is_explicit_and_keeps_errors_auditable() -> None:
    database = FakeDatabase(
        [
            {"id": 1, "source_id": "source-a", "extraction_status": "failed"},
            {"id": 2, "source_id": "source-b", "extraction_status": "skipped"},
        ]
    )
    processor = FakeProcessor({1: AttachmentProcessingError("temporary HTTP failure")})

    summary = process_pending_attachments(
        database,
        processor,
        retry_failed=True,
        source_ids={"source-a"},
    )

    assert processor.calls == [1]
    assert summary["retry_failed"] is True
    assert summary["processed"] == 1
    assert summary["failed"] == 1
    assert summary["items"] == [
        {
            "artifact_id": 1,
            "source_id": "source-a",
            "status": "failed",
            "error": "temporary HTTP failure",
        }
    ]


def test_batch_excludes_manifest_artifacts_with_non_automatic_policy() -> None:
    database = FakeDatabase(
        [
            {
                "id": 1,
                "source_id": "source-a",
                "extraction_status": "registered",
                "metadata": {
                    "government_artifact_id": "manual-source",
                    "manifest_status": "manual_verified",
                },
            },
            {
                "id": 2,
                "source_id": "source-b",
                "extraction_status": "failed",
                "metadata": {
                    "government_artifact_id": "student-scope-excluded",
                    "manifest_status": "current_non_student_eligible",
                },
            },
            {"id": 3, "source_id": "source-c", "extraction_status": "registered"},
        ]
    )
    processor = FakeProcessor({3: AttachmentResult(3, "extracted")})

    summary = process_pending_attachments(database, processor, retry_failed=True)

    assert processor.calls == [3]
    assert summary["selected"] == 1
    assert summary["policy_skipped"] == 2
    assert {item["artifact_id"] for item in summary["policy_skip_items"]} == {1, 2}


def test_batch_reparses_only_extracted_artifacts_with_stale_parser() -> None:
    database = FakeDatabase(
        [
            {
                "id": 4,
                "source_id": "source-d",
                "extraction_status": "extracted",
                "parser_version": "attachments-v3",
            },
            {
                "id": 5,
                "source_id": "source-e",
                "extraction_status": "extracted",
                "parser_version": "attachments-v4",
            },
        ]
    )
    processor = FakeProcessor({4: AttachmentResult(4, "extracted", rows_extracted=3)})

    summary = process_pending_attachments(
        database,
        processor,
        include_stale_extracted=True,
    )

    assert processor.calls == [4]
    assert summary["reparsed"] == 1
    assert summary["processed"] == 1
