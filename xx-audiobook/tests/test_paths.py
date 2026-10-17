"""First-run directories and app identity."""

import os
import stat

from xx_audiobook import __app_id__, __mpris_bus_name__, __player_bus_name__
from xx_audiobook import paths as paths_module


def test_app_ids():
    assert __app_id__ == "io.piercingxx.XXAudiobook"
    assert __player_bus_name__ == "io.piercingxx.XXAudiobook.Player"
    assert __mpris_bus_name__ == "org.mpris.MediaPlayer2.xx_audiobook"
    assert __player_bus_name__ != __app_id__


def test_first_run_creates_dirs_before_any_file():
    dirs = paths_module.ensure_app_dirs()
    assert set(dirs) == {"data", "config", "cache", "state"}
    for path in dirs.values():
        assert path.is_dir()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


def test_db_config_log_books_covers_layout():
    assert paths_module.db_path().parent == paths_module.data_dir()
    assert paths_module.config_path().parent == paths_module.config_dir()
    assert paths_module.log_file().parent == paths_module.state_dir()
    assert paths_module.books_dir().parent == paths_module.data_dir()
    assert paths_module.covers_dir().parent == paths_module.data_dir()
    assert paths_module.db_path().name == "audiobook.db"


def test_ensure_parent_for_creates_missing_parents(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = paths_module.db_path()
    assert not target.parent.exists()
    paths_module.ensure_parent_for(target)
    assert target.parent.is_dir()
