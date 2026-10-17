"""Every scaffold module must be installed by meson.build (no orphans)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_meson_installs_all_sources() -> None:
    build = (ROOT / "meson.build").read_text(encoding="utf-8")
    modules = sorted(
        p.name for p in (ROOT / "src" / "xx_captions").glob("*.py"))
    assert modules, "package must not be empty"
    for module in modules:
        assert f"src/xx_captions/{module}" in build, module
    assert "src/xx_captions/captions.css" in build
    assert "import xx_ui" in build or "xx_ui" in build
    assert "data/xx-captions.in" in build
    assert "data/io.piercingxx.Captions1.service.in" in build
