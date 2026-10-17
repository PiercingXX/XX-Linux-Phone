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


@pytest.fixture(autouse=True)
def _pin_display_country(monkeypatch):
    """Display tests pin to the passed region: live network/SIM/locale
    resolution is covered by common/tests/test_xx_phone.py."""
    import xx_phone

    monkeypatch.setattr(xx_phone, "user_country", lambda *a, **k: "")
