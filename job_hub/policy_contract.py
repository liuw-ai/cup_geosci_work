"""Fingerprint the code and taxonomy that decide student publication."""

from __future__ import annotations

import hashlib
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_POLICY_FILES = (
    "job_hub/profiles.py",
    "job_hub/pipeline.py",
    "job_hub/db.py",
    "job_hub/matching.py",
    "job_hub/major_taxonomy.py",
    "job_hub/locations.py",
    "data/major_taxonomy.json",
)


def publication_policy_digest() -> str:
    digest = hashlib.sha256()
    for relative_path in _POLICY_FILES:
        digest.update(relative_path.encode("ascii"))
        digest.update(b"\0")
        digest.update((_ROOT / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
