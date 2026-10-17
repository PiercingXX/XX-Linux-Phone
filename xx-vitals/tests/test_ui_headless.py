"""Headless UI contract: thin widgets import without a display, long lists
use Gtk.ListView, entry uses Adw dialogs, layout stays in the 320–360 px
budget, and no UI path blocks the main loop."""

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_vitals"


def test_ui_modules_import_headless():
    from xx_vitals.ui import browse, entry, home, journal, profile, shell

    assert callable(entry.validate_weight_input)
    assert callable(entry.validate_activity_input)
    assert callable(entry.submit_weight)
    assert callable(entry.submit_activity)
    assert callable(entry.build_weight_dialog)
    assert callable(entry.build_activity_dialog)
    assert callable(home.day_summary)
    assert callable(home.build_home_page)
    assert callable(journal.journal_rows)
    assert callable(journal.gap_band_text)
    assert callable(browse.visible_rows)
    assert callable(browse.detail_series)
    assert callable(profile.apply_goal_edits)
    assert callable(profile.sync_lines)
    assert callable(profile.save_nas_host)
    assert callable(profile.import_takeout)
    assert callable(shell.visible_tabs)
    assert callable(shell.build_window)


def test_long_lists_use_list_view_and_entry_uses_dialogs():
    journal_src = (SRC / "ui" / "journal.py").read_text(encoding="utf-8")
    browse_src = (SRC / "ui" / "browse.py").read_text(encoding="utf-8")
    profile_src = (SRC / "ui" / "profile.py").read_text(encoding="utf-8")
    entry_src = (SRC / "ui" / "entry.py").read_text(encoding="utf-8")
    assert "Gtk.ListView" in journal_src
    assert "Gtk.ListView" in browse_src
    assert "Gtk.ListView" in profile_src
    assert "Adw.Dialog" in entry_src
    assert "PreferencesWindow" not in profile_src


def test_layout_uses_design_system_pages():
    # Phone fit comes from xx_ui builders (capped widths, fixed header plus
    # scrolling body plus bottom tabs); pages must assemble through them.
    import re

    shell_src = (SRC / "ui" / "shell.py").read_text(encoding="utf-8")
    assert "widgets.bottom_tabs" in shell_src
    assert "set_decorated(False)" in shell_src
    expectations = {
        "home.py": ("widgets.page_header", "widgets.page", "widgets.fab"),
        "journal.py": ("widgets.page_header", "widgets.page", "Gtk.ListView"),
        "browse.py": ("widgets.page_header", "widgets.page", "Gtk.ListView"),
        "profile.py": ("widgets.page_header", "widgets.page", "Gtk.ListView"),
        "entry.py": ("widgets.dialog_box", "Adw.Dialog"),
    }
    for name, markers in expectations.items():
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in src, (name, marker)
    for name in ("home.py", "journal.py", "browse.py", "profile.py"):
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        assert re.search(r"xx-page-body|page_header|compact_header", src), name


def test_ui_never_blocks_the_main_loop():
    banned = ("subprocess.run", "subprocess.check_output", "time.sleep", "call_sync")
    for name in ("entry.py", "home.py", "journal.py", "browse.py", "profile.py", "shell.py"):
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, (name, token)


def test_journal_filter_chips_fit_phone_width():
    from xx_vitals.ui import journal

    pairs = list(journal.FILTERS)
    assert [value for _, value in pairs] == [
        "all", "walk", "run", "cycle", "other"]
    assert all(len(label) <= 4 for label, _ in pairs)
    src = (SRC / "ui" / "journal.py").read_text(encoding="utf-8")
    assert "spacing=4" in src


def test_visible_tabs_home_always_visible():
    from xx_vitals import prefs
    from xx_vitals.ui import shell

    cfg = prefs.AppConfig(show_journal_tab=False, show_browse_tab=False, show_profile_tab=False)
    assert shell.visible_tabs(cfg) == ["home"]
    assert shell.visible_tabs(prefs.AppConfig()) == ["home", "journal", "browse", "profile"]


def test_profile_goal_edits_apply_and_reject():
    from xx_vitals import prefs
    from xx_vitals.ui import profile

    cfg = prefs.AppConfig()
    assert profile.apply_goal_edits(cfg, steps_raw="12000") == {}
    assert cfg.steps_goal == 12000
    errors = profile.apply_goal_edits(cfg, steps_raw="0")
    assert "steps" in errors and cfg.steps_goal == 12000
    cfg.units = "metric"
    assert profile.apply_height_edit(cfg, "1.78") is None
    assert cfg.height_metres == 1.78
    assert profile.apply_height_edit(cfg, "0.3") is not None
    cfg.units = "imperial"
    assert profile.apply_height_edit(cfg, "5'9") is None


def test_worker_runs_off_thread_without_blocking_caller():
    import threading

    from xx_vitals.sync import worker

    done = threading.Event()

    def work():
        done.set()
        return 7

    thread = worker.run_in_background(work)
    assert isinstance(thread, threading.Thread)
    assert done.wait(timeout=10) is True
    thread.join(timeout=10)
