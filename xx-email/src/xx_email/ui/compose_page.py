"""Compose page: recipients, subject, plain-text body, attachments (thin view).

Ports ComposeScreen/ComposeViewModel over xx_email.compose (pure)
and xx_email.outbox (worker pipeline). Recipient autocomplete reads
xx-contacts' D-Bus Search with the read-only EDS fallback; files are
picked with the portal file chooser (Gtk.FileDialog); schedule-send
offers the Gmail-style presets. All store/network work enters
through hooks on worker threads — this page only shows state.

Focus rule: recipient/subject/body fields never take focus on their
own, so the on-screen keyboard stays down until a tap. Each field
enables + focuses only on a CAPTURE-phase tap. The single exception
is the first empty recipient field when the user explicitly opens
compose: opening compose is an explicit intent to type, so that one
field stays focusable and takes focus on show. A prefilled reply To
(or any non-empty first field) takes nothing.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from datetime import datetime

from gi.repository import Adw, Gio, GLib, Gtk

from xx_email import compose as compose_mod
from xx_email import contacts as contacts_mod
from xx_email.ui import common as common_mod
from xx_ui import widgets

SUGGEST_LIMIT = 8
AUTOSAVE_MS = 1200
SUGGEST_MS = 250


def _token(text: str) -> str:
    """Address fragment being typed (after the last comma/semicolon)."""
    fragment = (text or "").replace(";", ",").rsplit(",", 1)[-1]
    return fragment.strip()


class ComposePage(Gtk.Box):
    """New / reply / reply-all / forward editor for one account."""

    def __init__(self, account: str, initial: dict, hooks, toast,
                 autofocus_to: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast
        self._busy = False
        self._staged: list[dict] = list(initial.get("staged", []))
        self._offer_count = int(initial.get("offer_count", 0) or 0)
        self._offer_done = self._offer_count <= 0
        self._suggest_timer: int | None = None
        self._suggest_for = None

        mode = initial.get("mode", "new")
        title = {"reply": "Reply", "reply_all": "Reply all",
                 "forward": "Forward"}.get(mode, "Compose")
        self.append(widgets.compact_header(
            lambda: self._on_discard(), title, account))

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form.add_css_class("xx-page-body")
        form.set_margin_top(12)
        form.set_margin_bottom(12)

        self._to = self._recipient_entry(form, "To", initial.get("to",
                                                                 ""))
        self._cc_box, self._cc = self._boxed_recipient(
            form, "Cc", initial.get("cc", ""))
        self._bcc_box, self._bcc = self._boxed_recipient(
            form, "Bcc", initial.get("bcc", ""))
        show_cc = bool(initial.get("cc") or initial.get("bcc")
                       or initial.get("show_cc_bcc"))
        self._cc_box.set_visible(show_cc)
        self._bcc_box.set_visible(show_cc)
        if not show_cc:
            toggle = widgets.text_button("Add Cc/Bcc")
            toggle.connect("clicked", lambda _b: (
                self._cc_box.set_visible(True),
                self._bcc_box.set_visible(True),
                toggle.set_visible(False)))
            form.append(toggle)

        self._suggest = Gtk.ListBox()
        self._suggest.set_selection_mode(Gtk.SelectionMode.NONE)
        self._suggest.set_visible(False)
        form.append(self._suggest)

        form.append(widgets.caption("Subject"))
        self._subject = widgets.text_entry("Subject")
        self._subject.set_hexpand(True)
        self._subject.set_text(initial.get("subject", ""))
        self._subject.connect("changed",
                              lambda _e: self._schedule_autosave())
        common_mod.tap_to_focus(self._subject)
        form.append(self._subject)

        attach_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=12)
        attach = widgets.outline_button("Attach")
        attach.connect("clicked", lambda _b: self._pick_files())
        attach_row.append(attach)
        self._attach_note = widgets.label("", "xx-secondary")
        self._attach_note.set_hexpand(True)
        self._attach_note.set_valign(Gtk.Align.CENTER)
        attach_row.append(self._attach_note)
        form.append(attach_row)

        self._chips = Gtk.FlowBox()
        self._chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self._chips.set_max_children_per_line(2)
        form.append(self._chips)

        self._offer_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        offer = widgets.outline_button(
            f"Include original attachments ({self._offer_count:d})")
        offer.connect("clicked",
                      lambda _b: self._fetch_offered())
        self._offer_row.append(offer)
        decline = widgets.text_button("Skip")
        decline.connect("clicked", lambda _b: (
            self._hooks.decline_offer(),
            self._offer_row.set_visible(False)))
        self._offer_row.append(decline)
        self._offer_row.set_visible(not self._offer_done)
        form.append(self._offer_row)

        form.append(widgets.caption("Message"))
        self._body = Gtk.TextView()
        self._body.add_css_class("xx-entry")
        self._body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._body.set_hexpand(True)
        self._body.set_vexpand(True)
        self._body.get_buffer().set_text(initial.get("body", ""))
        self._body.get_buffer().connect("changed",
                                        lambda _b:
                                        self._schedule_autosave())
        common_mod.tap_to_focus(self._body)
        body_frame = Gtk.ScrolledWindow()
        body_frame.set_policy(Gtk.PolicyType.NEVER,
                              Gtk.PolicyType.AUTOMATIC)
        body_frame.set_min_content_height(180)
        body_frame.set_vexpand(True)
        body_frame.set_child(self._body)
        form.append(body_frame)

        self._error = widgets.label("", "xx-secondary")
        form.append(self._error)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(form)
        self.append(scrolled)

        send_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=12)
        send_row.add_css_class("xx-page-body")
        send_row.set_margin_top(12)
        send_row.set_margin_bottom(20)
        schedule = widgets.outline_button("Schedule")
        schedule.connect("clicked", lambda _b: self._open_schedule())
        send_row.append(schedule)
        self._spinner = Adw.Spinner()
        self._spinner.set_visible(False)
        self._spinner.set_valign(Gtk.Align.CENTER)
        send_row.append(self._spinner)
        send = widgets.primary_button("Send")
        send.set_hexpand(True)
        send.connect("clicked", lambda _b: self._send(None))
        send_row.append(send)
        self.append(send_row)

        self._paint_chips()
        # Explicit-open exception: the first empty recipient field is
        # why the user came here, so it alone takes focus on show.
        # Everything else waits for a tap (see common.tap_to_focus).
        if autofocus_to and not self._to.get_text().strip():
            self._to.set_can_focus(True)
            GLib.idle_add(self._to.grab_focus)

    # ------------------------------------------------------------------ fields

    def _recipient_entry(self, form, label_text: str,
                         value: str) -> Gtk.Entry:
        form.append(widgets.caption(label_text))
        entry = widgets.text_entry(label_text)
        entry.set_hexpand(True)
        entry.set_text(value or "")
        entry.connect("changed", lambda e: self._on_recipient(e))
        common_mod.tap_to_focus(entry)
        form.append(entry)
        return entry

    def _boxed_recipient(self, form, label_text: str,
                         value: str) -> tuple:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_hexpand(True)
        box.append(widgets.caption(label_text))
        entry = widgets.text_entry(label_text)
        entry.set_hexpand(True)
        entry.set_text(value or "")
        entry.connect("changed", lambda e: self._on_recipient(e))
        common_mod.tap_to_focus(entry)
        box.append(entry)
        form.append(box)
        return (box, entry)

    def _fields(self) -> dict:
        start, end = self._body.get_buffer().get_bounds()
        return {
            "to": self._to.get_text(),
            "cc": self._cc.get_text(),
            "bcc": self._bcc.get_text(),
            "subject": self._subject.get_text(),
            "body": self._body.get_buffer().get_text(start, end,
                                                     False),
        }

    def _on_recipient(self, entry) -> None:
        self._schedule_autosave()
        if self._suggest_timer is not None:
            GLib.source_remove(self._suggest_timer)
        self._suggest_for = entry
        fragment = _token(entry.get_text())
        if len(fragment) < 2:
            self._suggest.set_visible(False)
            self._suggest_timer = None
            return
        self._suggest_timer = GLib.timeout_add(
            SUGGEST_MS, self._run_suggest, fragment)

    def _run_suggest(self, fragment: str) -> bool:
        self._suggest_timer = None
        entry = self._suggest_for

        def _done(rows) -> None:
            if entry is not self._suggest_for:
                return
            if not rows:
                rows = contacts_mod.search_eds_suggestions(
                    fragment, SUGGEST_LIMIT)
            self._paint_suggestions(entry, rows or [])

        contacts_mod.search_suggestions_async(fragment, _done,
                                              SUGGEST_LIMIT)
        return False

    def _paint_suggestions(self, entry, rows: list) -> None:
        while True:
            row = self._suggest.get_row_at_index(0)
            if row is None:
                break
            self._suggest.remove(row)
        if not rows:
            self._suggest.set_visible(False)
            return
        for uid, display, primary in rows[:SUGGEST_LIMIT]:
            _ = uid
            label = f"{display} <{primary}>" if "@" in (
                primary or "") else display
            button = widgets.menu_row(label)
            button.connect("clicked",
                           lambda _b, e=entry, d=display,
                           p=primary: self._apply_suggest(e, d, p))
            self._suggest.append(button)
        self._suggest.set_visible(True)

    def _apply_suggest(self, entry, display: str,
                       primary: str) -> None:
        head = entry.get_text().replace(";", ",")
        stem, _, _ = head.rpartition(",")
        insert = f"{display} <{primary}>" if "@" in (
            primary or "") else display
        head = f"{stem + ',' if stem.strip() else ''} {insert}, "
        entry.set_text(head.lstrip())
        self._suggest.set_visible(False)

    # ------------------------------------------------------------------ drafts

    def _schedule_autosave(self) -> None:
        if self._busy:
            return
        if self._save_timer() is not None:
            return
        self._autosave_pending = GLib.timeout_add(
            AUTOSAVE_MS, self._run_autosave)

    def _save_timer(self):
        pending = getattr(self, "_autosave_pending", None)
        return pending

    def _run_autosave(self) -> bool:
        self._autosave_pending = None
        try:
            self._hooks.save_draft(self._fields())
        except Exception:
            pass
        return False

    # ------------------------------------------------------------- attachments

    def _paint_chips(self) -> None:
        while True:
            chip = self._chips.get_first_child()
            if chip is None:
                break
            self._chips.remove(chip)
        total = sum(item.get("size", 0) for item in self._staged)
        for item in list(self._staged):
            size = item.get("size", 0)
            if size >= 1024 * 1024:
                shown = f"{size / 1024 / 1024:.1f} MB"
            elif size >= 1024:
                shown = f"{size // 1024:d} KB"
            else:
                shown = f"{size:d} B"
            chip = widgets.outline_button(
                f"{item.get('name', 'file')} ({shown})")
            chip.connect("clicked",
                         lambda _b, p=item.get("path"):
                         self._remove_staged(p))
            chip.set_tooltip_text("Tap to remove")
            self._chips.append(chip)
        count = len(self._staged)
        if count and total:
            if total >= 1024 * 1024:
                shown = f"{total / 1024 / 1024:.1f} MB"
            else:
                shown = f"{total // 1024:d} KB"
            self._attach_note.set_label(
                f"{count:d} attached, {shown} of "
                f"{compose_mod.MAX_TOTAL_ATTACHMENT_BYTES // 1024 // 1024:d} MB")
        elif count:
            self._attach_note.set_label(f"{count:d} attached")
        else:
            self._attach_note.set_label("")

    def _pick_files(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Attach file")
        try:
            dialog.open_multiple(self.get_root(), None,
                                 self._on_files_chosen)
        except Exception:
            self._toast("Couldn't open the file picker", None)

    def _on_files_chosen(self, dialog, result) -> None:
        try:
            picked = dialog.open_multiple_finish(result)
        except Exception:
            self._toast("Couldn't read the picked files", None)
            return
        if picked is None:
            return
        for position in range(picked.get_n_items()):
            item = picked.get_item(position)
            path = item.get_path() if isinstance(item, Gio.File) \
                else None
            if not path:
                self._toast("Couldn't use that file", None)
                continue
            name = item.get_basename() or path.rsplit("/", 1)[-1]
            self._hooks.stage_upload(path, name, self._on_staged)

    def _on_staged(self, result) -> None:
        if isinstance(result, Exception):
            self._toast(str(result) or "Couldn't attach file",
                        None)
            self._error.set_label(str(result))
            return
        self._error.set_label("")
        self._staged.append(result)
        self._paint_chips()
        self._schedule_autosave()

    def _remove_staged(self, path) -> None:
        self._staged = [item for item in self._staged
                        if item.get("path") != path]
        try:
            self._hooks.remove_staged(path)
        except Exception:
            pass
        self._paint_chips()

    def _fetch_offered(self) -> None:
        self._offer_row.set_visible(False)
        self._offer_done = True

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast("Couldn't include the originals", None)
                return
            self._staged.extend(result or [])
            self._paint_chips()

        self._hooks.fetch_offered(_done)

    # ------------------------------------------------------------------ send

    def _open_schedule(self) -> None:
        dialog = Adw.Dialog()
        dialog.set_title("Schedule send")
        content = widgets.dialog_box("Schedule send")
        now = datetime.now().astimezone()
        for label_text, at_ms in compose_mod.schedule_presets(now):
            button = widgets.menu_row(label_text)
            button.connect("clicked",
                           lambda _b, at=at_ms: (
                               dialog.close(), self._send(at)))
            content.append(button)
        cancel = widgets.menu_row("Cancel")
        cancel.connect("clicked", lambda _b: dialog.close())
        content.append(cancel)
        dialog.set_child(content)
        dialog.present(self.get_root())

    def _send(self, scheduled_at) -> None:
        if self._busy:
            return
        self._busy = True
        self._spinner.set_visible(True)
        self._error.set_label("")
        fields = self._fields()
        staged = list(self._staged)

        def _done(result) -> None:
            self._busy = False
            self._spinner.set_visible(False)
            if isinstance(result, Exception):
                self._error.set_label(str(result))
                return
            entry_id, undo_seconds, scheduled = result
            if scheduled:
                self._toast("Send scheduled", None)
            else:
                self._toast(
                    compose_mod.describe_undo_window(undo_seconds),
                    lambda eid=entry_id: self._undo(eid))
            self._hooks.after_sent()

        self._hooks.send(fields, staged, scheduled_at, _done)

    def _undo(self, entry_id: int) -> None:
        def _done(result) -> None:
            if isinstance(result, Exception) or not result:
                self._toast("Send already left", None)
            else:
                self._toast("Send cancelled", None)

        self._hooks.undo_send(entry_id, _done)

    def _on_discard(self) -> None:
        try:
            self._hooks.discard()
        except Exception:
            self._toast("Couldn't discard the draft", None)


def compose_navigation_page(page: ComposePage) -> Adw.NavigationPage:
    """Wrap the editor for the NavigationView stack."""
    navigation = Adw.NavigationPage(title="Compose")
    navigation.set_child(page)
    return navigation
