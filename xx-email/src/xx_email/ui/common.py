"""Shared GTK helpers for the mailbox stack (thin over mailbox.py).

Every widget here stays a thin view: formatting and mutations live in
xx_email.mailbox / xx_email.bodies (stdlib, pytest-covered). Blocking
store/network work always runs on the worker executor with results
posted back via GLib.idle_add — never on the GTK main loop.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from xx_email import mailbox as mailbox_mod


class ThreadItem(GObject.Object):
    """One ListView row's fields (ListStore needs GObject items)."""

    __gtype_name__ = "XXEmailThreadItem"

    thread_id = GObject.Property(type=str, default="")
    sender = GObject.Property(type=str, default="")
    subject = GObject.Property(type=str, default="")
    snippet = GObject.Property(type=str, default="")
    age = GObject.Property(type=str, default="")
    unread = GObject.Property(type=bool, default=False)
    starred = GObject.Property(type=bool, default=False)
    attached = GObject.Property(type=bool, default=False)
    selected = GObject.Property(type=bool, default=False)

    @classmethod
    def from_thread(cls, thread) -> "ThreadItem":
        item = cls()
        item.thread_id = thread.id
        name = (thread.from_name or "").strip()
        item.sender = name or thread.from_address
        item.subject = thread.subject or "(no subject)"
        item.snippet = thread.snippet or ""
        item.age = mailbox_mod.time_ago_now(thread.date)
        item.unread = thread.unread_count > 0
        item.starred = bool(thread.starred)
        item.attached = bool(thread.has_attachments)
        item.selected = False
        return item


class MessageItem(GObject.Object):
    """One thread-view row's fields."""

    __gtype_name__ = "XXEmailMessageItem"

    message_id = GObject.Property(type=str, default="")
    sender = GObject.Property(type=str, default="")
    to_line = GObject.Property(type=str, default="")
    date = GObject.Property(type=str, default="")
    body = GObject.Property(type=str, default="")
    unread = GObject.Property(type=bool, default=False)


def phone_header(title: str) -> Adw.HeaderBar:
    """Header without minimize/maximize buttons (phone has none).

    Only the close affordance stays: start/end title buttons off, so
    page actions are explicit text buttons packed by each page.
    """
    header = Adw.HeaderBar()
    header.set_title_widget(Gtk.Label(label=title))
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)
    return header


def text_button(label: str, tooltip: str | None = None) -> Gtk.Button:
    """Text-first button (no icon grids on this UI)."""
    button = Gtk.Button(label=label)
    if tooltip is not None:
        button.set_tooltip_text(tooltip)
    return button


def ellipsized(label: Gtk.Label) -> Gtk.Label:
    """Single-line label that shrinks instead of overflowing 320 px."""
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_wrap(False)
    label.set_lines(1)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    return label


def wrapped(label: Gtk.Label) -> Gtk.Label:
    """Body label that wraps inside 360/320 px instead of overflowing."""
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    return label


def run_in_background(executor, blocking, done) -> None:
    """Run blocking() on the worker pool; done(result) via idle_add.

    Failures arrive as the exception object so pages can banner them.
    """
    future = executor.submit(blocking)

    def _finish(completed) -> None:
        try:
            result = completed.result()
        except Exception as exc:  # surface, never throw on the loop
            result = exc
        GLib.idle_add(done, result)

    future.add_done_callback(_finish)


def new_thread_store(items: list | None = None) -> Gio.ListStore:
    store = Gio.ListStore.new(ThreadItem)
    for thread in items or []:
        store.append(ThreadItem.from_thread(thread))
    return store


def replace_store(store: Gio.ListStore, threads: list) -> None:
    store.remove_all()
    for thread in threads:
        store.append(ThreadItem.from_thread(thread))


def find_item(store: Gio.ListStore, thread_id: str):
    for position in range(store.get_n_items()):
        item = store.get_item(position)
        if item.thread_id == thread_id:
            return item
    return None


def make_thread_row() -> tuple:
    """Build one mailbox row's widgets (factory setup, once per row)."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.add_css_class("email-row")
    row.set_margin_start(12)
    row.set_margin_end(12)
    row.set_margin_top(10)
    row.set_margin_bottom(10)

    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    texts.set_hexpand(True)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    sender = ellipsized(Gtk.Label())
    sender.add_css_class("email-sender")
    age = Gtk.Label()
    age.add_css_class("email-muted")
    top.append(sender)
    top.append(age)
    subject = ellipsized(Gtk.Label())
    snippet = ellipsized(Gtk.Label())
    snippet.add_css_class("email-muted")
    texts.append(top)
    texts.append(subject)
    texts.append(snippet)
    row.append(texts)

    clip = Gtk.Label(label="[file]")
    clip.set_visible(False)
    row.append(clip)

    star = text_button("*", "Star toggle")
    star.add_css_class("flat")
    row.append(star)
    return (row, sender, age, subject, snippet, clip, star)


def bind_thread_row(widgets: tuple, item: ThreadItem,
                    show_star: bool) -> None:
    _, sender, age, subject, snippet, clip, star = widgets
    sender.set_label(item.sender)
    sender.add_css_class("email-unread" if item.unread else "email-read")
    sender.remove_css_class("email-read" if item.unread else "email-unread")
    age.set_label(item.age)
    subject.set_label(item.subject)
    snippet.set_label(item.snippet)
    clip.set_visible(item.attached)
    star.set_visible(show_star)
    star.set_label("Unstar" if item.starred else "Star")


def thread_list_view(store: Gio.ListStore, on_open, on_toggle_select,
                     on_swipe, on_star, swipe_left: str,
                     swipe_right: str) -> Gtk.ListView:
    """Gtk.ListView for a thread list (long lists stay list-backed)."""
    factory = Gtk.SignalListItemFactory()

    def _setup(_factory, list_item) -> None:
        widgets = make_thread_row()
        row = widgets[0]
        list_item.set_child(row)

        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, x, y:
                      _on_row_click(list_item, n))
        row.add_controller(click)

        if on_toggle_select is not None:
            hold = Gtk.GestureLongPress()
            hold.connect("pressed", lambda g, x, y:
                         _on_row_hold(list_item))
            row.add_controller(hold)

        if on_swipe is not None:
            swipe = Gtk.GestureSwipe()
            swipe.connect("swipe", lambda g, vx, vy:
                          _on_row_swipe(list_item, vx))
            row.add_controller(swipe)

        _, _, _, _, _, _, star = widgets
        star.connect("clicked", lambda _b: _on_star(list_item))

    def _on_row_click(list_item, n_press: int) -> None:
        item = list_item.get_item()
        if item is not None:
            on_open(item.thread_id, n_press)

    def _on_row_hold(list_item) -> None:
        item = list_item.get_item()
        if item is not None:
            on_toggle_select(item.thread_id)

    def _on_row_swipe(list_item, velocity_x: float) -> None:
        item = list_item.get_item()
        if item is None:
            return
        action = swipe_right if velocity_x > 0 else swipe_left
        on_swipe(action, item.thread_id)

    def _on_star(list_item) -> None:
        item = list_item.get_item()
        if item is not None and on_star is not None:
            on_star(item.thread_id)

    def _bind(_factory, list_item) -> None:
        item = list_item.get_item()
        bind_thread_row(_row_widgets(list_item.get_child()), item,
                        on_star is not None)

    factory.connect("setup", _setup)
    factory.connect("bind", _bind)
    view = Gtk.ListView(model=Gtk.NoSelection.new(store), factory=factory)
    view.set_single_click_activate(True)
    view.add_css_class("email-listview")
    return view


def _row_widgets(row):
    texts = row.get_first_child()
    top = texts.get_first_child()
    sender = top.get_first_child()
    age = sender.get_next_sibling()
    subject = top.get_next_sibling()
    snippet = subject.get_next_sibling()
    clip = texts.get_next_sibling()
    star = clip.get_next_sibling()
    return (row, sender, age, subject, snippet, clip, star)
