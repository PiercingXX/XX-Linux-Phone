import json

from txxt import theme


def test_all_eight_shell_presets_known():
    assert set(theme.THEME_PRESETS) == {
        'amoled', 'graphite', 'forest', 'ocean',
        'paper', 'mist', 'aura', 'burgundy',
    }


def test_android_preset_keys_alias_to_shell_keys():
    assert theme.normalize_preset_key('amoled-night') == 'amoled'
    assert theme.normalize_preset_key('forest-night') == 'forest'
    assert theme.normalize_preset_key('ocean-drift') == 'ocean'


def test_unknown_preset_falls_back_to_amoled():
    assert theme.normalize_preset_key('nope') == 'amoled'
    assert theme.normalize_preset_key(None) == 'amoled'


def test_missing_shell_config_file_gives_empty_dict(tmp_path):
    assert theme.load_xxwm_config(tmp_path / 'absent.json') == {}


def test_invalid_shell_config_file_gives_empty_dict(tmp_path):
    bad = tmp_path / 'config.json'
    bad.write_text('{not json', encoding='utf-8')
    assert theme.load_xxwm_config(bad) == {}


def test_resolve_theme_reads_shell_config(tmp_path):
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'theme': 'forest'}), encoding='utf-8')
    active = theme.resolve_theme(theme.load_xxwm_config(cfg))
    assert active['key'] == 'forest'
    assert active['background'] == '#10261B'
    assert active['dark'] is True


def test_manual_preset_wins_over_shell_config():
    active = theme.resolve_theme({'theme': 'forest'}, manual_preset='paper')
    assert active['key'] == 'paper'
    assert active['dark'] is False


def test_custom_shell_theme_passes_background_through():
    cfg = {'theme': 'custom', 'custom_background': '#123456'}
    active = theme.resolve_theme(cfg)
    assert active['key'] == 'custom'
    assert active['background'] == '#123456'


def test_resolve_font_matches_shell_families():
    assert theme.resolve_font({'font': 'space-mono'}).startswith('Space Mono')
    assert theme.resolve_font({}) == theme.FONT_FAMILIES['jetbrains-mono-nerd']
    assert theme.resolve_font({'font': 'bogus'}) == theme.FONT_FAMILIES['jetbrains-mono-nerd']


def test_css_carries_background_and_font():
    active = theme.resolve_theme({'theme': 'burgundy'})
    css = theme.css_for_theme(active, 'Space Mono, Monospace')
    assert '#2A1018' in css
    assert 'Space Mono' in css


def test_every_preset_resolves_with_own_ground():
    for key in (
        'amoled', 'graphite', 'forest', 'ocean',
        'paper', 'mist', 'aura', 'burgundy',
    ):
        active = theme.resolve_theme({'theme': key})
        assert active['key'] == key
        assert active['background'] == theme.THEME_PRESETS[key]['background']


def test_missing_shell_config_falls_back_to_amoled():
    active = theme.resolve_theme(theme.load_xxwm_config('/definitely/absent.json'))
    assert active['key'] == 'amoled'


def test_manual_font_family_mapping():
    assert theme.manual_font_family('space-mono').startswith('Space Mono')
    assert theme.manual_font_family('jetbrains-mono').startswith('JetBrains Mono')
    assert theme.manual_font_family('system') == 'Sans Light'
    assert theme.manual_font_family('bogus') == theme.manual_font_family('space-mono')


def test_directory_watch_fires_on_config_edit(tmp_path):
    from gi.repository import GLib

    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'theme': 'forest'}), encoding='utf-8')
    seen = []
    monitor = theme.watch_xxwm_config(seen.append, path=cfg)
    assert monitor is not None
    cfg.write_text(json.dumps({'theme': 'ocean'}), encoding='utf-8')
    context = GLib.MainContext.default()
    end = __import__('time').monotonic() + 5
    while not seen and __import__('time').monotonic() < end:
        context.iteration(True)
    assert seen and seen[-1].get('theme') == 'ocean'


def test_directory_watch_ignores_sibling_files(tmp_path):
    from gi.repository import GLib

    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'theme': 'forest'}), encoding='utf-8')
    seen = []
    monitor = theme.watch_xxwm_config(seen.append, path=cfg)
    assert monitor is not None
    (tmp_path / 'other.json').write_text('{}', encoding='utf-8')
    context = GLib.MainContext.default()
    for _ in range(20):
        context.iteration(False)
    assert seen == []


def test_directory_watch_missing_dir_returns_none(tmp_path):
    assert theme.watch_xxwm_config(lambda cfg: None, path=tmp_path / 'nope' / 'config.json') is None
