"""The reusable core stays source-agnostic and app-agnostic.

xx_music must never import a music-source library or the xx_radio app,
so another app can reuse it with a different engine. Checked on the
source text (AST), so a lazy import inside a function is caught too.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
FORBIDDEN = ("xx_radio", "ytmusicapi", "yt_dlp", "yt_dlp_plugins", "requests")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_core_imports_no_source_library_and_not_the_app():
    files = sorted((SRC / "xx_music").rglob("*.py"))
    assert files
    for path in files:
        for name in _imports(path):
            top = name.split(".")[0]
            assert top not in FORBIDDEN, f"{path.name} imports {name}"


def test_youtube_code_lives_only_under_the_youtube_engine():
    for path in sorted(SRC.rglob("*.py")):
        if "youtube" in path.parts:
            continue
        for name in _imports(path):
            assert name.split(".")[0] not in ("ytmusicapi", "yt_dlp"), path


def test_core_text_names_no_particular_service():
    for path in sorted((SRC / "xx_music").rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        assert "youtube" not in text, path.name
