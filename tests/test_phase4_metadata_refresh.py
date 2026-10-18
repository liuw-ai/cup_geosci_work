from pathlib import Path


def test_phase4_metadata_refresh_wrapper_orders_report_dependencies() -> None:
    content = Path("scripts/refresh_phase4_metadata.py").read_text(encoding="utf-8")
    order = [
        "audit_phase4_current_state.py",
        "audit_phase4_completion.py",
        "audit_phase4_workspace_layout.py",
        "build_phase4_manifest.py",
        "verify_phase4_manifest.py",
    ]
    positions = [content.index(name) for name in order]
    assert positions == sorted(positions)
