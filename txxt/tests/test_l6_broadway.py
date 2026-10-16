"""Broadway L6 smoke: settings dialog, blocking + quarantine pages.

Seeds a temp store with realistic data (Android-style millisecond ids,
a blocked number, keyword/phrase terms, one quarantined thread), builds
the real :class:`TxxtWindow`, opens the settings dialog, the blocking
page and the quarantine page, flips a theme control, and pumps the main
loop without exceptions or Gtk warnings. Skips only when
``gtk4-broadwayd`` is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

import pytest  # noqa: E402

CID_Q = 1792142432343
MID_Q_IN = 1792142432401
CID_A = 1792142430343
MID_A_IN = 1792142430401


def _seed(store):
    from txxt.store import ConversationRow, MessageRow

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(
        id=CID_A, participant_addresses="+15551230001",
    ))
    store.upsert_conversation(ConversationRow(
        id=CID_Q, participant_addresses="+15559990001", is_quarantined=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_IN, conversation_id=CID_A, direction="INCOMING",
        transport="SMS", body="hello inbound", timestamp_millis=now - 60000,
        sender_address="+15551230001", is_read=False, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_Q_IN, conversation_id=CID_Q, direction="INCOMING",
        transport="SMS", body="spammy hello", timestamp_millis=now - 40000,
        sender_address="+15559990001", is_read=False, sent=True,
    ))
    store.blocklist_add("sender", "+15550001111")
    store.blocklist_add("keyword", "loan")
    store.blocklist_add("phrase", "act now")


def _child_main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gio, GLib, Gtk

    from txxt.main import TxxtWindow
    from txxt.settings import load as load_settings

    errors: list = []
    gtk_logs: list = []
    old_excepthook = sys.excepthook
    old_threadhook = threading.excepthook
    sys.excepthook = lambda t, v, tb: errors.append(f"{t.__name__}: {v}")
    threading.excepthook = lambda args: errors.append(
        f"{type(args.exc_value).__name__}: {args.exc_value}")

    def _on_log(domain, level, message, _data):
        gtk_logs.append(f"{domain} {level}: {message}")

    handler_id = GLib.log_set_handler(
        "Gtk",
        GLib.LogLevelFlags.LEVEL_CRITICAL
        | GLib.LogLevelFlags.LEVEL_WARNING
        | GLib.LogLevelFlags.LEVEL_ERROR,
        _on_log,
    )

    def pump(seconds):
        context = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.02)
            context.iteration(False)
        assert errors == [], f"exceptions during main-loop pump: {errors!r}"

    def focus_inside_entry(window):
        widget = window.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Editable):
                return True
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return False

    window = None
    app = None
    try:
        app = Adw.Application(
            application_id="io.piercingxx.TxxT.L6Smoke",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        app.register(None)
        window = TxxtWindow(app)
        window.present()
        pump(1.2)
        assert window._list_page._quarantine_banner.get_visible() is True
        assert window._list_page._quarantine_banner.get_label() == "Quarantine (1)"
        assert focus_inside_entry(window) is False

        dialog = window.open_settings()
        pump(0.8)
        dialog.refresh_subtitles()
        assert dialog._blocking_row.get_subtitle() == "1 numbers, 2 terms"
        assert dialog._quarantine_row.get_subtitle() == "1 held"
        assert load_settings().theme_preset == "amoled"
        dialog._auto_sync_row.set_active(False)
        keys = [key for key, _label in dialog._preset_options]
        dialog._preset_row.set_selected(keys.index("forest"))
        pump(0.8)
        assert load_settings().auto_sync_theme is False
        assert load_settings().theme_preset == "forest"
        dialog.close()
        pump(0.5)

        window.open_blocking()
        pump(1.2)
        assert window._blocking_page is not None
        assert window._blocking_page._lists["sender"].get_row_at_index(0) is not None
        assert window._blocking_page._lists["keyword"].get_row_at_index(0) is not None
        assert window._blocking_page._lists["phrase"].get_row_at_index(0) is not None
        assert focus_inside_entry(window) is False

        window.open_quarantine()
        pump(1.2)
        assert window._quarantine_page is not None
        assert window._quarantine_page._model.get_n_items() == 1
        item = window._quarantine_page._model.get_item(0)
        assert int(item.conversation_id) == CID_Q > 2_147_483_647
        assert item.title.strip() != ""
        assert focus_inside_entry(window) is False

        assert errors == []
        assert gtk_logs == [], f"Gtk warnings during smoke: {gtk_logs!r}"
    finally:
        GLib.log_remove_handler("Gtk", handler_id)
        sys.excepthook = old_excepthook
        threading.excepthook = old_threadhook
        if window is not None:
            try:
                window._store.close()
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass
        if app is not None:
            try:
                app.quit()
            except Exception:
                pass
        context = GLib.MainContext.default()
        for _ in range(20):
            if not context.pending():
                break
            context.iteration(False)
    print("SMOKE-OK", flush=True)


def _free_display():
    for number in range(5, 100):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 8080 + number))
        except OSError:
            probe.close()
            continue
        probe.close()
        return number
    return None


def test_broadway_l6_settings_blocking_quarantine():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    from txxt.main import ensure_data_dir
    from txxt.store import Store

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.TemporaryDirectory(prefix="txxt-l6-broadway")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    old_config = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    os.environ["XDG_CONFIG_HOME"] = tmp.name
    proc = None
    try:
        seed_store = Store(str(ensure_data_dir() / "txxt.db"))
        try:
            _seed(seed_store)
        finally:
            seed_store.close()
        number = None
        for _ in range(5):
            candidate = _free_display()
            if candidate is None:
                break
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{candidate}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            if proc.poll() is None:
                number = candidate
                break
            proc = None
        if number is None:
            pytest.fail("could not start gtk4-broadwayd on any display 5..99")
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{number}"
        env["XDG_DATA_HOME"] = tmp.name
        env["XDG_CONFIG_HOME"] = tmp.name
        env["PYTHONPATH"] = os.path.join(repo_root, "src") + os.pathsep + env.get(
            "PYTHONPATH", "")
        child = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert child.returncode == 0, (
            f"broadway child failed (exit {child.returncode}):\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
        assert "SMOKE-OK" in child.stdout
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg
        if old_config is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_config
        tmp.cleanup()


if __name__ == "__main__" and "--child" in sys.argv:
    _child_main()
