from pathlib import Path


def test_phase4_metadata_refresh_wrapper_orders_report_dependencies() -> None:
    content = Path("scripts/refresh_phase4_metadata.py").read_text(encoding="utf-8")
    current = content.index("audit_phase4_current_state.py")
    workspace = content.index("audit_phase4_workspace_layout.py")
    manifest = content.index("build_phase4_manifest.py")
    completion = content.index("audit_phase4_completion.py")
    verify = content.index("verify_phase4_manifest.py")

    # The first manifest establishes the clean boundary consumed by the
    # completion audit; the second manifest binds the final completion hash.
    assert current < workspace < manifest < completion < verify
    assert content.count('"build_phase4_manifest.py"') == 2
