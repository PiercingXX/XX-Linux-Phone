"""Backup-exclusion documentation names every store and its posture."""

from pathlib import Path

DOCS = Path(__file__).parent.parent / "docs"


def test_backup_doc_exists_and_covers_all_stores():
    text = (DOCS / "backup.md").read_text(encoding="utf-8")
    for path in (
        "~/.local/share/xx-vitals/vitals.db",
        "~/.config/xx-vitals/config.json",
        "~/.local/state/xx-vitals/xx-vitals.log",
        "~/.cache/xx-vitals/",
    ):
        assert path in text
    assert "allowBackup" in text
    assert "io.piercingxx.XXVitals" in text
    assert "sign in again" in text


def test_config_doc_points_at_backup_doc():
    text = (DOCS / "config.md").read_text(encoding="utf-8")
    assert "backup.md" in text
