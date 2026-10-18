"""Refresh Phase 4 audit reports in dependency order.

The current-state, completion, and workspace reports are generated first;
the release manifest is generated last because it hashes those reports.  This
small wrapper prevents a common audit error where a report is refreshed but
the manifest still points at its previous hash.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run(script: str) -> None:
    command = [sys.executable, str(ROOT / "scripts" / script)]
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    for script in (
        "audit_phase4_current_state.py",
        "audit_phase4_completion.py",
        "audit_phase4_workspace_layout.py",
        "build_phase4_manifest.py",
    ):
        _run(script)
    _run("verify_phase4_manifest.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
