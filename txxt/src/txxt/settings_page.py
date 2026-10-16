"""Settings as an ``Adw.PreferencesDialog``.

Every Android setting with a real Linux effect (``ui/SettingsActivity``,
``ui/SettingsStore``, ``ui/FontMode``): lock-screen privacy posture,
alert style, default region, MMS auto-download/auto-reply, theme
auto-sync + preset, font toggle — plus entry rows into Blocking and
Quarantine. Deliberately absent (no Linux equivalent): the notification
sound picker (a sound-theme name would not ring anything here) and the
channel-generation hack.

Phone rules: no entry autofocuses (region stays unfocusable until
tapped), header bars hide desktop window buttons — the dialog owns its
own header.
"""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from txxt import settings as settings_mod  # noqa: E402
from txxt import theme as txxt_theme  # noqa: E402
from txxt.conversations import tap_to_focus  # noqa: E402

LOCK_PRIVACY_OPTIONS = (
    ('sender-only', 'Sender only'),
    ('content', 'Content'),
    ('nothing', 'Nothing'),
)

ALERT_STYLE_OPTIONS = (
    ('silent', 'Silent'),
    ('vibrate', 'Vibrate'),
    ('sound', 'Sound'),
)

FONT_MODE_OPTIONS = (
    ('space-mono', 'Space Mono'),
    ('jetbrains-mono', 'JetBrains Mono'),
    ('system', 'System'),
)


def _labels(options) -> list[str]:
    return [label for _value, label in options]


def _index_of(options, value: str) -> int:
    for index, (option_value, _label) in enumerate(options):
        if option_value == value:
            return index
    return 0


class SettingsDialog(Adw.PreferencesDialog):
    """Preferences dialog over the txxt JSON config + rule-table counts."""

    def __init__(
        self, store, on_changed=None,
        on_open_blocking=None, on_open_quarantine=None,
    ) -> None:
        super().__init__()
        self._store = store
        self._on_changed = on_changed
        self._on_open_blocking = on_open_blocking
        self._on_open_quarantine = on_open_quarantine
        self._settings = settings_mod.load()
        self._ready = False
        self.set_title('Settings')

        notifications = Adw.PreferencesGroup()
        notifications.set_title('Notifications')
        self._privacy_row = Adw.ComboRow()
        self._privacy_row.set_title('Lock-screen privacy')
        self._privacy_row.set_model(Gtk.StringList.new(_labels(LOCK_PRIVACY_OPTIONS)))
        self._privacy_row.set_selected(_index_of(LOCK_PRIVACY_OPTIONS, self._settings.lock_screen_privacy))
        self._privacy_row.connect('notify::selected', self._on_privacy_changed)
        notifications.add(self._privacy_row)
        self._alert_row = Adw.ComboRow()
        self._alert_row.set_title('Alert style')
        self._alert_row.set_model(Gtk.StringList.new(_labels(ALERT_STYLE_OPTIONS)))
        self._alert_row.set_selected(_index_of(ALERT_STYLE_OPTIONS, self._settings.alert_style))
        self._alert_row.connect('notify::selected', self._on_alert_changed)
        notifications.add(self._alert_row)

        messages = Adw.PreferencesGroup()
        messages.set_title('Messages')
        self._region_row = Adw.EntryRow()
        self._region_row.set_title('Default region')
        self._region_row.set_text(self._settings.default_region)
        tap_to_focus(self._region_row)
        self._region_row.connect('changed', self._on_region_changed)
        messages.add(self._region_row)
        self._auto_download_row = Adw.SwitchRow()
        self._auto_download_row.set_title('Download MMS photos automatically')
        self._auto_download_row.set_subtitle('Off keeps tap-to-fetch')
        self._auto_download_row.set_active(self._settings.mms_auto_download)
        self._auto_download_row.connect('notify::active', self._on_auto_download_changed)
        messages.add(self._auto_download_row)
        self._auto_reply_row = Adw.SwitchRow()
        self._auto_reply_row.set_title('Auto-reply to dropped voice MMS')
        self._auto_reply_row.set_active(self._settings.mms_auto_reply)
        self._auto_reply_row.connect('notify::active', self._on_auto_reply_changed)
        messages.add(self._auto_reply_row)

        appearance = Adw.PreferencesGroup()
        appearance.set_title('Appearance')
        self._auto_sync_row = Adw.SwitchRow()
        self._auto_sync_row.set_title('Follow the shell theme')
        self._auto_sync_row.set_active(self._settings.auto_sync_theme)
        self._auto_sync_row.connect('notify::active', self._on_auto_sync_changed)
        appearance.add(self._auto_sync_row)
        preset_options = [
            (key, txxt_theme.THEME_PRESETS[key]['display']) for key in txxt_theme.THEME_PRESETS
        ]
        self._preset_row = Adw.ComboRow()
        self._preset_row.set_title('Theme preset')
        self._preset_row.set_model(Gtk.StringList.new(_labels(preset_options)))
        self._preset_row.set_selected(_index_of(
            preset_options, txxt_theme.normalize_preset_key(self._settings.theme_preset),
        ))
        self._preset_row.connect('notify::selected', self._on_preset_changed)
        appearance.add(self._preset_row)
        self._font_row = Adw.ComboRow()
        self._font_row.set_title('Font')
        self._font_row.set_model(Gtk.StringList.new(_labels(FONT_MODE_OPTIONS)))
        self._font_row.set_selected(_index_of(FONT_MODE_OPTIONS, self._settings.font_mode))
        self._font_row.connect('notify::selected', self._on_font_changed)
        appearance.add(self._font_row)
        self._preset_options = preset_options

        filtering = Adw.PreferencesGroup()
        filtering.set_title('Filtering')
        self._blocking_row = Adw.ActionRow()
        self._blocking_row.set_title('Blocking')
        self._blocking_row.set_activatable(True)
        self._blocking_row.connect('activated', self._on_blocking_activated)
        filtering.add(self._blocking_row)
        self._quarantine_row = Adw.ActionRow()
        self._quarantine_row.set_title('Quarantine')
        self._quarantine_row.set_activatable(True)
        self._quarantine_row.connect('activated', self._on_quarantine_activated)
        filtering.add(self._quarantine_row)

        for group in (notifications, messages, appearance, filtering):
            page = Adw.PreferencesPage()
            page.add(group)
            self.add(page)
        self._sync_manual_sensitivity()
        self.refresh_subtitles()
        self._ready = True

    # ------------------------------------------------------------ display

    def refresh_subtitles(self) -> None:
        """Re-read rule-table counts (call after blocking/quarantine edits)."""
        try:
            senders = self._store.blocklist_list('sender')
            keywords = self._store.blocklist_list('keyword')
            phrases = self._store.blocklist_list('phrase')
        except Exception:
            senders, keywords, phrases = [], [], []
        self._blocking_row.set_subtitle(
            f'{len(senders)} numbers, {len(keywords) + len(phrases)} terms',
        )
        try:
            held = sum(
                1 for row in self._store.get_all_conversations()
                if row.is_quarantined
            )
        except Exception:
            held = 0
        self._quarantine_row.set_subtitle(
            f'{held} held' if held else 'Empty',
        )

    # ------------------------------------------------------------ persist

    def _save(self) -> None:
        try:
            settings_mod.save(self._settings)
        except Exception:
            return
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass

    def _sync_manual_sensitivity(self) -> None:
        manual = not self._settings.auto_sync_theme
        self._preset_row.set_sensitive(manual)
        self._font_row.set_sensitive(manual)

    def _on_privacy_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.lock_screen_privacy = LOCK_PRIVACY_OPTIONS[row.get_selected()][0]
        self._save()

    def _on_alert_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.alert_style = ALERT_STYLE_OPTIONS[row.get_selected()][0]
        self._save()

    def _on_region_changed(self, row) -> None:
        if not self._ready:
            return
        region = row.get_text().strip().upper()
        if len(region) == 2 and region.isalpha():
            self._settings.default_region = region
            self._save()

    def _on_auto_download_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.mms_auto_download = bool(row.get_active())
        self._save()

    def _on_auto_reply_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.mms_auto_reply = bool(row.get_active())
        self._save()

    def _on_auto_sync_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.auto_sync_theme = bool(row.get_active())
        self._sync_manual_sensitivity()
        self._save()

    def _on_preset_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.theme_preset = self._preset_options[row.get_selected()][0]
        self._save()

    def _on_font_changed(self, row, _spec) -> None:
        if not self._ready:
            return
        self._settings.font_mode = FONT_MODE_OPTIONS[row.get_selected()][0]
        self._save()

    def _on_blocking_activated(self, _row) -> None:
        if self._on_open_blocking is not None:
            try:
                self._on_open_blocking()
            except Exception:
                pass

    def _on_quarantine_activated(self, _row) -> None:
        if self._on_open_quarantine is not None:
            try:
                self._on_open_quarantine()
            except Exception:
                pass
