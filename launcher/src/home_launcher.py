"""
Home screen launcher — curated app/folder list.
Folders expand in-place on tap; apps launch immediately.
Android apps launch via waydroid when available.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

_HOME_CSS = b"""
.home-item-btn {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 18px 0;
    color: #f4f4f4;
}
.home-item-btn:hover, .home-item-btn:active {
    background: transparent;
    color: #9a9a9a;
}
.home-item-label {
    font-size: 34pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
}
.home-folder-indicator {
    font-size: 18pt;
    font-weight: 300;
    color: #5a5a5a;
    margin-left: 6px;
}
.home-child-btn {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 12px 0;
    color: #c0c0c0;
}
.home-child-btn:hover, .home-child-btn:active {
    background: transparent;
    color: #f4f4f4;
}
.home-child-label {
    font-size: 24pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
}
"""


@dataclass
class HomeApp:
    label: str
    cmd: list[str] = field(default_factory=list)
    android_pkg: str = ''
    on_tap: Callable[[], None] | None = None


@dataclass
class HomeFolder:
    label: str
    children: list[HomeApp] = field(default_factory=list)


HomeItem = HomeApp | HomeFolder


import os as _os

_WAYDROID_ENV = {
    **_os.environ,
    'WAYLAND_DISPLAY': 'wayland-0',
    'XDG_RUNTIME_DIR': f'/run/user/{_os.getuid()}',
}


def _waydroid_session_running() -> bool:
    try:
        out = subprocess.check_output(['waydroid', 'status'], text=True, timeout=2,
                                      env=_WAYDROID_ENV, stderr=subprocess.DEVNULL)
        return 'Session:\tRUNNING' in out
    except Exception:
        return False


def _launch_android(pkg: str) -> None:
    try:
        if not _waydroid_session_running():
            # Start the session in background; app will come up once it's ready
            subprocess.Popen(
                ['waydroid', 'session', 'start'],
                env=_WAYDROID_ENV, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Give the session a moment before launching the app
            from gi.repository import GLib
            GLib.timeout_add(5000, lambda p=pkg: _do_launch_android(p) or False)
            return
        _do_launch_android(pkg)
    except FileNotFoundError:
        pass


def _do_launch_android(pkg: str) -> None:
    try:
        subprocess.Popen(
            ['waydroid', 'app', 'launch', pkg],
            env=_WAYDROID_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _launch_cmd(cmd: list[str]) -> None:
    try:
        subprocess.Popen(cmd, close_fds=True, env=_WAYDROID_ENV)
    except FileNotFoundError:
        pass


# Curated home screen layout
HOME_ITEMS: list[HomeItem] = [
    HomeApp(
        label='Notes',
        cmd=['kitty', 'nvim'],
    ),
    HomeFolder(
        label='Audio',
        children=[
            HomeApp(label='Audiobook', android_pkg='com.audiobookshelf.app'),
            HomeApp(label='Music',     android_pkg='com.google.android.apps.youtube.music'),
        ],
    ),
    HomeFolder(
        label='Comms',
        children=[
            HomeApp(label='Phone'),
            HomeApp(label='Text',       cmd=['chatty']),
            HomeApp(label='Email',      android_pkg='com.google.android.gm'),
            HomeApp(label='Chat',       android_pkg='com.synology.chat'),
            HomeApp(label='Softphone',  android_pkg='com.cloudsoftphone'),
        ],
    ),
    HomeApp(
        label='Calendar',
        android_pkg='com.google.android.calendar',
    ),
    HomeFolder(
        label='Tools',
        children=[
            HomeApp(label='Firefox',    cmd=['flatpak', 'run', 'org.mozilla.firefox']),
            HomeApp(label='Calculator', cmd=['gnome-calculator']),
            HomeApp(label='Camera',     cmd=['megapixels']),
            HomeApp(label='Photos',     android_pkg='com.synology.photo'),
        ],
    ),
]


class HomeLauncher(Gtk.Box):
    """Vertical list of home screen items with in-place folder expansion."""

    def __init__(self, open_dialer_fn: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self._open_dialer = open_dialer_fn
        self._expanded: set[str] = set()
        self._build()

    def _build(self) -> None:
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        for item in HOME_ITEMS:
            if isinstance(item, HomeApp):
                self.append(self._make_app_btn(item, child=False))
            else:
                self._add_folder(item)

    def _add_folder(self, folder: HomeFolder) -> None:
        expanded = folder.label in self._expanded

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.CENTER)

        lbl = Gtk.Label(label=folder.label)
        lbl.add_css_class('home-item-label')
        ind = Gtk.Label(label='▾' if expanded else '›')
        ind.add_css_class('home-folder-indicator')

        row.append(lbl)
        row.append(ind)

        btn = Gtk.Button()
        btn.add_css_class('home-item-btn')
        btn.set_hexpand(True)
        btn.set_child(row)
        btn.connect('clicked', lambda _b, f=folder: self._toggle_folder(f))
        self.append(btn)

        if expanded:
            for child in folder.children:
                self.append(self._make_app_btn(child, child=True))

    def _toggle_folder(self, folder: HomeFolder) -> None:
        if folder.label in self._expanded:
            self._expanded.discard(folder.label)
        else:
            self._expanded.add(folder.label)
        self._build()

    def _make_app_btn(self, app: HomeApp, child: bool) -> Gtk.Button:
        lbl = Gtk.Label(label=app.label)
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.add_css_class('home-child-label' if child else 'home-item-label')

        btn = Gtk.Button()
        btn.add_css_class('home-child-btn' if child else 'home-item-btn')
        btn.set_hexpand(True)
        btn.set_child(lbl)
        btn.connect('clicked', lambda _b, a=app: self._tap_app(a))
        return btn

    def _tap_app(self, app: HomeApp) -> None:
        if app.on_tap:
            app.on_tap()
        elif app.label == 'Phone' and self._open_dialer:
            self._open_dialer()
        elif app.android_pkg:
            _launch_android(app.android_pkg)
        elif app.cmd:
            _launch_cmd(app.cmd)
