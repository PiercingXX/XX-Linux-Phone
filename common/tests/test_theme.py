"""Config parsing, fallbacks, presets, rendering, custom derivation."""

import re

import pytest

from xx_ui import css, theme
from xx_ui.theme import (
    DANGER_RED,
    FONT_FAMILIES,
    THEME_PRESETS,
    _derive_shades,
    load,
    preset_keys,
    resolve_scale,
)

from conftest import write_config


def test_missing_config_falls_back(isolated_home):
    active = load()
    assert active.key == 'amoled'
    assert active.background == '#000000'
    assert active.font_family == FONT_FAMILIES['jetbrains-mono-nerd']
    assert active.scale == 1.0
    assert active.dark is True


def test_invalid_json_falls_back(isolated_home):
    path = isolated_home / '.config' / 'xx-wm' / 'config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{nope', encoding='utf-8')
    assert load().key == 'amoled'


def test_non_dict_json_falls_back(isolated_home):
    write_config(isolated_home, [1, 2, 3])
    assert load().key == 'amoled'


def test_unknown_theme_key_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 'neon'})
    assert load().key == 'amoled'


def test_non_string_theme_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 42})
    assert load().key == 'amoled'


def test_xdg_config_home_respected(tmp_path, monkeypatch):
    other = tmp_path / 'xdg'
    (other / 'xx-wm').mkdir(parents=True)
    import json
    (other / 'xx-wm' / 'config.json').write_text(
        json.dumps({'theme': 'forest'}), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(other))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    assert load().key == 'forest'


def test_every_preset_loads(isolated_home):
    for key, preset in THEME_PRESETS.items():
        write_config(isolated_home, {'theme': key})
        active = load()
        assert active.key == key
        assert active.background == preset.background
        assert active.surface == preset.surface
        assert active.surface_alt == preset.surface_alt
        assert active.border == preset.border
        assert active.foreground == preset.foreground
        assert active.muted == preset.muted
        assert active.accent == preset.accent


def test_preset_keys():
    assert preset_keys() == ['amoled', 'graphite', 'forest', 'ocean',
                             'paper', 'mist', 'aura', 'burgundy']


def test_light_dark_flags(isolated_home):
    for key in ('amoled', 'graphite', 'forest', 'ocean', 'aura', 'burgundy'):
        write_config(isolated_home, {'theme': key})
        assert load().dark is True, key
    for key in ('paper', 'mist'):
        write_config(isolated_home, {'theme': key})
        assert load().dark is False, key


def test_every_preset_renders_without_placeholders(isolated_home):
    for key in preset_keys():
        write_config(isolated_home, {'theme': key})
        out = css.render(load())
        assert '${' not in out, key
        assert key and THEME_PRESETS[key].background in out
        assert THEME_PRESETS[key].foreground in out


def test_render_defines_libadwaita_colors(isolated_home):
    out = css.render(load())
    for name in ('window_bg_color', 'view_bg_color', 'accent_bg_color',
                 'accent_color', 'card_bg_color', 'headerbar_bg_color',
                 'popover_bg_color', 'dialog_bg_color', '--window-bg-color'):
        assert name in out


def test_render_has_no_libadwaita_blue(isolated_home):
    for key in preset_keys():
        write_config(isolated_home, {'theme': key})
        out = css.render(load()).lower()
        assert '#3584e4' not in out, key
        assert '#1c71d8' not in out, key
        assert '#62a0ea' not in out, key


def test_render_uses_danger_red(isolated_home):
    assert DANGER_RED in css.render(load())


def test_font_keys(isolated_home):
    for key, family in FONT_FAMILIES.items():
        write_config(isolated_home, {'font': key})
        assert load().font_family == family, key


def test_invalid_font_falls_back(isolated_home):
    write_config(isolated_home, {'font': 'comic-sans'})
    assert load().font_family == FONT_FAMILIES['jetbrains-mono-nerd']


def test_custom_font_family(isolated_home):
    write_config(isolated_home, {'font': 'custom',
                                 'custom_font_family': 'My Font'})
    assert load().font_family == 'My Font'


def test_custom_font_without_family_falls_back(isolated_home):
    write_config(isolated_home, {'font': 'custom'})
    assert load().font_family == FONT_FAMILIES['jetbrains-mono-nerd']


def test_font_family_sanitized_for_css():
    assert "'" not in css.sanitize_font_family("a'b;c{d}")
    assert css.sanitize_font_family('') != ''


def test_scale_clamped(isolated_home):
    assert resolve_scale({'text_size_scale': 3.0}) == 2.0
    assert resolve_scale({'text_size_scale': 0.1}) == 0.5
    assert resolve_scale({'text_size_scale': 'junk'}) == 1.0
    assert resolve_scale({}) == 1.0
    write_config(isolated_home, {'text_size_scale': 2.0})
    assert load().scale == 2.0


def test_scale_changes_rendered_sizes(isolated_home):
    write_config(isolated_home, {'text_size_scale': 1.0})
    base = css.render(load())
    write_config(isolated_home, {'text_size_scale': 2.0})
    big = css.render(load())
    assert 'font-size: 30px' in base
    assert 'font-size: 60px' in big


def test_custom_dark_derivation(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#3a2a10'})
    active = load()
    assert active.key == 'custom'
    assert active.background == '#3a2a10'
    assert (active.surface, active.surface_alt, active.border) == \
        _derive_shades('#3a2a10')
    assert active.dark is True
    assert active.foreground == THEME_PRESETS['amoled'].foreground
    assert active.muted == THEME_PRESETS['amoled'].muted


def test_custom_light_derivation(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#F3EEE2'})
    active = load()
    assert active.dark is False
    assert active.foreground == THEME_PRESETS['paper'].foreground


def test_custom_contrast(isolated_home):
    def luminance(hex_color: str) -> float:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255
    for bg in ('#3a2a10', '#000000', '#10261B', '#F3EEE2', '#E6EDF5',
               '#7a1010', '#104010'):
        write_config(isolated_home, {'theme': 'custom',
                                     'custom_background': bg})
        active = load()
        gap = abs(luminance(active.foreground) - luminance(active.background))
        assert gap > 0.5, (bg, active.foreground)


def test_custom_invalid_color_falls_back(isolated_home):
    for bad in ('', 'red', '#fff', '#gggggg', None, '#3a2a10\n#000000'):
        write_config(isolated_home, {'theme': 'custom',
                                     'custom_background': bad})
        assert load().key == 'amoled', bad


def test_custom_missing_color_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 'custom'})
    assert load().key == 'amoled'


def test_render_custom_has_no_placeholders(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#3a2a10'})
    out = css.render(load())
    assert '${' not in out
    assert '#3a2a10' in out
    assert re.search(r'#[0-9a-f]{6}', out)


def test_shell_parity_derive_shades():
    # Same rule as xx-wm config._derive_shades: near-black keeps amoled trio.
    assert _derive_shades('#000000') == ('#111111', '#181818', '#2f2f2f')
    assert _derive_shades('junk') == ('#111111', '#181818', '#2f2f2f')
    assert _derive_shades('#3a2a10') == ('#2f220d', '#291e0b', '#1f1708')


def test_on_change_roundtrip():
    seen = []
    off = css.on_change(seen.append)
    assert callable(off)
    off()
    assert seen == []


def test_config_event_triggers_debounced_reload(isolated_home):
    from gi.repository import Gio, GLib
    write_config(isolated_home, {'theme': 'ocean'})
    seen = []
    off = css.on_change(seen.append)
    try:
        child = Gio.File.new_for_path(str(theme.config_path()))
        assert css._debounce_id == 0
        css._on_config_event(
            None, child, None, Gio.FileMonitorEvent.CHANGED)
        loop = GLib.MainLoop()
        GLib.timeout_add(600, loop.quit)
        loop.run()
    finally:
        off()
    assert seen and seen[-1].key == 'ocean'


def test_config_event_ignores_other_files(isolated_home):
    from gi.repository import Gio
    other = isolated_home / '.config' / 'xx-wm' / 'other.json'
    child = Gio.File.new_for_path(str(other))
    css._on_config_event(None, child, None, Gio.FileMonitorEvent.CHANGED)
    css._on_config_event(
        None, Gio.File.new_for_path(str(theme.config_path())), None,
        Gio.FileMonitorEvent.ATTRIBUTE_CHANGED)
    assert css._debounce_id == 0


@pytest.mark.parametrize('key', list(THEME_PRESETS) + ['custom-missing'])
def test_never_raises(isolated_home, key, tmp_path):
    if key == 'custom-missing':
        write_config(isolated_home, {'theme': 'custom'})
    else:
        write_config(isolated_home, {'theme': key})
    active = load(path=tmp_path / 'does-not-exist.json')
    assert active.key == 'amoled'
