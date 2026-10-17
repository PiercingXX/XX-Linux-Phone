"""First-run directories follow XDG and stay per app."""

import os
import stat

import pytest

from xx_music.paths import AppPaths


def test_xdg_dirs_and_leaf_mode():
    paths = AppPaths("xx-radio")
    dirs = paths.ensure_app_dirs()
    assert set(dirs) == {"data", "config", "cache", "state"}
    for path in dirs.values():
        assert path.is_dir()
        assert path.name == "xx-radio"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    assert str(paths.config_dir()).startswith(os.environ["XDG_CONFIG_HOME"])


def test_layout():
    paths = AppPaths("xx-radio")
    assert paths.db_path().parent == paths.data_dir()
    assert paths.offline_dir().parent == paths.data_dir()
    assert paths.engine_dir().parent == paths.data_dir()
    assert paths.audio_cache_dir().parent == paths.cache_dir()
    assert paths.art_cache_dir().parent == paths.cache_dir()
    assert paths.queue_path().parent == paths.state_dir()
    assert paths.log_file().name == "xx-radio.log"


def test_runtime_dir_prefers_xdg_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert AppPaths("xx-radio").runtime_dir() == tmp_path / "xx-radio"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    fallback = AppPaths("xx-radio").runtime_dir()
    assert fallback.parent == AppPaths("xx-radio").cache_dir()


def test_relative_xdg_values_are_ignored(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative/dir")
    assert AppPaths("a").data_dir().is_absolute()


@pytest.mark.parametrize("bad", ["", "a/b", ".hidden"])
def test_bad_dir_names(bad):
    with pytest.raises(ValueError):
        AppPaths(bad)


def test_ensure_parent_for(tmp_path):
    target = tmp_path / "x" / "y" / "file"
    AppPaths.ensure_parent_for(target)
    assert target.parent.is_dir()
