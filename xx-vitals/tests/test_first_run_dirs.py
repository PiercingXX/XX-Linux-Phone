"""First-run directories: a clean device has no ~/.local/share at all.

Regression test for the postmarketOS first-launch crash (do_activate ->
store init -> sqlite3.connect -> "unable to open database file"). The store
connect creates its parent, startup creates every XDG leaf (mode 0700).
"""

import os
import stat

import pytest

from xx_vitals import paths
from xx_vitals.paths import ensure_app_dirs


@pytest.fixture()
def empty_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_store_init_with_no_local_share_at_all(empty_home):
    from xx_vitals.store.db import Database, DailyMetric, default_db_path

    assert not (empty_home / ".local" / "share").exists()
    assert str(default_db_path()).startswith(str(empty_home))
    db = Database()
    db.init()
    db.upsert_daily(DailyMetric(source_id="manual", date="2026-10-16", steps=10, derived_mask=0))
    assert db.get_daily("manual", "2026-10-16").steps == 10
    assert default_db_path().is_file()


def test_prefs_round_trip_with_no_config_dir(empty_home):
    from xx_vitals import prefs

    assert not (empty_home / ".config").exists()
    cfg = prefs.load()
    assert cfg.units == "imperial"
    cfg.steps_goal = 8000
    prefs.save(cfg)
    assert prefs.load().steps_goal == 8000


def test_startup_creates_every_xdg_leaf_with_mode_0700(empty_home):
    dirs = ensure_app_dirs()
    assert set(dirs) == {"data", "config", "cache", "state"}
    assert dirs["data"] == empty_home / ".local" / "share" / "xx-vitals"
    assert dirs["config"] == empty_home / ".config" / "xx-vitals"
    assert dirs["cache"] == empty_home / ".cache" / "xx-vitals"
    assert dirs["state"] == empty_home / ".local" / "state" / "xx-vitals"
    for path in dirs.values():
        assert path.is_dir()
        assert _mode(path) == 0o700


def test_ensure_is_idempotent_and_repairs_mode(empty_home):
    ensure_app_dirs()
    os.chmod(paths.data_dir(), 0o755)
    ensure_app_dirs()
    assert _mode(paths.data_dir()) == 0o700


def test_log_file_parent_is_the_state_dir(empty_home):
    ensure_app_dirs()
    assert paths.log_file().parent == paths.state_dir()
    assert paths.log_file().parent.is_dir()
