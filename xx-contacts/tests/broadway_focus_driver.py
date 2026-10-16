"""Broadway driver for the L3 round-3 focus tests.

Runs one focus scenario against a real GTK display (the parent pytest
hands it ``GDK_BACKEND=broadway`` plus ``BROADWAY_DISPLAY``) and prints
``KEY: value`` markers on stdout. Any unexpected exception exits
nonzero so the parent test fails instead of passing on empty output.
Not collected by pytest: it is executed as a subprocess by
``tests/test_l3fix3.py``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from xx_contacts.list_ui import install_tap_to_focus  # noqa: E402


def _pump(seconds: float) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def _subtree_has_focus(widget: Gtk.Widget) -> bool:
    if widget.has_focus():
        return True
    child = widget.get_first_child()
    while child is not None:
        if _subtree_has_focus(child):
            return True
        child = child.get_next_sibling()
    return False


def _focus_chain(window: Gtk.Window) -> str:
    names = []
    widget = window.get_focus()
    while widget is not None:
        names.append(type(widget).__name__)
        widget = widget.get_parent()
    return ">".join(names)


def _tap(entry: Gtk.Widget) -> None:
    model = entry.observe_controllers()
    for index in range(model.get_n_items()):
        controller = model.get_item(index)
        if isinstance(controller, Gtk.GestureClick):
            assert (controller.get_propagation_phase()
                    == Gtk.PropagationPhase.CAPTURE)
            controller.emit("pressed", 1, 5.0, 5.0)
            return
    raise AssertionError("no GestureClick on entry")


def _scenario_startup() -> None:
    window = Gtk.Window()
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    print("STARTUP_CAN_FOCUS:", entry.get_can_focus())
    window.set_child(entry)
    window.present()
    _pump(1.2)
    print("STARTUP_INSIDE:", _subtree_has_focus(entry))
    window.destroy()


def _scenario_tap() -> None:
    window = Gtk.Window()
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    window.set_child(entry)
    window.present()
    _pump(1.2)
    print("TAP_BEFORE:", _subtree_has_focus(entry))
    _tap(entry)
    _pump(0.6)
    print("TAP_CAN_FOCUS:", entry.get_can_focus())
    print("TAP_INSIDE:", _subtree_has_focus(entry))
    print("TAP_CHAIN:", _focus_chain(window))
    window.destroy()


def _scenario_relock() -> None:
    window = Gtk.Window()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    other = Gtk.Button(label="Other")
    box.append(entry)
    box.append(other)
    window.set_child(box)
    window.present()
    _pump(1.2)
    _tap(entry)
    _pump(0.6)
    print("RELOCK_TAPPED_INSIDE:", _subtree_has_focus(entry))
    other.grab_focus()
    _pump(0.6)
    print("RELOCK_INSIDE:", _subtree_has_focus(entry))
    print("RELOCK_CAN_FOCUS:", entry.get_can_focus())
    window.destroy()


def _scenario_navpop() -> None:
    window = Gtk.Window()
    navigation = Adw.NavigationView()
    toolbar = Adw.ToolbarView()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    box.append(entry)
    toolbar.set_content(box)
    navigation.push(Adw.NavigationPage(child=toolbar, title="List"))
    window.set_child(navigation)
    window.present()
    _pump(1.2)
    print("NAVPOP_STARTUP_INSIDE:", _subtree_has_focus(entry))
    _tap(entry)
    _pump(0.6)
    print("NAVPOP_TAPPED_INSIDE:", _subtree_has_focus(entry))
    detail = Adw.NavigationPage(child=Gtk.Label(label="Detail"),
                                title="Detail")
    navigation.push(detail)
    _pump(0.6)
    navigation.pop()
    _pump(0.6)
    print("NAVPOP_AFTER_POP_INSIDE:", _subtree_has_focus(entry))
    window.destroy()


_SCENARIOS = {
    "startup": _scenario_startup,
    "tap": _scenario_tap,
    "relock": _scenario_relock,
    "navpop": _scenario_navpop,
}


def main(argv: list) -> None:
    Gtk.init()
    scenario = _SCENARIOS[argv[1]]
    scenario()


main(sys.argv)
