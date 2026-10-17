import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
# The UI runs against the installed xx_ui package (common/meson.build);
# from source and under tests the shared design system lives in ../common.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'common'))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path_factory, monkeypatch):
    """Never read the developer's real ~/.config/xx-wm theme."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
