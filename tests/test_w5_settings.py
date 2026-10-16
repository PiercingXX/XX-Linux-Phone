"""W5 settings UIs: battery standby, app permissions, smart lock.

Headless: backends run against the real PyGObject (imported before any
fake-gi fixture swaps the bindings); section widgets build against
recorder fakes (the test_w2_settings idiom), and bare namespaces drive
build/refresh/pick flows. Static tests pin the registry, meson set,
and the no-blocking-IO rules for the new files.
"""
import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import app_permissions  # noqa: E402
import app_standby  # noqa: E402
import config as config_mod  # noqa: E402
import privacy_monitor  # noqa: E402
import settings_page  # noqa: E402
import settings_permissions  # noqa: E402
import settings_smart_lock  # noqa: E402
import settings_standby  # noqa: E402
import smart_lock  # noqa: E402
import system_settings  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
NEW_FILES = ('settings_standby.py', 'settings_permissions.py',
             'settings_smart_lock.py')


# ------------------------------------------------------------------
# Backend: standby built-ins + saver threshold
# ------------------------------------------------------------------

class TestBuiltinExemptions:
    def test_ids_match_without_desktop_and_case(self):
        assert app_standby.is_builtin_exempt('xx-dialer.desktop') is True
        assert app_standby.is_builtin_exempt('ORG.GNOME.CALLS') is True
        assert app_standby.is_builtin_exempt('Chatty') is True
        assert app_standby.is_builtin_exempt('org.gnome.clocks.desktop') is True
        assert app_standby.is_builtin_exempt('xx-txtt') is True
        assert app_standby.is_builtin_exempt('org.mozilla.firefox') is False
        assert app_standby.is_builtin_exempt('') is False

    def test_labels_name_the_five_surfaces(self):
        assert app_standby.builtin_exemption_labels() == [
            'Dialer', 'TxxT daemon', 'Calls', 'Clock & alarms',
            'Media playing']

    def test_policy_never_freezes_builtins(self):
        for app_id in ('xx-clock.desktop', 'chatty', 'xx-dialer',
                       'org.gnome.calls'):
            assert app_standby.should_standby(
                app_id, now=1000.0, last_active=0.0, screen_blanked=False,
                blank_since=None, standby_minutes=10) is False
        assert app_standby.should_standby(
            'org.example.App.desktop', now=1000.0, last_active=0.0,
            screen_blanked=False, blank_since=None,
            standby_minutes=10) is True


class TestSaverThreshold:
    def _ctl(self, **overrides):
        data = dict(app_standby_minutes=10, standby_exempt=[],
                    battery_saver=False, battery_saver_auto=True,
                    battery_saver_auto_pct=15)
        data.update(overrides)
        cfg = SimpleNamespace(**data)
        return app_standby.StandbyController(
            config=cfg, session_bus_factory=lambda: None,
            system_bus_factory=lambda: None, force_degraded=True)

    def test_default_is_fifteen(self):
        assert app_standby.saver_auto_threshold(object()) == 15
        assert app_standby.saver_auto_threshold(
            SimpleNamespace()) == 15

    def test_custom_and_clamped(self):
        assert app_standby.saver_auto_threshold(
            SimpleNamespace(battery_saver_auto_pct=25)) == 25
        assert app_standby.saver_auto_threshold(
            SimpleNamespace(battery_saver_auto_pct=0)) == 1
        assert app_standby.saver_auto_threshold(
            SimpleNamespace(battery_saver_auto_pct=500)) == 100
        assert app_standby.saver_auto_threshold(
            SimpleNamespace(battery_saver_auto_pct='junk')) == 15

    def test_custom_threshold_engages(self):
        ctl = self._ctl(battery_saver_auto_pct=25)
        ctl._battery = (24.0, False)
        ctl._update_saver_auto()
        assert ctl._auto_saver is True
        defaulted = self._ctl()
        defaulted._battery = (24.0, False)
        defaulted._update_saver_auto()
        assert defaulted._auto_saver is False

    def test_clear_stays_above_threshold(self):
        ctl = self._ctl(battery_saver_auto_pct=25)
        ctl._battery = (24.0, False)
        ctl._update_saver_auto()
        assert ctl._auto_saver is True
        ctl._battery = (31.0, False)
        ctl._update_saver_auto()
        assert ctl._auto_saver is False


# ------------------------------------------------------------------
# Backend: config keys
# ------------------------------------------------------------------

@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


class TestW5ConfigKeys:
    def test_defaults_present(self):
        assert config_mod.DEFAULT_CONFIG['battery_saver_auto_pct'] == 15
        assert config_mod.DEFAULT_CONFIG['privacy_indicators'] is True

    def test_threshold_round_trip(self, _isolated_home):
        cfg = config_mod.ShellConfig()
        assert cfg.battery_saver_auto_pct == 15
        cfg.set_battery_saver_auto_pct(25)
        assert cfg.battery_saver_auto_pct == 25

    def test_threshold_clamps(self, _isolated_home):
        cfg = config_mod.ShellConfig()
        cfg.set_battery_saver_auto_pct(0)
        assert cfg.battery_saver_auto_pct == 1
        cfg.set_battery_saver_auto_pct(999)
        assert cfg.battery_saver_auto_pct == 100

    def test_threshold_corrupt_reads_default(self, _isolated_home):
        cfg = config_mod.ShellConfig()
        cfg.data['battery_saver_auto_pct'] = 'junk'
        assert cfg.battery_saver_auto_pct == 15

    def test_indicators_round_trip(self, _isolated_home):
        cfg = config_mod.ShellConfig()
        assert cfg.privacy_indicators is True
        cfg.set_privacy_indicators(False)
        assert cfg.privacy_indicators is False

    def test_indicators_corrupt_reads_shown(self, _isolated_home):
        cfg = config_mod.ShellConfig()
        cfg.data['privacy_indicators'] = 'yes'
        assert cfg.privacy_indicators is True


# ------------------------------------------------------------------
# Backend: privacy usage log
# ------------------------------------------------------------------

def _log(tmp_path, **kwargs):
    kwargs.setdefault('path', tmp_path / 'usage.json')
    return privacy_monitor.UsageLog(**kwargs)


class TestUsageLog:
    def test_note_and_recent_newest_first(self, tmp_path):
        log = _log(tmp_path)
        assert log.note_snapshot(
            {'mic': {'Calls'}, 'camera': set(), 'location': set()},
            now=1000.0) is True
        assert log.note_snapshot(
            {'mic': set(), 'camera': {'Camera'}, 'location': {'Maps'}},
            now=1100.0) is True
        recent = log.recent(now=1200.0)
        assert [(e['kind'], e['app']) for e in recent] == [
            ('camera', 'Camera'), ('location', 'Maps'), ('mic', 'Calls')]

    def test_empty_snapshot_records_nothing(self, tmp_path):
        log = _log(tmp_path)
        assert log.note_snapshot(
            {'mic': set(), 'camera': set(), 'location': set()},
            now=1000.0) is False
        assert log.recent(now=1000.0) == []

    def test_window_drops_old_entries(self, tmp_path):
        log = _log(tmp_path)
        log.note_snapshot({'mic': {'Old'}}, now=0.0)
        log.note_snapshot({'mic': {'New'}}, now=100.0)
        assert [e['app'] for e in log.recent(now=100.0)] == ['New', 'Old']
        assert [e['app'] for e in log.recent(
            now=privacy_monitor.RECENT_USE_WINDOW_S + 100.0)] == ['New']

    def test_persists_and_reloads(self, tmp_path):
        first = _log(tmp_path)
        first.note_snapshot({'mic': {'Calls'}}, now=500.0)
        second = _log(tmp_path)
        assert [(e['kind'], e['app']) for e in second.recent(now=600.0)] == [
            ('mic', 'Calls')]

    def test_corrupt_file_reads_empty(self, tmp_path):
        path = tmp_path / 'usage.json'
        path.write_text('{not json', encoding='utf-8')
        assert _log(tmp_path).recent(now=1.0) == []

    def test_bad_entries_dropped(self, tmp_path):
        log = _log(tmp_path)
        log.from_dict({'entries': [
            {'kind': 'mic', 'app': 'Calls', 'at': 10.0},
            {'kind': 'nfc', 'app': 'Nope', 'at': 10.0},
            {'kind': 'mic', 'app': '', 'at': 10.0},
            'junk',
        ]})
        assert [(e['kind'], e['app']) for e in log.recent(now=20.0)] == [
            ('mic', 'Calls')]

    def test_indicators_switch(self):
        assert privacy_monitor.indicators_enabled(object()) is True
        assert privacy_monitor.indicators_enabled(
            SimpleNamespace(privacy_indicators=False)) is False
        assert privacy_monitor.indicators_enabled(
            SimpleNamespace(privacy_indicators='yes')) is True


def _pw_node(nid, media_class='Stream/Input/Audio', name='Calls',
             state='running'):
    return {'id': nid, 'type': 'PipeWire:Interface:Node',
            'info': {'state': state, 'props': {
                'media.class': media_class, 'application.name': name}}}


class TestMonitorFeedsLog:
    def test_live_use_is_logged(self, tmp_path):
        log = _log(tmp_path)
        monitor = privacy_monitor.PrivacyMonitor(usage_log=log)
        monitor.feed_data(json.dumps([_pw_node(1)]))
        recent = log.recent()
        assert [(e['kind'], e['app']) for e in recent] == [('mic', 'Calls')]

    def test_no_log_without_use(self, tmp_path):
        log = _log(tmp_path)
        monitor = privacy_monitor.PrivacyMonitor(usage_log=log)
        monitor.feed_data(json.dumps([]))
        assert log.recent() == []

    def test_log_failure_never_breaks_emit(self, tmp_path):
        seen = []
        monitor = privacy_monitor.PrivacyMonitor(
            on_change=seen.append, usage_log=object())
        monitor.feed_data(json.dumps([_pw_node(1)]))
        assert seen and seen[-1]['mic'] == {'Calls'}


# ------------------------------------------------------------------
# Backend: flatpak detection + saved wifi + on-body
# ------------------------------------------------------------------

class TestFlatpakDetection:
    def test_exports_paths_are_flatpak(self):
        assert app_permissions.is_flatpak_desktop_path(
            '/var/lib/flatpak/exports/share/applications/org.gimp.xml') is True
        home = '/home/u/.local/share/flatpak/exports/share/applications/x.desktop'
        assert app_permissions.is_flatpak_desktop_path(home) is True

    def test_system_paths_are_native(self):
        assert app_permissions.is_flatpak_desktop_path(
            '/usr/share/applications/firefox.desktop') is False
        assert app_permissions.is_flatpak_desktop_path('') is False
        assert app_permissions.is_flatpak_desktop_path(None) is False

    def test_entry_reads_desktop_file(self):
        flat = SimpleNamespace(
            desktop_app=SimpleNamespace(get_filename=lambda:
                                        '/var/lib/flatpak/exports/share/x.desktop'))
        native = SimpleNamespace(
            desktop_app=SimpleNamespace(get_filename=lambda:
                                        '/usr/share/applications/x.desktop'))
        broken = SimpleNamespace(desktop_app=None)
        assert app_permissions.app_is_flatpak(flat) is True
        assert app_permissions.app_is_flatpak(native) is False
        assert app_permissions.app_is_flatpak(broken) is False


class TestSavedWifi:
    def _run(self, stdout, returncode=0):
        def _fake(cmd, timeout=None):
            assert cmd[:3] == ['nmcli', '-t', '-f']
            return SimpleNamespace(returncode=returncode, stdout=stdout)
        return _fake

    def test_only_wifi_profiles(self):
        run = self._run('Home:wifi\nWork:802-11-wireless\nmodem:gsm\n'
                        'wired:ethernet\n')
        assert system_settings.saved_wifi_ssids(run=run) == ['Home', 'Work']

    def test_escaped_colons_and_sort(self):
        run = self._run('b\\:net:wifi\na-net:wifi\n')
        assert system_settings.saved_wifi_ssids(run=run) == ['a-net', 'b:net']

    def test_unavailable_is_none(self):
        assert system_settings.saved_wifi_ssids(
            run=self._run('', returncode=1)) is None

        def _boom(cmd, timeout=None):
            raise OSError('no nmcli')
        assert system_settings.saved_wifi_ssids(run=_boom) is None


class TestOnBody:
    def test_not_supported(self):
        assert smart_lock.ON_BODY_SUPPORTED is False
        assert smart_lock.on_body_supported() is False


# ------------------------------------------------------------------
# Widget harness (recorder fakes, the test_w2_settings idiom)
# ------------------------------------------------------------------

class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.props = {}
        self.handlers = {}

    def append(self, child):
        self.children.append(child)
        try:
            child._parent = self
        except AttributeError:
            pass

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        parent = getattr(self, '_parent', None)
        if parent is None:
            return None
        siblings = parent.children
        try:
            index = siblings.index(self)
        except ValueError:
            return None
        return siblings[index + 1] if index + 1 < len(siblings) else None

    def set_child(self, child):
        self.child = child

    def get_child(self):
        return getattr(self, 'child', None) or _FakeWidget()

    def add_css_class(self, name):
        self.classes.append(name)

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_hexpand(self, value):
        self.props['hexpand'] = value

    def set_vexpand(self, value):
        self.props['vexpand'] = value

    def set_xalign(self, value):
        self.props['xalign'] = value

    def set_margin_top(self, value):
        self.props['margin_top'] = value

    def set_margin_bottom(self, value):
        self.props['margin_bottom'] = value

    def set_margin_start(self, value):
        self.props['margin_start'] = value

    def set_margin_end(self, value):
        self.props['margin_end'] = value

    def set_policy(self, horizontal, vertical):
        self.props['policy'] = (horizontal, vertical)

    def set_wrap(self, value):
        self.props['wrap'] = value

    def set_wrap_mode(self, value):
        self.props['wrap_mode'] = value

    def set_max_width_chars(self, value):
        self.props['max_width_chars'] = value

    def set_ellipsize(self, value):
        self.props['ellipsize'] = value

    def set_placeholder_text(self, value):
        self.props['placeholder'] = value

    def set_text(self, value):
        self.props['text'] = value
        if isinstance(self, _FakeLabel):
            self.props['label'] = value

    def set_label(self, value):
        self.props['label'] = value

    def label_text(self):
        if 'label' in self.props:
            return self.props['label']
        return self.kwargs.get('label')

    def set_visibility(self, value):
        self.props['visibility'] = value

    def set_width_chars(self, value):
        self.props['width_chars'] = value

    def set_sensitive(self, value):
        self.props['sensitive'] = bool(value)

    def set_active(self, value):
        self.props['active'] = bool(value)

    def get_active(self):
        return self.props.get('active', False)

    def get_allocation(self):
        return self.props.get('allocation', SimpleNamespace(x=0, y=0))

    def get_vadjustment(self):
        return self.props['vadjustment']


class _FakeLabel(_FakeWidget):
    pass


class _FakeButton(_FakeWidget):
    pass


class _FakeBox(_FakeWidget):
    pass


class _FakeSwitch(_FakeWidget):
    pass


@pytest.fixture
def fake_gtk():
    """Swap the real gi bindings for recorder fakes, restored after."""
    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    gtk.Box = _FakeBox
    gtk.Label = _FakeLabel
    gtk.Button = _FakeButton
    gtk.Entry = _FakeWidget
    gtk.ScrolledWindow = _FakeWidget
    gtk.Switch = _FakeSwitch
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    sys.modules.update(installed)
    try:
        yield gtk
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _StubConfig:
    """Duck-typed ShellConfig with real mutation semantics."""

    def __init__(self, **overrides):
        self.battery_saver = False
        self.battery_saver_auto = True
        self.battery_saver_auto_pct = 15
        self.app_standby_minutes = 10
        self.standby_exempt = []
        self.network_denied_apps = []
        self.smart_lock_bt_devices = []
        self.smart_lock_wifi_ssids = []
        self.smart_lock_last_pin_time = 0.0
        self.pin_hash = None
        self.privacy_indicators = True
        for key, value in overrides.items():
            setattr(self, key, value)

    def label_for(self, app_id, name):
        return name

    def set_battery_saver(self, value):
        self.battery_saver = bool(value)

    def set_battery_saver_auto(self, value):
        self.battery_saver_auto = bool(value)

    def set_battery_saver_auto_pct(self, value):
        self.battery_saver_auto_pct = max(1, min(100, int(value)))

    def set_app_standby_minutes(self, value):
        self.app_standby_minutes = max(0, int(value))

    def set_standby_exempt(self, app_ids):
        self.standby_exempt = [str(x) for x in app_ids]

    def set_network_denied_apps(self, app_ids):
        self.network_denied_apps = [str(x) for x in app_ids]

    def set_smart_lock_bt_devices(self, addresses):
        self.smart_lock_bt_devices = smart_lock.sanitize_bt_devices(
            addresses)

    def set_smart_lock_wifi_ssids(self, ssids):
        self.smart_lock_wifi_ssids = smart_lock.sanitize_ssids(ssids)

    def set_privacy_indicators(self, value):
        self.privacy_indicators = bool(value)

    def record_smart_lock_pin_time(self, timestamp):
        self.smart_lock_last_pin_time = timestamp


def _entry(app_id, name=None, flatpak=False):
    base = ('/var/lib/flatpak/exports/share/applications/%s'
            % app_id if flatpak
            else '/usr/share/applications/%s' % app_id)
    return SimpleNamespace(
        app_id=app_id, name=name or app_id,
        desktop_app=SimpleNamespace(get_filename=lambda: base))


def _bare_win(**overrides):
    statuses = []
    menus = []
    win = SimpleNamespace(
        config=_StubConfig(),
        app_index=SimpleNamespace(entries=[]),
        standby=SimpleNamespace(saver_effective=False, frozen_apps=[],
                                saver_calls=[],
                                set_saver=lambda v: (
                                    setattr(win, '_saver_set', v),
                                    win.standby.saver_calls.append(v))),
        _saver_set=None,
        stack=SimpleNamespace(
            visible=None,
            set_visible_child_name=lambda name: setattr(
                win.stack, 'visible', name)),
        item_actions=SimpleNamespace(
            _show_action_menu=lambda anchor, title, choices: menus.append(
                (title, [(label, cb) for label, cb in choices]))),
        _run_bg=lambda work, on_done: _sync_bg(work, on_done),
        _show_status=statuses.append,
        present=lambda: None,
        gesture_config=SimpleNamespace(get=lambda key: 'none'),
        _gesture_binding_labels={},
        _system_gesture_labels={},
        _pick_exempt_mode=False,
        _perm_net_supported=True,
        _statuses=statuses,
        _menus=menus,
    )
    for key, value in overrides.items():
        setattr(win, key, value)
    return win


def _sync_bg(work, on_done):
    try:
        result = work()
    except Exception:
        result = None
    on_done(result)


def _labels(box):
    """All Label texts under a box, depth-first (buttons excluded)."""
    texts = []

    def _walk(widget):
        if isinstance(widget, _FakeLabel):
            if widget.label_text() is not None:
                texts.append(widget.label_text())
        if isinstance(widget, _FakeWidget):
            for child in widget.children:
                _walk(child)

    _walk(box)
    return texts


def _buttons(box):
    found = []

    def _walk(widget):
        if isinstance(widget, _FakeButton) and 'clicked' in widget.handlers:
            found.append(widget)
        if isinstance(widget, _FakeWidget):
            for child in widget.children:
                _walk(child)

    _walk(box)
    return found


def _switches(box):
    found = []

    def _walk(widget):
        if isinstance(widget, _FakeSwitch):
            found.append(widget)
        if isinstance(widget, _FakeWidget):
            for child in widget.children:
                _walk(child)

    _walk(box)
    return found


# ------------------------------------------------------------------
# Standby section widgets
# ------------------------------------------------------------------

class TestStandbySection:
    def _section(self):
        return settings_standby.StandbySection()

    def test_titles_and_cards(self, fake_gtk):
        win = _bare_win()
        widgets = self._section().build(win)
        titles = [w.label_text() for w in widgets
                  if isinstance(w, _FakeLabel)]
        assert titles == ['Battery saver', 'App standby']
        cards = [w for w in widgets if isinstance(w, _FakeBox)]
        assert len(cards) == 2
        assert all('settings-card' in card.classes for card in cards)

    def test_saver_effects_and_builtins_listed(self, fake_gtk):
        win = _bare_win()
        widgets = self._section().build(win)
        texts = _labels(widgets[1]) + _labels(widgets[3])
        assert any('weather' in text for text in texts)
        for name in ('Dialer', 'TxxT daemon', 'Calls', 'Clock & alarms',
                     'Media playing'):
            assert any(name in text for text in texts), name

    def test_frozen_count_live(self, fake_gtk):
        win = _bare_win()
        widgets = self._section().build(win)
        assert 'Frozen now: 0' in _labels(widgets[1])
        win.standby.frozen_apps = ['a.desktop', 'b.desktop']
        self._section().refresh(win)
        assert 'Frozen now: 2' in _labels(widgets[1])

    def test_saver_flip_drives_controller_and_config(self, fake_gtk):
        win = _bare_win()
        self._section().build(win)
        switch = win._saver_switch
        assert switch.get_active() is False
        switch.set_active(True)
        callback, _args = switch.handlers['notify::active']
        callback(switch, None)
        assert win._saver_set is True
        assert win.config.battery_saver is True

    def test_threshold_pick(self, fake_gtk):
        win = _bare_win()
        section = self._section()
        section.build(win)
        assert win._saver_threshold_btn.label_text() == 'Auto-on at 15%'
        section._pick_threshold(win, win._saver_threshold_btn)
        assert win._menus and win._menus[-1][0] == 'Auto-on level'
        choices = dict(win._menus[-1][1])
        choices['20%']()
        assert win.config.battery_saver_auto_pct == 20
        assert win._saver_threshold_btn.label_text() == 'Auto-on at 20%'

    def test_timeout_pick_never(self, fake_gtk):
        win = _bare_win()
        section = self._section()
        section.build(win)
        section._pick_timeout(win, win._standby_timeout_btn)
        choices = dict(win._menus[-1][1])
        assert set(choices) == {'5 min', '15 min', '30 min', 'Never'}
        choices['Never']()
        assert win.config.app_standby_minutes == 0
        assert win._standby_timeout_btn.label_text() == 'Freeze after: Never'

    def test_exempt_add_remove(self, fake_gtk):
        win = _bare_win()
        win.app_index.entries = [_entry('org.a.App', 'A App')]
        win.config.standby_exempt = ['org.a.App']
        section = self._section()
        section.build(win)
        assert 'A App' in _labels(win._standby_exempt_box)
        remove = [btn for btn in _buttons(win._standby_exempt_box)
                  if btn.label_text() == 'Remove']
        assert len(remove) == 1
        callback, _args = remove[0].handlers['clicked']
        callback(remove[0])
        assert win.config.standby_exempt == []
        assert 'Every app may freeze.' in _labels(win._standby_exempt_box)

    def test_add_enters_drawer_pick_mode(self, fake_gtk):
        win = _bare_win()
        self._section().build(win)
        self._section()._on_add_exempt(win)
        assert win._pick_exempt_mode is True
        assert win.stack.visible == 'apps'
        assert any('never freeze' in text for text in win._statuses)

    def test_refresh_survives_bare_win(self, fake_gtk):
        self._section().refresh(SimpleNamespace())


# ------------------------------------------------------------------
# Permissions section widgets
# ------------------------------------------------------------------

class TestPermissionsSection:
    def _section(self):
        return settings_permissions.PermissionsSection()

    def test_titles_nav_and_disclaimer(self, fake_gtk):
        win = _bare_win()
        widgets = self._section().build(win)
        assert widgets[0].label_text() == 'App permissions'
        nav = [btn.label_text() for btn in _buttons(win._perm_nav_box)]
        assert nav == ['Camera — 0 allowed', 'Microphone — 0 allowed',
                       'Location — 0 allowed', 'Network — 0 blocked']
        assert 'No use in the last 24 h.' in _labels(win._perm_recent_box)
        assert any('Native apps reach camera' in text
                   for text in _labels(widgets[2]))

    def test_indicator_flip(self, fake_gtk):
        win = _bare_win()
        self._section().build(win)
        switch = win._perm_ind_switch
        assert switch.get_active() is True
        switch.set_active(False)
        callback, _args = switch.handlers['notify::active']
        callback(switch, None)
        assert win.config.privacy_indicators is False

    def test_app_rows_tag_flatpak(self, fake_gtk):
        win = _bare_win()
        win.app_index.entries = [_entry('org.flat.App', 'Flat'),
                                 _entry('org.native.App', 'Native',
                                        flatpak=False)]
        win.app_index.entries[0] = _entry('org.flat.App', 'Flat',
                                         flatpak=True)
        self._section().build(win)
        rows = [btn.label_text() for btn in _buttons(win._perm_apps_box)]
        assert rows == ['Flat — Flatpak', 'Native — Native']

    def test_perm_view_lists_states(self, fake_gtk):
        win = _bare_win()
        self._section().build(win)
        win._perm_maps = {'camera': {'org.x': ['yes'], 'org.y': ['no']},
                          'microphone': {}, 'location': {}}
        self._section()._open_perm_view(win, 'camera')
        texts = _labels(win._perm_apps_box)
        assert 'org.x — allowed' in texts
        assert 'org.y — denied' in texts

    def test_network_view_lists_blocks(self, fake_gtk):
        win = _bare_win()
        win.config.network_denied_apps = ['org.a.App']
        self._section().build(win)
        self._section()._open_perm_view(win, 'network')
        assert 'org.a.App — blocked' in _labels(win._perm_apps_box)

    def test_app_detail_switches(self, fake_gtk):
        win = _bare_win()
        win.app_index.entries = [_entry('a', 'A')]
        self._section().build(win)
        win._perm_maps = {'camera': {'a': ['yes']},
                          'microphone': {}, 'location': {}}
        self._section()._open_app_view(win, 'a')
        switches = _switches(win._perm_apps_box)
        assert len(switches) == 4
        assert switches[0].get_active() is True
        assert switches[3].get_active() is True

    def test_camera_flip_applies_store(self, fake_gtk, monkeypatch):
        applied = []
        monkeypatch.setattr(
            settings_permissions, 'apply_store_perm',
            lambda kind, app_id, allowed: applied.append(
                (kind, app_id, allowed)))
        win = _bare_win()
        win.app_index.entries = [_entry('a', 'A')]
        self._section().build(win)
        win._perm_maps = {'camera': {}, 'microphone': {}, 'location': {}}
        self._section()._open_app_view(win, 'a')
        mic = _switches(win._perm_apps_box)[1]
        mic.set_active(False)
        callback, _args = mic.handlers['notify::active']
        callback(mic, None)
        assert applied == [('microphone', 'a', False)]

    def test_network_flip_updates_config(self, fake_gtk):
        win = _bare_win()
        win.app_index.entries = [_entry('a', 'A')]
        self._section().build(win)
        win._perm_maps = {'camera': {}, 'microphone': {}, 'location': {}}
        self._section()._open_app_view(win, 'a')
        net = _switches(win._perm_apps_box)[3]
        net.set_active(False)
        callback, _args = net.handlers['notify::active']
        callback(net, None)
        assert win.config.network_denied_apps == ['a']

    def test_reset_to_ask(self, fake_gtk, monkeypatch):
        reset = []
        monkeypatch.setattr(
            settings_permissions, 'reset_store_perms',
            lambda app_id: reset.append(app_id))
        win = _bare_win()
        win.app_index.entries = [_entry('a', 'A')]
        win.config.network_denied_apps = ['a']
        self._section().build(win)
        win._perm_maps = {'camera': {'a': ['yes']},
                          'microphone': {}, 'location': {}}
        self._section()._open_app_view(win, 'a')
        reset_btn = [btn for btn in _buttons(win._perm_apps_box)
                     if btn.label_text() == 'Reset all to ask']
        assert len(reset_btn) == 1
        callback, _args = reset_btn[0].handlers['clicked']
        callback(reset_btn[0])
        assert reset == ['a']
        assert win.config.network_denied_apps == []

    def test_recent_rows(self, fake_gtk):
        win = _bare_win()
        self._section().build(win)
        now = 10_000.0
        self._section()._render_recent(
            win, [{'kind': 'mic', 'app': 'Calls', 'at': now - 90.0},
                  {'kind': 'camera', 'app': 'Cam', 'at': now - 7200.0}],
            now)
        texts = _labels(win._perm_recent_box)
        assert 'Calls — mic · 1m ago' in texts
        assert 'Cam — cam · 2h ago' in texts

    def test_helpers(self):
        assert settings_permissions.age_text(100.0, 130.0) == 'just now'
        assert settings_permissions.age_text(0.0, 300.0) == '5m ago'
        assert settings_permissions.age_text(0.0, 7200.0) == '2h ago'
        maps = {'camera': {'Org.X.DESKTOP': ['YES']}}
        assert settings_permissions.stored_state(maps, 'camera', 'org.x') is True
        assert settings_permissions.stored_state(maps, 'camera', 'org.y') is None
        assert settings_permissions.stored_state(None, 'camera', 'org.x') is None


# ------------------------------------------------------------------
# Smart Lock section widgets
# ------------------------------------------------------------------

@pytest.fixture
def trusted_live():
    smart_lock._reset_runtime_state()
    live = SimpleNamespace(trusted_bt=['Pixel Buds'], trusted_ssid=None,
                           stop=lambda: None)
    previous = smart_lock._LIVE
    smart_lock._LIVE = live
    try:
        yield live
    finally:
        smart_lock._LIVE = previous
        smart_lock._reset_runtime_state()


class TestSmartLockSection:
    def _section(self):
        return settings_smart_lock.SmartLockSection()

    def test_off_when_unconfigured(self, fake_gtk):
        win = _bare_win()
        assert settings_smart_lock.trust_status(win) == ('Off', '')
        widgets = self._section().build(win)
        texts = _labels(widgets[1])
        assert 'Off' in texts
        assert 'PIN re-check every 4 h.' in texts

    def test_unlocked_by_device(self, fake_gtk, trusted_live):
        win = _bare_win()
        win.config.smart_lock_bt_devices = ['AA:BB:CC:DD:EE:FF']
        smart_lock.note_pin_unlock(win.config)
        assert settings_smart_lock.trust_status(win) == (
            'Unlocked by: Pixel Buds', '')

    def test_configured_but_untrusted_shows_reason(self, fake_gtk):
        win = _bare_win()
        win.config.pin_hash = 'set'
        win.config.smart_lock_wifi_ssids = ['Home']
        status, detail = settings_smart_lock.trust_status(win)
        assert status == 'Off'
        assert detail.startswith('PIN required')

    def test_no_on_body_row(self, fake_gtk):
        win = _bare_win()
        widgets = self._section().build(win)
        texts = []
        for widget in widgets:
            texts.extend(_labels(widget))
        assert 'on body' not in '\n'.join(texts).lower()

    def test_trusted_lists_and_remove(self, fake_gtk):
        win = _bare_win()
        win.config.smart_lock_bt_devices = ['AA:BB:CC:DD:EE:FF']
        win.config.smart_lock_wifi_ssids = ['Home']
        self._section().build(win)
        assert 'AA:BB:CC:DD:EE:FF' in _labels(win._slock_bt_box)
        assert 'Home' in _labels(win._slock_wifi_box)
        remove = [btn for btn in _buttons(win._slock_wifi_box)
                  if btn.label_text() == 'Remove'][0]
        callback, _args = remove.handlers['clicked']
        callback(remove)
        assert win.config.smart_lock_wifi_ssids == []

    def test_bt_picker_adds_paired(self, fake_gtk, monkeypatch):
        devs = [SimpleNamespace(address='AA:BB:CC:DD:EE:FF',
                                name='Buds', paired=True, connected=True),
                SimpleNamespace(address='11:22:33:44:55:66',
                                name='Speaker', paired=False,
                                connected=False)]
        monkeypatch.setattr(system_settings, 'list_bt_devices',
                            lambda: devs)
        win = _bare_win()
        section = self._section()
        section.build(win)
        section._pick_bt_device(win, object())
        assert win._menus and win._menus[-1][0] == 'Trust a device'
        choices = dict(win._menus[-1][1])
        assert set(choices) == {'Buds (AA:BB:CC:DD:EE:FF)'}
        choices['Buds (AA:BB:CC:DD:EE:FF)']()
        assert win.config.smart_lock_bt_devices == ['AA:BB:CC:DD:EE:FF']

    def test_bt_unavailable_announced(self, fake_gtk, monkeypatch):
        monkeypatch.setattr(system_settings, 'list_bt_devices', lambda: None)
        win = _bare_win()
        self._section()._pick_bt_device(win, object())
        assert win._statuses == ['Bluetooth unavailable.']

    def test_ssid_picker_adds_saved(self, fake_gtk, monkeypatch):
        monkeypatch.setattr(system_settings, 'saved_wifi_ssids',
                            lambda: ['Cafe', 'Home'])
        win = _bare_win()
        win.config.smart_lock_wifi_ssids = ['Home']
        section = self._section()
        section.build(win)
        section._pick_ssid(win, object())
        choices = dict(win._menus[-1][1])
        assert set(choices) == {'Cafe'}
        choices['Cafe']()
        assert win.config.smart_lock_wifi_ssids == ['Home', 'Cafe']

    def test_reauth_text(self):
        assert settings_smart_lock.reauth_text() == 'PIN re-check every 4 h.'


# ------------------------------------------------------------------
# Deep links
# ------------------------------------------------------------------

class _FakeAdjustment:
    def __init__(self):
        self.values = []

    def set_value(self, value):
        self.values.append(value)


def _title_at(y):
    title = _FakeLabel(label='section', xalign=0)
    title.props['allocation'] = SimpleNamespace(x=0, y=y)
    return title


def _open_win(**titles):
    win = _bare_win()
    win.presented = []
    win.present = lambda: win.presented.append(1)
    win._settings_section_titles = {
        key: _title_at(y) for key, y in titles.items()}
    win.adjustment = _FakeAdjustment()
    scroll = _FakeBox()
    scroll.props['vadjustment'] = win.adjustment
    win._settings_scroll = scroll
    win._refresh_wifi = lambda: None
    win._refresh_bluetooth = lambda: None

    def _scroll(section):
        titles = win._settings_section_titles or {}
        target = titles.get(section)
        scroll_ref = win._settings_scroll
        if target is None or scroll_ref is None:
            return False
        offset = target.get_allocation().y
        scroll_ref.get_vadjustment().set_value(max(0.0, float(offset)))
        return False

    win._scroll_settings_to = _scroll
    return win


class TestDeepLinks:
    def _page(self, win):
        return settings_page.SettingsPage(win)

    def test_standby_opens(self, fake_gtk):
        win = _open_win(standby=120)
        self._page(win).open_settings('standby')
        assert win.stack.visible == 'settings'
        assert win.presented == [1]
        assert win.adjustment.values[-1] == 120.0

    def test_permissions_opens(self, fake_gtk):
        win = _open_win(permissions=240)
        self._page(win).open_settings('permissions')
        assert win.stack.visible == 'settings'
        assert win.adjustment.values[-1] == 240.0

    def test_smart_lock_opens(self, fake_gtk):
        win = _open_win(smart_lock=360)
        self._page(win).open_settings('smart_lock')
        assert win.stack.visible == 'settings'
        assert win.adjustment.values[-1] == 360.0

    def test_unknown_still_opens_top(self, fake_gtk):
        win = _open_win(standby=120)
        self._page(win).open_settings('torch')
        assert win.stack.visible == 'settings'
        assert win.adjustment.values == []


# ------------------------------------------------------------------
# Drawer exempt pick mode
# ------------------------------------------------------------------

class TestDrawerExemptPick:
    def test_pick_adds_exempt(self, fake_gtk):
        import drawer_page
        win = _bare_win()
        win._pick_slot_mode = False
        win._pick_gesture_key = None
        win._pick_exempt_mode = True
        drawer_page.DrawerPage(win)._launch_entry(_entry('org.a.App', 'A'))
        assert win.config.standby_exempt == ['org.a.App']
        assert win._pick_exempt_mode is False
        assert win.stack.visible == 'settings'
        assert any('never freeze' in text for text in win._statuses)

    def test_duplicate_ignored(self, fake_gtk):
        import drawer_page
        win = _bare_win()
        win._pick_slot_mode = False
        win._pick_gesture_key = None
        win._pick_exempt_mode = True
        win.config.standby_exempt = ['ORG.A.APP.desktop']
        drawer_page.DrawerPage(win)._launch_entry(_entry('org.a.App', 'A'))
        assert win.config.standby_exempt == ['ORG.A.APP.desktop']


# ------------------------------------------------------------------
# Static: registry, install set, UI rules
# ------------------------------------------------------------------

class TestW5RegistryStatic:
    def test_order_and_gestures_last(self):
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        for key in ('standby', 'permissions', 'smart_lock'):
            assert key in keys
        assert keys[-1] == 'gestures'

    def test_sections_shape(self):
        seen = set()
        for key, cls in settings_page.SETTINGS_SECTIONS:
            assert key and key not in seen
            seen.add(key)
            assert issubclass(cls, settings_page._Section), key
            assert cls().key == key
            assert callable(cls().build) and callable(cls().refresh)

    def test_deep_link_keys(self):
        for key in ('standby', 'permissions', 'smart_lock'):
            assert key in settings_page._SETTINGS_SECTION_KEYS

    def test_meson_lists_new_files(self):
        meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
        for name in NEW_FILES:
            assert "'src/%s'" % name in meson
            assert (SRC / name).is_file()

    def test_no_blocking_io_or_focus_grabs(self):
        for name in NEW_FILES:
            text = (SRC / name).read_text(encoding='utf-8')
            assert 'call_sync' not in text, name
            assert 'subprocess' not in text, name
            assert 'time.sleep' not in text, name
            assert 'grab_focus' not in text, name
            assert 'Gtk.Entry(' not in text, name
            assert 'NOTIF_ID' not in text, name

    def test_only_known_signals(self):
        found = set()
        for name in NEW_FILES:
            text = (SRC / name).read_text(encoding='utf-8')
            for signal in re.findall(r"\.connect\(\s*['\"]([^'\"]+)", text):
                found.add(signal.split('::')[0])
        assert found <= {'clicked', 'notify'}, found
