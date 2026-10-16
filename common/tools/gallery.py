#!/usr/bin/env python3
"""XX UI Gallery: every xx_ui component with realistic content.

Three pages selectable by XX_GALLERY_PAGE=1|2|3, each fitting a 360x720
phone screen (used by scripts/theme_sheet.sh via scripts/snap.py).
Each page is a fixed header, a scrolling body and fixed bottom tabs,
assembled with xx_ui.widgets.page().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gio, Gtk

import xx_ui
from xx_ui import widgets

PAGE = os.environ.get('XX_GALLERY_PAGE', '1')

TABS_INBOX = [('', 'Inbox', True, None),
              ('', 'Keypad', False, None),
              ('', 'People', False, None),
              ('', 'Rules', False, None)]

TABS_PEOPLE = [('', 'Inbox', False, None),
               ('', 'Keypad', False, None),
               ('', 'People', True, None),
               ('', 'Rules', False, None)]

TABS_KEYPAD = [('', 'Inbox', False, None),
               ('', 'Keypad', True, None),
               ('', 'People', False, None),
               ('', 'Rules', False, None)]


def _pad(box: Gtk.Box) -> Gtk.Box:
    box.add_css_class('xx-page-body')
    return box


def _contacts() -> list:
    return [
        ('Mara Voss', 'on the 42 now. five min out', '21:44', 'MV', False),
        ('+1 614-555-0142', 'Silenced · outside 09:00–19:00', '21:12', '', True),
        ('Mom', 'Missed · rang through DND', '20:31', 'M', False),
        ('Ridgeline Dental', 'Cleaning Tue 10:30. Reply C', '16:02', 'RD', False),
        ('Dez Okafor', 'Outgoing call · 4:12', '12:48', 'DO', False),
        ('Ada Lovelace', '+1 555-010-0142', '09:15', 'AL', False),
    ]


def page_list() -> Gtk.Widget:
    header = widgets.page_header('Inbox', '6 threads',
                                 icon_name='list-add-symbolic')
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    chips = _pad(Gtk.Box.new(Gtk.Orientation.VERTICAL, 0))
    chips.append(widgets.chip_bar([('All', True, None),
                                   ('Texts', False, None),
                                   ('Calls', False, None),
                                   ('Held 2', False, None)]))
    body.append(chips)
    rows = _pad(Gtk.Box.new(Gtk.Orientation.VERTICAL, 0))
    contacts = _contacts()
    for index, (name, sub, meta, initials, dashed) in enumerate(contacts):
        leading = widgets.avatar(initials or '', 40, dashed=dashed)
        row = widgets.list_row(name, sub, meta=meta, leading=leading,
                               last=index == len(contacts) - 1,
                               danger_sub='Missed' in sub)
        rows.append(row)
    body.append(rows)
    bottom = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    bottom.append(_pad(widgets.search_bar()))
    bottom.append(widgets.bottom_tabs(TABS_INBOX))
    return widgets.page(header, body, bottom, widgets.fab())


def page_detail() -> Gtk.Widget:
    header = widgets.compact_header(
        None, 'Mara Voss ★', 'Mobile · always rings · tap for card',
        'call-start-symbolic', None)
    body = _pad(Gtk.Box.new(Gtk.Orientation.VERTICAL, 6))
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.append(widgets.avatar('MV', 88))
    body.append(top)
    body.append(widgets.label('Mara Voss', 'xx-display-large'))
    actions = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    call = widgets.outline_button('Call')
    call.set_hexpand(True)
    message = widgets.outline_button('Message')
    message.set_hexpand(True)
    actions.append(call)
    actions.append(message)
    body.append(actions)
    fields = Gtk.Box.new(Gtk.Orientation.VERTICAL, 20)
    fields.append(widgets.field('Phone', '+1 (614) 555-0199'))
    fields.append(widgets.field('Email', 'mara@example.com'))
    fields.append(widgets.field('Note', 'Add a note', placeholder=True))
    body.append(fields)
    body.append(widgets.section_label('Ringing'))
    body.append(widgets.setting_row(
        '★ Starred — rings any time', '', widgets.make_switch(True)))
    body.append(widgets.setting_row(
        'Repeat caller rings', 'Second call within 15 min gets through',
        widgets.make_switch(True), last=True))
    expect = widgets.card()
    expect.append(widgets.label('Expecting a call', 'xx-title'))
    expect.append(widgets.label('Everyone rings for a while, unknown too',
                                'xx-secondary'))
    expect.append(widgets.segmented([('Enforce', True, None),
                                     ('Observe', False, None)]))
    body.append(expect)
    bottom = widgets.bottom_tabs(TABS_PEOPLE)
    return widgets.page(header, body, bottom)


def _keypad() -> Gtk.Grid:
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(8)
    grid.set_halign(Gtk.Align.CENTER)
    keys = [('1', 'VM'), ('2', 'ABC'), ('3', 'DEF'),
            ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
            ('7', 'PQRS'), ('8', 'TUV'), ('9', 'WXYZ'),
            ('*', ''), ('0', '+'), ('#', '')]
    for index, (digit, sub) in enumerate(keys):
        grid.attach(widgets.keypad_key(digit, sub), index % 3,
                    index // 3, 1, 1)
    return grid


def page_keypad() -> Gtk.Widget:
    header = widgets.page_header('Keypad', 'Offline · everything stays here')
    body = _pad(Gtk.Box.new(Gtk.Orientation.VERTICAL, 6))
    number = widgets.label('614 555 01', 'xx-hero', xalign=0.5)
    body.append(number)
    match = widgets.card()
    match_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    match_row.append(widgets.avatar('MV', 32))
    match_label = widgets.label('Mara Voss  614 555 0199', 'xx-body')
    match_label.set_hexpand(True)
    match_row.append(match_label)
    match.append(match_row)
    saves = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    saves.set_halign(Gtk.Align.CENTER)
    saves.append(widgets.caps_button('Save'))
    saves.append(widgets.caps_button('Text this number'))
    match.append(saves)
    body.append(match)
    body.append(_keypad())
    call_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    call_row.set_halign(Gtk.Align.CENTER)
    call_row.append(widgets.call_button('Call'))
    call_row.append(widgets.icon_button('edit-clear-symbolic'))
    body.append(call_row)
    body.append(widgets.day_separator('Today'))
    body.append(widgets.bubble('still on for tonight?', outgoing=False))
    body.append(widgets.bubble('yeah. 9 at the laundromat', outgoing=True))
    dialog = widgets.dialog_box('Delete thread?')
    dialog.append(widgets.label('This removes every message.', 'xx-body'))
    buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    cancel = widgets.outline_button('Cancel')
    cancel.set_hexpand(True)
    delete = widgets.danger_button('Delete')
    delete.set_hexpand(True)
    buttons.append(cancel)
    buttons.append(delete)
    dialog.append(buttons)
    body.append(dialog)
    bottom = widgets.bottom_tabs(TABS_KEYPAD)
    return widgets.page(header, body, bottom)


class GalleryApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.piercingxx.UIGallery',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        xx_ui.apply(self)
        window = Gtk.ApplicationWindow(application=self)
        window.set_title('XX UI Gallery')
        window.set_decorated(False)
        window.set_default_size(360, 720)
        if PAGE == '2':
            window.set_child(page_detail())
        elif PAGE == '3':
            window.set_child(page_keypad())
        else:
            window.set_child(page_list())
        window.present()


if __name__ == '__main__':
    GalleryApp().run([])
