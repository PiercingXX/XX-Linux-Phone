"""Theme adoption: the app uses common/xx_ui, not its own theme copy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "xx_calendar"


def test_no_vendored_theme_reader():
    assert not (SRC / "theme.py").exists()
    main = (SRC / "main.py").read_text()
    assert "from xx_calendar.theme import" not in main
    assert "css_for_theme" not in main
    assert "ThemeWatcher" not in main
    assert "xx_ui.apply(" in main
    assert "xx_ui.on_change(" in main


def test_app_id_lives_in_main():
    from xx_calendar.main import APP_ID

    assert APP_ID == "io.piercingxx.XXCalendar"


def test_xx_ui_covers_all_themes():
    from xx_ui import preset_keys
    from xx_ui import theme as xx_theme

    assert set(preset_keys()) == {
        "amoled", "graphite", "forest", "ocean",
        "paper", "mist", "aura", "burgundy",
    }
    assert xx_theme.THEME_PRESETS["amoled"].background == "#000000"
    assert xx_theme.THEME_PRESETS["paper"].foreground == "#151515"


def test_app_css_uses_tokens_only():
    text = (SRC / "calendar.css").read_text(encoding="utf-8")
    assert "#" not in text
    assert "xxc-" in text


def test_month_grid_reads_theme_and_redraws():
    source = (SRC / "month_grid.py").read_text()
    assert "xx_ui.theme.load()" in source
    assert "xx_ui.on_change" in source
    assert "queue_draw" in source
