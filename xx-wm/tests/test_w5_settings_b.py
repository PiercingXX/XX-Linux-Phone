"""W5 settings B: charging, battery use, screen time, medical ID, lock media.

Headless model/presenter tests for every new section plus static guards:
registry order/shape, meson install set, no main-loop blocking calls in the
new settings files, and D-Bus reply_type discipline. Widget rendering runs
against recording Gtk fakes (the test_w2_settings idiom); Gtk can not
init without a display here.
"""
import importlib.util
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
HELPER = Path(__file__).parent.parent / 'launcher' / 'helper' / 'xx_wm_helper.py'
# Lane copies carry xx-contacts under ref/; the real checkout has it as a
# sibling repo.
CONTACTS_SERVICE = next(
    (p for p in (Path(__file__).parent.parent / 'ref' / 'xx-contacts',
                 Path(__file__).parent.parent.parent / 'xx-contacts')
     if (p / 'src' / 'xx_contacts' / 'service.py').exists()),
    Path(__file__).parent.parent / 'ref' / 'xx-contacts') \
    / 'src' / 'xx_contacts' / 'service.py'


# --- recording Gtk fakes -------------------------------------------------

class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.calls = []
        self.handlers = {}
        self._text = kwargs.get('label', '')
        self._active = False

    def _rec(self, name, *args):
        self.calls.append((name, args))

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

    def add_controller(self, controller):
        self.calls.append(('add_controller', (controller,)))

    def set_visible(self, value):
        self._visible = bool(value)

    def get_visible(self):
        return getattr(self, '_visible', True)

    def is_visible(self):
        return self.get_visible()

    def set_hexpand(self, v):
        self._rec('hexpand', v)

    def set_vexpand(self, v):
        self._rec('vexpand', v)

    def set_xalign(self, v):
        self._rec('xalign', v)

    def set_halign(self, v):
        self._rec('halign', v)

    def set_valign(self, v):
        self._rec('valign', v)

    def set_margin_top(self, v):
        self._rec('margin_top', v)

    def set_margin_bottom(self, v):
        self._rec('margin_bottom', v)

    def set_margin_start(self, v):
        self._rec('margin_start', v)

    def set_margin_end(self, v):
        self._rec('margin_end', v)

    def set_wrap(self, v):
        self._rec('wrap', v)

    def set_wrap_mode(self, v):
        self._rec('wrap_mode', v)

    def set_max_width_chars(self, v):
        self.calls.append(('max_width_chars', (v,)))

    def set_ellipsize(self, v):
        self._rec('ellipsize', v)

    def set_placeholder_text(self, v):
        self._rec('placeholder', v)

    def set_text(self, v):
        self._text = v

    def get_text(self):
        return self._text

    def set_label(self, v):
        self._text = v

    def get_label(self):
        return self._text

    def set_visibility(self, v):
        self._rec('visibility', v)

    def set_width_chars(self, v):
        self.calls.append(('width_chars', (v,)))

    def set_input_purpose(self, v):
        self._rec('input_purpose', v)

    def set_active(self, v):
        self._active = bool(v)

    def get_active(self):
        return self._active

    def set_show_text(self, v):
        self._rec('show_text', v)

    def set_fraction(self, v):
        self.calls.append(('fraction', (v,)))

    def set_orientation(self, v):
        self._rec('orientation', v)

    def set_inverted(self, v):
        self._rec('inverted', v)

    def set_size_request(self, w, h):
        self.calls.append(('size_request', (w, h)))

    def set_sensitive(self, v):
        self._rec('sensitive', v)

    def set_single_line_mode(self, v):
        self._rec('single_line', v)

    def set_lines(self, v):
        self._rec('lines', v)

    def set_selectable(self, v):
        self._rec('selectable', v)


def _walk(widget):
    yield widget
    for child in list(getattr(widget, 'children', [])):
        yield from _walk(child)
    direct = getattr(widget, 'child', None)
    if isinstance(direct, _FakeWidget):
        yield from _walk(direct)


def _texts(widget):
    found = []
    for node in _walk(widget):
        label = node.kwargs.get('label')
        if isinstance(label, str):
            found.append(label)
        text = getattr(node, '_text', '')
        if isinstance(text, str) and text and text != label:
            found.append(text)
    return found


def _install_fake_gi(monkeypatch):
    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = _require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def _make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        _make('Adw'), _make('Gdk'), _make('GLib'), _make('Gtk'),
        _make('Pango'), _make('Gio'))
    gtk.Box = _FakeWidget
    gtk.Label = _FakeWidget
    gtk.Button = _FakeWidget
    gtk.Entry = _FakeWidget
    gtk.Switch = _FakeWidget
    gtk.ScrolledWindow = _FakeWidget
    gtk.ProgressBar = _FakeWidget
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    gtk.InputPurpose = SimpleNamespace(NUMBER=1, FREE_FORM=0)
    gtk.Align = SimpleNamespace(START=0, END=1, CENTER=2, FILL=3)
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 100

    class _FakeCssProvider:
        def __init__(self):
            self.data = []

        def load_from_data(self, data):
            self.data.append(bytes(data))

    class _FakeGesture(_FakeWidget):
        @classmethod
        def new(cls):
            return cls()

    gtk.CssProvider = _FakeCssProvider
    gtk.GestureClick = _FakeGesture
    gtk.StyleContext = SimpleNamespace(
        add_provider_for_display=lambda *a, **k: None)
    gdk.Display = SimpleNamespace(get_default=lambda: None)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]
    glib.Error = type('Error', (Exception,), {})
    glib.SpawnFlags = SimpleNamespace(SEARCH_PATH=1)
    gio.BusType = SimpleNamespace(SYSTEM=1, SESSION=2)
    gio.DBusCallFlags = SimpleNamespace(NONE=0)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    for name, module in {'gi': gi, 'gi.repository': repo,
                         'gi.repository.Adw': adw,
                         'gi.repository.Gdk': gdk,
                         'gi.repository.GLib': glib,
                         'gi.repository.Gtk': gtk,
                         'gi.repository.Pango': pango,
                         'gi.repository.Gio': gio}.items():
        monkeypatch.setitem(sys.modules, name, module)
    return gtk


@pytest.fixture
def fake_gi(monkeypatch):
    return _install_fake_gi(monkeypatch)


def _bare_win(**overrides):
    config = SimpleNamespace(
        charge_limit=None, medical_id={},
        lock_screen_medical_id=True, app_time_limits={},
        app_limit_ignored={},
        label_for=lambda app_id, name: name,
    )

    def _set_charge(value):
        config.charge_limit = value

    def _set_medical(medical):
        config.medical_id = dict(medical)

    def _set_show(value):
        config.lock_screen_medical_id = bool(value)

    def _set_limit(app_id, minutes):
        limits = dict(config.app_time_limits)
        if minutes <= 0:
            limits.pop(app_id, None)
        else:
            limits[app_id] = int(minutes)
        config.app_time_limits = limits

    def _set_ignored(app_id, day):
        ignored = dict(config.app_limit_ignored)
        if day is None:
            ignored.pop(app_id, None)
        else:
            ignored[app_id] = str(day)
        config.app_limit_ignored = ignored

    config.set_charge_limit = _set_charge
    config.set_medical_id = _set_medical
    config.set_lock_screen_medical_id = _set_show
    config.set_app_time_limit = _set_limit
    config.set_app_limit_ignored = _set_ignored
    win = SimpleNamespace(config=config, app_index=SimpleNamespace(entries=[]))
    for key, value in overrides.items():
        setattr(win, key, value)
    return win


def _load_helper():
    spec = importlib.util.spec_from_file_location('xx_wm_helper', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- charging model ------------------------------------------------------

class TestChargeClamp:
    def test_matches_helper_across_range(self):
        import settings_charging as charging
        helper = _load_helper()
        for value in list(range(0, 130, 3)) + [80, 85, 90]:
            assert charging.clamp_charge_limit(value) == \
                helper.clamp_charge_limit(value)

    def test_garbage_raises_both(self):
        import settings_charging as charging
        helper = _load_helper()
        for bad in ('many', None, ''):
            with pytest.raises(Exception):
                charging.clamp_charge_limit(bad)
            with pytest.raises(Exception):
                helper.clamp_charge_limit(bad)

    def test_boundaries(self):
        import settings_charging as charging
        assert charging.clamp_charge_limit(50) == 50
        assert charging.clamp_charge_limit(100) == 100
        assert charging.clamp_charge_limit(20) == 50
        assert charging.clamp_charge_limit(140) == 100


class TestChargeProbe:
    def _sysfs(self, tmp_path, knobs, supply_type='Battery'):
        base = tmp_path / 'class' / 'power_supply'
        for battery, knob in knobs:
            knob_dir = base / battery
            knob_dir.mkdir(parents=True, exist_ok=True)
            (knob_dir / knob).write_text('100\n')
            (knob_dir / 'type').write_text(supply_type + '\n')
        return str(tmp_path)

    def test_standard_knob_found(self, tmp_path):
        import settings_charging as charging
        root = self._sysfs(tmp_path, [('BAT0', 'charge_control_end_threshold')])
        assert charging.knob_supported(root) is True

    def test_vendor_knob_found(self, tmp_path):
        import settings_charging as charging
        root = self._sysfs(tmp_path, [('BAT0', 'charge_stop_threshold')])
        assert charging.knob_supported(root) is True

    def test_no_knob(self, tmp_path):
        import settings_charging as charging
        root = self._sysfs(tmp_path, [])
        assert charging.knob_supported(root) is False

    def test_missing_sysfs(self, tmp_path):
        import settings_charging as charging
        assert charging.knob_supported(
            str(tmp_path / 'nosuchdir')) is False

    def test_no_knob_text_exact(self):
        import settings_charging as charging
        assert charging.NO_KNOB_TEXT == (
            "This phone's battery driver doesn't support a charge limit.")

    def test_custom_parsing(self):
        import settings_charging as charging
        assert charging.parse_custom_limit('85') == 85
        assert charging.parse_custom_limit(' 92% ') == 92
        assert charging.parse_custom_limit('30') == 50
        assert charging.parse_custom_limit('140') == 100
        assert charging.parse_custom_limit('off') is None
        assert charging.parse_custom_limit('') is None


class TestChargingSection:
    def test_build_and_unsupported_message(self, fake_gi):
        import settings_charging as charging
        win = _bare_win()
        section = charging.ChargingSection()
        widgets = section.build(win)
        assert len(widgets) == 2
        assert 'settings-card' in widgets[1].classes
        assert 'section-title' in widgets[0].classes
        section._on_state(win, (False, None))
        assert charging.NO_KNOB_TEXT in _texts(widgets[1])

    def test_supported_shows_presets(self, fake_gi):
        import settings_charging as charging
        win = _bare_win()
        section = charging.ChargingSection()
        widgets = section.build(win)
        section._on_state(win, (True, '87% — discharging'))
        labels = _texts(widgets[1])
        assert any('80%' in text for text in labels)
        assert any('87%' in text for text in labels)

    def test_apply_success_saves_config(self, fake_gi, monkeypatch):
        import settings_charging as charging
        calls = {}

        fake_helper = SimpleNamespace(
            set_charge_limit=lambda pct, cb: (
                calls.update(percent=pct), cb(True, None)))
        monkeypatch.setitem(sys.modules, 'helper_client', fake_helper)
        win = _bare_win()
        section = charging.ChargingSection()
        section.build(win)
        section._apply(win, 80)
        assert calls['percent'] == 80
        assert win.config.charge_limit == 80

    def test_apply_off_sends_100(self, fake_gi, monkeypatch):
        import settings_charging as charging
        calls = {}
        fake_helper = SimpleNamespace(
            set_charge_limit=lambda pct, cb: (
                calls.update(percent=pct), cb(True, None)))
        monkeypatch.setitem(sys.modules, 'helper_client', fake_helper)
        section = charging.ChargingSection()
        section.build(_bare_win())
        section._apply(_bare_win(), None)
        assert calls['percent'] == 100

    def test_apply_failure_says_so(self, fake_gi, monkeypatch):
        import settings_charging as charging
        fake_helper = SimpleNamespace(
            set_charge_limit=lambda pct, cb: cb(False, 'denied'))
        monkeypatch.setitem(sys.modules, 'helper_client', fake_helper)
        win = _bare_win()
        section = charging.ChargingSection()
        section.build(win)
        section._apply(win, 85)
        assert 'denied' in (section._note.get_text() if section._note else '')

    def _visible_texts(self, widget):
        found = []
        visible = getattr(widget, 'get_visible', None)
        if callable(visible) and not visible():
            return found
        label = widget.kwargs.get('label')
        if isinstance(label, str):
            found.append(label)
        text = getattr(widget, '_text', '')
        if isinstance(text, str) and text and text != label:
            found.append(text)
        for child in list(getattr(widget, 'children', [])):
            found.extend(self._visible_texts(child))
        direct = getattr(widget, 'child', None)
        if isinstance(direct, _FakeWidget):
            found.extend(self._visible_texts(direct))
        return found

    def test_unsupported_hides_custom_controls(self, fake_gi):
        import settings_charging as charging
        win = _bare_win()
        section = charging.ChargingSection()
        widgets = section.build(win)
        section._on_state(win, (False, None))
        assert section._custom_entry.get_visible() is False
        assert section._apply_btn.get_visible() is False
        labels = self._visible_texts(widgets[1])
        assert charging.NO_KNOB_TEXT in labels
        assert not any('Custom' in text for text in labels)
        assert not any('Set custom limit' in text for text in labels)
        assert not any('%' in text and 'Charge limit' not in text
                       for text in labels)

    def test_unknown_hides_custom_controls(self, fake_gi):
        import settings_charging as charging
        win = _bare_win()
        section = charging.ChargingSection()
        widgets = section.build(win)
        assert section._custom_entry.get_visible() is False
        assert section._apply_btn.get_visible() is False
        labels = self._visible_texts(widgets[1])
        assert not any('Set custom limit' in text for text in labels)

    def test_supported_shows_custom_controls(self, fake_gi):
        import settings_charging as charging
        win = _bare_win()
        section = charging.ChargingSection()
        widgets = section.build(win)
        section._on_state(win, (True, '87% — discharging'))
        assert section._custom_entry.get_visible() is True
        assert section._apply_btn.get_visible() is True
        labels = self._visible_texts(widgets[1])
        assert any('Set custom limit' in text for text in labels)
        assert any('80%' in text for text in labels)


# --- battery-use model ---------------------------------------------------

def _cgroup_tree(tmp_path, scopes):
    app_slice = (tmp_path / 'user.slice' / 'user-1000.slice'
                 / 'user@1000.service' / 'app.slice')
    for scope, usec in scopes.items():
        scope_dir = app_slice / scope
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / 'cpu.stat').write_text(
            f'usage_usec {usec}\nuser_usec 1\nsystem_usec 1\n')
    return str(app_slice)


class TestCpuModel:
    def test_parse_cpu_stat(self):
        import settings_battery_use as battery_use
        assert battery_use.parse_cpu_stat(
            'usage_usec 12345\nuser_usec 9\n') == 12345
        assert battery_use.parse_cpu_stat('user_usec 9\n') is None
        assert battery_use.parse_cpu_stat('usage_usec nope\n') is None
        assert battery_use.parse_cpu_stat('') is None

    def test_sanitize_matches_standby(self):
        import settings_battery_use as battery_use
        from app_standby import sanitize_app_id
        for app_id in ('org.gnome.Calls.desktop', 'my app!',
                       'a/b.c-d_e', '', 'xx-dialer'):
            assert battery_use.sanitize_app_id(app_id) == \
                sanitize_app_id(app_id)

    def test_cpu_time_from_fake_tree(self, tmp_path, monkeypatch):
        import settings_battery_use as battery_use
        monkeypatch.setattr('os.getuid', lambda: 1000)
        scopes = {
            'app-xxwm-org.example.App.desktop-11.scope': 1_000_000,
            'app-xxwm-org.example.App.desktop-22.scope': 2_000_000,
            'app-xxwm-org.example.Other.desktop-33.scope': 9_000_000,
        }
        app_slice = _cgroup_tree(tmp_path, scopes)
        got = battery_use.cpu_time_for_app('org.example.App.desktop',
                                           app_slice)
        assert got == 3_000_000
        assert battery_use.cpu_time_for_app('org.example.Missing.desktop',
                                            app_slice) is None

    def test_cpu_time_missing_tree(self, tmp_path):
        import settings_battery_use as battery_use
        assert battery_use.cpu_time_for_app(
            'org.example.App.desktop',
            str(tmp_path / 'nosuchdir')) is None


class TestEstimateShares:
    def test_ranked_and_percentages(self):
        import settings_battery_use as battery_use
        rows = battery_use.estimate_shares(
            {'a.desktop': 600, 'b.desktop': 60},
            {'a.desktop': 60_000_000, 'b.desktop': 1_200_000_000})
        assert [row[0] for row in rows] == ['b.desktop', 'a.desktop']
        total = sum(row[1] for row in rows)
        assert total == pytest.approx(100.0)
        assert all('estimated' not in row for row in rows)

    def test_empty_when_nothing_measurable(self):
        import settings_battery_use as battery_use
        assert battery_use.estimate_shares({}, {}) == []
        assert battery_use.estimate_shares({'a': 0}, {'a': 0}) == []

    def test_row_format_labels_estimated_context(self):
        import settings_battery_use as battery_use
        text = battery_use.format_share(('a.desktop', 68.4, 720, 180_000_000))
        assert text.startswith('68%')
        assert 'screen' in text and 'CPU' in text


class TestBatteryUseSection:
    def test_gather_and_render(self, fake_gi, tmp_path, monkeypatch):
        import settings_battery_use as battery_use
        monkeypatch.setattr('os.getuid', lambda: 1000)
        scopes = {'app-xxwm-org.example.App.desktop-11.scope': 600_000_000}
        app_slice = _cgroup_tree(tmp_path, scopes)
        tracker = SimpleNamespace(
            daily_totals=lambda day: {'org.example.App.desktop': 600})
        win = _bare_win()
        section = battery_use.BatteryUseSection()
        widgets = section.build(win)
        section._slice_override = app_slice
        section._render(win, section._gather(tracker, app_slice))
        labels = _texts(widgets[1])
        assert any('%' in text for text in labels)
        assert section._note is not None
        assert 'estimated' in section._note.get_text()

    def test_empty_state(self, fake_gi):
        import settings_battery_use as battery_use
        win = _bare_win()
        section = battery_use.BatteryUseSection()
        widgets = section.build(win)
        section._render(win, ({}, {}))
        assert 'No usage yet.' in _texts(widgets[1])


# --- screen-time model ---------------------------------------------------

class TestScreenTimeModel:
    def test_fmt_duration(self):
        import settings_screentime as screen_time
        assert screen_time.fmt_duration(45) == '45s'
        assert screen_time.fmt_duration(720) == '12m'
        assert screen_time.fmt_duration(3900) == '1h 05m'
        assert screen_time.fmt_duration('junk') == '0s'

    def test_limit_cycle(self):
        import settings_screentime as screen_time
        assert screen_time.next_limit(0) == 15
        assert screen_time.next_limit(240) == 0
        assert screen_time.next_limit(60) == 120

    def test_reached_and_ignore(self):
        import settings_screentime as screen_time
        config = SimpleNamespace(
            app_time_limits={'a.desktop': 60},
            app_limit_ignored={},
            set_app_limit_ignored=lambda a, d: config.app_limit_ignored.update(
                {a: d}))
        totals = {'a.desktop': 3600, 'b.desktop': 10}
        assert screen_time.reached_limits(
            config, totals, '2026-10-16') == {'a.desktop': (3600, 3600)}
        screen_time.ignore_for_today(config, 'a.desktop', '2026-10-16')
        assert screen_time.reached_limits(
            config, totals, '2026-10-16') == {}
        assert screen_time.is_ignored(config, 'a.desktop', '2026-10-16')

    def test_is_limited_today(self, fake_gi, monkeypatch):
        import settings_battery_use as battery_use
        import settings_screentime as screen_time
        tracker = SimpleNamespace(
            daily_totals=lambda day: {'a.desktop': 3600})
        monkeypatch.setattr(battery_use, 'usage_tracker_for',
                            lambda w: tracker)
        win = _bare_win()
        win.config.app_time_limits = {'a.desktop': 60}
        assert screen_time.is_limited_today(win, 'a.desktop') is True
        assert screen_time.is_limited_today(win, 'b.desktop') is False
        screen_time.ignore_for_today(win.config, 'a.desktop')
        assert screen_time.is_limited_today(win, 'a.desktop') is False

    def test_notify_override_seam(self):
        import settings_screentime as screen_time
        seen = []
        win = SimpleNamespace(
            _screen_time_banner=lambda a, label, s, lim: seen.append(a))
        assert screen_time.notify_limit(
            win, 'a.desktop', 'App', 3600, 3600) is True
        assert seen == ['a.desktop']

    def test_notify_locked_app_stays_quiet(self):
        import settings_screentime as screen_time
        app = SimpleNamespace(_banner_locked=lambda: True,
                              _ensure_banner=lambda: None)
        win = SimpleNamespace(get_application=lambda: app)
        assert screen_time.notify_limit(
            win, 'a.desktop', 'App', 3600, 3600) is False


class TestScreenTimeSection:
    def test_render_week_and_counts(self, fake_gi):
        import datetime
        import settings_screentime as screen_time
        win = _bare_win()
        section = screen_time.ScreenTimeSection()
        section.build(win)
        today = datetime.date.today()
        week = [(today - datetime.timedelta(days=i), 60 * i)
                for i in range(6, -1, -1)]
        section._render(win, ({'a.desktop': 120}, week, 7, 3))
        assert section._today_label.get_text() == 'Today: 2m'
        assert '7 unlocks · 3 notifications' in \
            section._counts_label.get_text()
        assert len(section._week_box.children) == 7
        fractions = [args[0] for col in section._week_box.children
                     for bar in col.children
                     for name, args in bar.calls if name == 'fraction']
        assert len(fractions) == 7
        assert max(fractions) == pytest.approx(1.0)

    def test_limit_banner_fires_once(self, fake_gi):
        import settings_screentime as screen_time
        seen = []
        config = SimpleNamespace(
            app_time_limits={'a.desktop': 60}, app_limit_ignored={},
            label_for=lambda a, n: n)
        win = _bare_win(config=config)
        win._screen_time_banner = lambda a, label, s, lim: seen.append(a)
        win._screen_time_notified = set()
        section = screen_time.ScreenTimeSection()
        section.build(win)
        section._render(win, ({'a.desktop': 3600}, [], 0, 0))
        assert seen == ['a.desktop']
        section._render(win, ({'a.desktop': 3700}, [], 0, 0))
        assert seen == ['a.desktop']

    def test_breakdown_view(self, fake_gi):
        import settings_screentime as screen_time
        win = _bare_win()
        section = screen_time.ScreenTimeSection()
        section.build(win)
        section._select(win, None)
        section._selected = 'a.desktop'
        section._render(win, ({'a.desktop': 120}, [], 0, 0))
        assert any('‹ Back' in text
                   for text in _texts(section._apps_box))


# --- medical model --------------------------------------------------------

class TestContactsShapes:
    def test_constants_match_xx_contacts_xml(self):
        import settings_medical as medical
        if not CONTACTS_SERVICE.exists():
            pytest.skip('xx-contacts source not found')
        xml = CONTACTS_SERVICE.read_text(encoding='utf-8')
        assert medical.CONTACTS_BUS in xml
        assert medical.CONTACTS_PATH in xml
        assert medical.CONTACTS_IFACE in xml
        assert medical.CONTACTS_BUS == 'io.piercingxx.Contacts'
        assert medical.CONTACTS_PATH == '/io/piercingxx/Contacts'
        assert medical.CONTACTS_IFACE == 'io.piercingxx.Contacts1'
        search = re.search(
            r'<method name="Search">(.*?)</method>', xml, re.S).group(1)
        assert 'type="s" name="query"' in search
        assert 'type="u" name="limit"' in search
        assert 'type="a(sss)"' in search

    def test_parse_search_results(self):
        import settings_medical as medical
        assert medical.parse_search_results(
            [('u1', 'Ada', '+100'), ('u2', 'Bob', '')]) == [
            ('u1', 'Ada', '+100'), ('u2', 'Bob', '')]
        assert medical.parse_search_results('junk') == []
        assert medical.parse_search_results([('only-two', 'x')]) == []
        assert medical.parse_search_results(
            [('u', '', ''), ('v', 'Ann', '1')]) == [('v', 'Ann', '1')]

    def test_search_uses_variant_reply_type(self):
        text = (SRC / 'settings_medical.py').read_text(encoding='utf-8')
        assert 'GLib.VariantType.new(CONTACTS_SEARCH_REPLY)' in text
        assert 'call_sync(' not in text
        assert '.unpack()[0]' in text


class TestContactsSearchCall:
    def _fake_dbus(self, monkeypatch, rows=None):
        seen = {}

        class _Reply:
            def unpack(self):
                return (rows,)

        class _Conn:
            def call_finish(self, _result):
                return _Reply()

            def call(self, *args):
                seen['args'] = args
                callback = args[9]
                callback(self, 'result')

        class _FakeGio:
            BusType = SimpleNamespace(SYSTEM=1, SESSION=2)
            DBusCallFlags = SimpleNamespace(NONE=0)

            @staticmethod
            def bus_get_finish(_result):
                return _Conn()

            @staticmethod
            def bus_get(_bus, _cancellable, callback):
                callback('source', 'result')

        class _FakeVariant:
            def __init__(self, fmt, value=None):
                self.fmt = fmt
                self.value = value

        class _FakeGLib:
            Variant = _FakeVariant

            class VariantType:
                @staticmethod
                def new(spec):
                    seen['reply_type'] = spec
                    return spec

        monkeypatch.setitem(sys.modules, 'gi.repository.Gio', _FakeGio)
        monkeypatch.setitem(sys.modules, 'gi.repository.GLib', _FakeGLib)
        repo = sys.modules.get('gi.repository')
        if repo is not None:
            monkeypatch.setattr(repo, 'Gio', _FakeGio, raising=False)
            monkeypatch.setattr(repo, 'GLib', _FakeGLib, raising=False)
        return seen

    def test_success_rows(self, fake_gi, monkeypatch):
        import settings_medical as medical
        seen = self._fake_dbus(
            monkeypatch, rows=[('u1', 'Ada', '+100')])
        done = []
        medical.search_contacts('ada', lambda ok, res: done.append((ok, res)))
        assert done == [(True, [('u1', 'Ada', '+100')])]
        assert seen['reply_type'] == '(a(sss))'
        assert seen['args'][3] == 'Search'

    def test_missing_service_fails_soft(self, fake_gi, monkeypatch):
        import settings_medical as medical

        class _BoomGio:
            BusType = SimpleNamespace(SYSTEM=1, SESSION=2)

            @staticmethod
            def bus_get(*args, **kwargs):
                raise Exception('no session bus')

        monkeypatch.setitem(sys.modules, 'gi.repository.Gio', _BoomGio)
        repo = sys.modules.get('gi.repository')
        if repo is not None:
            monkeypatch.setattr(repo, 'Gio', _BoomGio, raising=False)
        done = []
        medical.search_contacts('ada', lambda ok, res: done.append(ok))
        assert done == [False]


class TestMedicalSection:
    def test_build_and_save(self, fake_gi):
        import settings_medical as medical
        saved = {}
        config = SimpleNamespace(
            medical_id={'name': 'Ada', 'blood_type': 'O+',
                        'emergency_contacts': [{'name': 'Bob',
                                                'number': '+100'}]},
            lock_screen_medical_id=True,
            set_medical_id=lambda m: saved.update(medical=m),
            set_lock_screen_medical_id=lambda v: saved.update(show=v))
        win = _bare_win(config=config)
        section = medical.MedicalSection()
        section.build(win)
        assert set(section._entries) == {'name', 'blood_type', 'allergies',
                                         'medications', 'conditions'}
        assert section._entries['name'].get_text() == 'Ada'
        assert section._search_entry is not None
        section._on_save(win)
        assert saved['medical']['name'] == 'Ada'
        assert saved['medical']['blood_type'] == 'O+'
        assert saved['medical']['emergency_contacts'] == [
            {'name': 'Bob', 'number': '+100'}]
        assert 'Saved.' in (section._note.get_text() or '')

    def test_entries_stay_narrow(self, fake_gi):
        import settings_medical as medical
        section = medical.MedicalSection()
        section.build(_bare_win())
        for field, entry in section._entries.items():
            widths = [args[0] for name, args in entry.calls
                      if name == 'width_chars']
            assert widths == [12], field


# --- lock media -----------------------------------------------------------

class TestLockMediaCard:
    def test_locked_card_ignores_title_tap(self, fake_gi):
        import media_controls
        raised = []

        class _Watcher:
            def add_listener(self, _cb):
                return None

            def remove_listener(self, _cb):
                return None

            def active_snapshot(self):
                return media_controls.PlayerSnapshot(
                    bus_name='org.mpris.MediaPlayer2.vlc',
                    identity='VLC', desktop_entry='vlc',
                    status='Paused', title='Song', artist='A', album='',
                    can_go_next=True, can_go_previous=True,
                    can_play=True, can_pause=True)

            def raise_player(self, _bus):
                raised.append(_bus)
                return True

        from config import THEME_PRESETS
        config = SimpleNamespace(theme=THEME_PRESETS['amoled'])
        card = media_controls.MediaCard(watcher=_Watcher(), config=config,
                                        locked=True)
        card._on_title_pressed(None, 1, 0.0, 0.0)
        assert raised == []

    def test_unlocked_card_still_raises(self, fake_gi):
        import media_controls
        raised = []

        class _Watcher:
            def add_listener(self, _cb):
                return None

            def remove_listener(self, _cb):
                return None

            def active_snapshot(self):
                return media_controls.PlayerSnapshot(
                    bus_name='org.mpris.MediaPlayer2.vlc',
                    identity='VLC', desktop_entry='',
                    status='Paused', title='Song', artist='A', album='',
                    can_go_next=True, can_go_previous=True,
                    can_play=True, can_pause=True)

            def raise_player(self, _bus):
                raised.append(_bus)
                return True

        from config import THEME_PRESETS
        config = SimpleNamespace(theme=THEME_PRESETS['amoled'])
        card = media_controls.MediaCard(watcher=_Watcher(), config=config)
        card._on_title_pressed(None, 1, 0.0, 0.0)
        assert raised == ['org.mpris.MediaPlayer2.vlc']

    def test_lock_screen_embeds_locked_card(self):
        text = (SRC / 'lock_screen.py').read_text(encoding='utf-8')
        assert 'MediaCard(' in text
        assert 'locked=True' in text
        assert 'media_watcher' in text

    def test_window_passes_shared_watcher(self):
        text = (SRC / 'window.py').read_text(encoding='utf-8')
        assert 'media_watcher=mpris' in text


# --- launch attribution ----------------------------------------------------

class TestLaunchScopes:
    def test_app_index_launch_tracks_scope(self, fake_gi):
        import app_index
        seen = {}

        class _Info:
            def launch_uris_as_manager(self, uris, *args):
                callback = args[4]
                callback(self, 4321, None)
                seen['launched'] = True
                return True

        class _Tracker:
            def thaw_for_launch(self, app_id):
                seen['thaw'] = app_id

            def track(self, app_id, pid):
                seen['track'] = (app_id, pid)

        from app_index import AppEntry
        entry = AppEntry(app_id='org.example.App.desktop', name='App',
                         description='', executable='', search_text='',
                         desktop_app=_Info())
        ok, error = app_index.AppIndex().launch(entry, tracker=_Tracker())
        assert (ok, error) == (True, None)
        assert seen['thaw'] == 'org.example.App.desktop'
        assert seen['track'] == ('org.example.App.desktop', 4321)

    def test_window_launch_path_tracks_scope(self):
        text = (SRC / 'window.py').read_text(encoding='utf-8')
        assert 'tracker.track(app_id, pid)' in text


# --- registry / install / guards ------------------------------------------

class TestSectionRegistry:
    def test_new_keys_registered_gestures_last(self):
        import settings_page
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert keys[-1] == 'gestures'
        for key in ('charging', 'batteryuse', 'screentime', 'medical'):
            assert key in keys
            assert keys.count(key) == 1

    def test_deep_link_keys_usable(self):
        import settings_page
        for key in ('charging', 'batteryuse', 'screentime', 'medical'):
            assert key in settings_page._SETTINGS_SECTION_KEYS

    def test_each_section_builds_title_then_card(self, fake_gi):
        import settings_page
        win = _bare_win()
        for key, cls in settings_page.SETTINGS_SECTIONS:
            if key in ('charging', 'batteryuse', 'screentime', 'medical'):
                widgets = cls().build(win)
                assert len(widgets) == 2, key
                assert 'section-title' in widgets[0].classes, key
                assert 'settings-card' in widgets[1].classes, key

    def test_all_strings_phone_sized(self, fake_gi):
        import settings_page
        win = _bare_win()
        for key, cls in settings_page.SETTINGS_SECTIONS:
            if key in ('charging', 'batteryuse', 'screentime', 'medical'):
                for widget in cls().build(win):
                    for node in _walk(widget):
                        for name, args in node.calls:
                            if name == 'max_width_chars':
                                assert args[0] <= 26, (key, args)
                            if name == 'width_chars':
                                assert args[0] <= 12, (key, args)


class TestDeepLinks:
    def test_open_settings_refreshes_new_sections(self, fake_gi):
        import settings_page
        stack = SimpleNamespace(visible=None,
                                set_visible_child_name=lambda n: setattr(
                                    stack, 'visible', n))
        scrolled = []
        win = _bare_win(stack=stack,
                        present=lambda: scrolled.append(1),
                        _scroll_settings_to=lambda key: False,
                        _refresh_wifi=lambda: None,
                        _refresh_bluetooth=lambda: None)
        page = settings_page.SettingsPage(win)
        for key in ('charging', 'batteryuse', 'screentime'):
            page.open_settings(key)
            assert stack.visible == 'settings'
            assert scrolled, key
        page.open_settings('nope')
        assert stack.visible == 'settings'


class TestInstallSet:
    def test_meson_lists_new_sections(self):
        meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
        for name in ('settings_charging.py', 'settings_battery_use.py',
                     'settings_screentime.py', 'settings_medical.py'):
            assert f"'src/{name}'" in meson

    def test_meson_set_still_equals_tree(self):
        import re as _re
        text = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
        meson_set = set(_re.findall(r"'(src/[^'/]+\.py)'", text))
        tree_set = {f'src/{p.name}' for p in SRC.glob('*.py')
                    if p.is_file()}
        assert meson_set == tree_set


class TestMainLoopGuards:
    _FILES = ('settings_charging.py', 'settings_battery_use.py',
              'settings_screentime.py', 'settings_medical.py')

    def test_no_blocking_calls(self):
        for name in self._FILES:
            text = (SRC / name).read_text(encoding='utf-8')
            assert 'call_sync(' not in text, name
            assert 'subprocess' not in text, name
            assert 'time.sleep' not in text, name
            assert 'grab_focus' not in text, name

    def test_gtk_signals_valid(self):
        import re as _re
        connected = set()
        for name in self._FILES:
            text = (SRC / name).read_text(encoding='utf-8')
            connected.update(_re.findall(
                r"""\.connect\(\s*['"]([a-z0-9_-]+)(?:::[a-z0-9_-]+)?['"]""",
                text))
        assert connected <= {'clicked', 'changed', 'activate',
                             'notify::active', 'map', 'unmap'}, connected

    def test_medical_reply_type_is_variant(self):
        text = (SRC / 'settings_medical.py').read_text(encoding='utf-8')
        assert 'VariantType.new(' in text
        assert "reply_type" not in text or 'VariantType' in text


class TestBackupKeys:
    def test_round_trip_new_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        from backup import export_backup, restore_backup
        from config import ShellConfig
        config = ShellConfig()
        config.set_charge_limit(80)
        config.set_app_time_limit('a.desktop', 60)
        config.set_app_limit_ignored('a.desktop', '2026-10-16')
        backup = export_backup(config)
        assert backup['charge_limit'] == 80
        assert backup['app_time_limits'] == {'a.desktop': 60}
        assert backup['app_limit_ignored'] == {'a.desktop': '2026-10-16'}

        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        assert restore_backup(fresh, backup) is True
        assert fresh.charge_limit == 80
        assert fresh.app_time_limits == {'a.desktop': 60}
        assert export_backup(fresh) == backup

    def test_bad_keys_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        from backup import validate_backup
        ok, _err = validate_backup({'version': 1, 'home_slots': [],
                                    'charge_limit': 10})
        assert ok is False
        ok, _err = validate_backup({'version': 1, 'home_slots': [],
                                    'app_time_limits': ['nope']})
        assert ok is False
