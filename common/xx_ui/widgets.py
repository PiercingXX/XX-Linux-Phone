"""Thin builders for the XX components: plain Gtk widgets with the
``xx-`` CSS classes applied. No subclasses, no framework. Uppercase is
applied in code (GTK CSS has no text-transform).

Width contract: the phone canvas is 360 px wide, so no helper may impose
a natural width above 320 px of content. Every label therefore carries a
``max-width-chars`` cap plus ellipsize (single line) or word-char wrap;
the caps only bound size negotiation. Labels allocated exactly their
natural width (button children, centered separator captions) ellipsize
only text longer than the cap: ellipsize misfires on fractional rounding
otherwise, swapping a whole fitting character for an ellipsis. Icons are
symbolic icon-theme images, colored by CSS, never Unicode glyphs.
"""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Pango

BAR_ICON_PX = 20
BUTTON_ICON_PX = 24

_STANDALONE_CHARS = 32
_ROW_CHARS = 12
_HEADER_TITLE_CHARS = 8
_BUTTON_CHARS = 8
_WIDE_BUTTON_CHARS = 16
_TAB_CHARS = 8
_CHIP_CHARS = 16
_META_CHARS = 12
_AVATAR_CHARS = 4
_KEY_DIGIT_CHARS = 4
_KEY_SUB_CHARS = 8
_ENTRY_CHARS = 20
_BIG_LABEL_CHARS = 12

_BIG_LABEL_CLASSES = frozenset(
    ('xx-display', 'xx-display-large', 'xx-hero'))


def _fit_single(line: Gtk.Label, chars: int) -> Gtk.Label:
    line.set_ellipsize(Pango.EllipsizeMode.END)
    line.set_max_width_chars(chars)
    return line


def _fit_wrap(line: Gtk.Label, chars: int = _STANDALONE_CHARS) -> Gtk.Label:
    line.set_wrap(True)
    line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    line.set_max_width_chars(chars)
    line.set_ellipsize(Pango.EllipsizeMode.NONE)
    return line


def _fit_exact(text_label: Gtk.Label, text: str, chars: int) -> Gtk.Label:
    # For labels allocated exactly their natural width (button children,
    # captions centered between flexible separators): ellipsize fires on
    # fractional rounding even when the text fits, swapping a whole
    # character for an ellipsis. Only short text can fit exactly, so
    # ellipsize only the text that cannot fit; anything else clips a
    # fraction of a pixel invisibly instead.
    text_label.set_max_width_chars(chars)
    if len(text) > chars:
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        text_label.set_ellipsize(Pango.EllipsizeMode.NONE)
    return text_label


def _fit_button_text(button: Gtk.Button,
                     chars: int = _BUTTON_CHARS) -> Gtk.Button:
    child = button.get_child()
    if isinstance(child, Gtk.Label):
        _fit_exact(child, child.get_text(), chars)
    return button


def label(text: str, cls: str, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.add_css_class(cls)
    widget.set_xalign(xalign)
    if cls in _BIG_LABEL_CLASSES:
        _fit_single(widget, _BIG_LABEL_CHARS)
    else:
        _fit_single(widget, _STANDALONE_CHARS)
    return widget


def caption(text: str) -> Gtk.Label:
    return label(text.upper(), 'xx-caption')


def icon_image(name: str, px: int = BAR_ICON_PX) -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(px)
    return image


def _icon_button(name: str, callback=None,
                 px: int = BAR_ICON_PX) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class('xx-btn')
    button.add_css_class('xx-btn-icon')
    button.set_child(icon_image(name, px))
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def page_header(title: str, caption_text: str | None = None,
                actions: list | None = None,
                overflow_cb=None,
                icon_name: str | None = None,
                icon_cb=None) -> Gtk.Box:
    """Big display title at the gutter, optional caption, trailing text
    actions (no chrome), an optional ``icon_name`` symbolic action and an
    optional overflow menu button (view-more-symbolic)."""
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.add_css_class('xx-page-body')
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.set_valign(Gtk.Align.CENTER)
    title_label = label(title, 'xx-display')
    _fit_single(title_label, _HEADER_TITLE_CHARS)
    title_label.set_hexpand(True)
    title_label.set_valign(Gtk.Align.CENTER)
    top.append(title_label)
    for action_label, callback in actions or []:
        button = Gtk.Button.new_with_label(action_label)
        button.add_css_class('xx-btn')
        button.add_css_class('xx-btn-text')
        button.set_valign(Gtk.Align.CENTER)
        _fit_button_text(button)
        if callback is not None:
            button.connect('clicked', lambda _b, cb=callback: cb())
        top.append(button)
    if icon_name is not None:
        trailing = _icon_button(icon_name, icon_cb)
        trailing.set_valign(Gtk.Align.CENTER)
        top.append(trailing)
    if overflow_cb is not None:
        more = _icon_button('view-more-symbolic', overflow_cb)
        more.set_valign(Gtk.Align.CENTER)
        top.append(more)
    root.append(top)
    if caption_text:
        sub = caption(caption_text)
        _fit_single(sub, _STANDALONE_CHARS)
        root.append(sub)
    return root


def compact_header(back_cb, title: str, subtitle: str = '',
                   icon_name: str = '', icon_cb=None) -> Gtk.Box:
    """Sub-page header: 56 px bar, back chevron, caption-style title,
    trailing symbolic icon. Everything vertically centered."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-page-body')
    root.add_css_class('xx-compact-header')
    back = _icon_button('go-previous-symbolic', back_cb)
    back.set_valign(Gtk.Align.CENTER)
    root.append(back)
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    title_label = caption(title)
    _fit_single(title_label, _ROW_CHARS)
    middle.append(title_label)
    if subtitle:
        sub = label(subtitle, 'xx-secondary')
        _fit_single(sub, _ROW_CHARS)
        middle.append(sub)
    root.append(middle)
    if icon_name:
        button = _icon_button(icon_name, icon_cb)
        button.set_valign(Gtk.Align.CENTER)
        root.append(button)
    return root


def avatar(initials: str, size: int = 40, dashed: bool = False) -> Gtk.Label:
    disc = Gtk.Label(label=initials)
    disc.add_css_class('xx-avatar')
    if dashed:
        disc.add_css_class('dashed')
    disc.set_size_request(size, size)
    disc.set_xalign(0.5)
    disc.set_valign(Gtk.Align.CENTER)
    _fit_exact(disc, initials, _AVATAR_CHARS)
    return disc


def list_row(primary: str, secondary: str = '', meta: str = '',
             leading=None, trailing=None, on_activate=None,
             last: bool = False, danger_sub: bool = False) -> Gtk.Button:
    """Activatable 64 px row with hairline (``last`` drops it)."""
    row = Gtk.Button()
    row.add_css_class('xx-row')
    if last:
        row.add_css_class('last')
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    box.set_valign(Gtk.Align.CENTER)
    if leading is not None:
        leading.set_valign(Gtk.Align.CENTER)
        box.append(leading)
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    primary_label = label(primary, 'xx-title')
    _fit_single(primary_label, _ROW_CHARS)
    middle.append(primary_label)
    if secondary:
        sub = label(secondary, 'xx-secondary')
        _fit_single(sub, _ROW_CHARS)
        if danger_sub:
            sub.add_css_class('xx-danger-text')
        middle.append(sub)
    box.append(middle)
    if meta:
        meta_label = label(meta, 'xx-time', xalign=1.0)
        _fit_single(meta_label, _META_CHARS)
        meta_label.set_valign(Gtk.Align.CENTER)
        box.append(meta_label)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        box.append(trailing)
    row.set_child(box)
    if on_activate is not None:
        row.connect('clicked', lambda _b: on_activate())
    return row


def chip_bar(items: list) -> Gtk.ScrolledWindow:
    """Scrollable row of filter chips. ``items``: (label, selected, cb)."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    for text, selected, callback in items:
        chip = Gtk.Button.new_with_label(text.upper())
        chip.add_css_class('xx-chip')
        if selected:
            chip.add_css_class('selected')
        _fit_button_text(chip, _CHIP_CHARS)
        if callback is not None:
            chip.connect('clicked', lambda _b, cb=callback: cb())
        box.append(chip)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scrolled.set_child(box)
    return scrolled


def segmented(items: list) -> Gtk.Box:
    """Joined caps chips. ``items``: (label, selected, cb)."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    root.add_css_class('xx-segmented')
    count = len(items)
    for index, (text, selected, callback) in enumerate(items):
        button = Gtk.Button.new_with_label(text.upper())
        if index == 0:
            button.add_css_class('seg-first')
        elif index == count - 1:
            button.add_css_class('seg-last')
        else:
            button.add_css_class('seg-mid')
        if selected:
            button.add_css_class('seg-selected')
        _fit_button_text(button, _CHIP_CHARS)
        if callback is not None:
            button.connect('clicked', lambda _b, cb=callback: cb())
        button.set_hexpand(True)
        root.append(button)
    return root


def bottom_tabs(items: list) -> Gtk.Box:
    """4 equal tabs, indicator line on top of the active one."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    root.add_css_class('xx-tabs')
    root.set_homogeneous(True)
    for _glyph, text, active, callback in items:
        tab = Gtk.Button.new_with_label(text.upper())
        tab.add_css_class('xx-tab')
        if active:
            tab.add_css_class('active')
        _fit_button_text(tab, _TAB_CHARS)
        if callback is not None:
            tab.connect('clicked', lambda _b, cb=callback: cb())
        root.append(tab)
    return root


def search_bar(placeholder: str = 'Search', boxed: bool = False,
               on_changed=None) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-searchbar-boxed' if boxed else 'xx-searchbar')
    if not boxed:
        glyph = icon_image('system-search-symbolic')
        glyph.set_valign(Gtk.Align.CENTER)
        root.append(glyph)
    entry = Gtk.Entry()
    entry.set_placeholder_text(placeholder)
    entry.set_max_width_chars(_ENTRY_CHARS)
    entry.set_hexpand(True)
    entry.set_valign(Gtk.Align.CENTER)
    if on_changed is not None:
        entry.connect('changed', lambda _e: on_changed(entry.get_text()))
    root.append(entry)
    return root


def _button(text: str, classes: list, callback=None,
            chars: int = _BUTTON_CHARS) -> Gtk.Button:
    button = Gtk.Button.new_with_label(text)
    for cls in classes:
        button.add_css_class(cls)
    _fit_button_text(button, chars)
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def _icon_text_button(icon_name: str, text: str, classes: list,
                      callback=None) -> Gtk.Button:
    button = Gtk.Button()
    for cls in classes:
        button.add_css_class(cls)
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    row.set_halign(Gtk.Align.CENTER)
    row.set_valign(Gtk.Align.CENTER)
    row.append(icon_image(icon_name, BUTTON_ICON_PX))
    text_label = Gtk.Label(label=text)
    _fit_exact(text_label, text, _BUTTON_CHARS)
    row.append(text_label)
    button.set_child(row)
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def outline_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-outline'], callback)


def primary_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-primary'], callback)


def danger_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-danger'], callback)


def text_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-text'], callback)


def caps_button(text: str, callback=None) -> Gtk.Button:
    return _button(text.upper(), ['xx-btn-caps'], callback,
                   _WIDE_BUTTON_CHARS)


def icon_button(icon_name: str, callback=None) -> Gtk.Button:
    return _icon_button(icon_name, callback)


def call_button(text: str = 'Call', callback=None) -> Gtk.Button:
    return _icon_text_button('call-start-symbolic', text,
                             ['xx-btn', 'xx-btn-primary'], callback)


def fab(callback=None,
        icon_name: str = 'list-add-symbolic') -> Gtk.Button:
    button = _icon_button(icon_name, callback, BUTTON_ICON_PX)
    button.add_css_class('xx-fab')
    button.set_size_request(56, 56)
    button.set_halign(Gtk.Align.END)
    button.set_valign(Gtk.Align.END)
    button.set_margin_end(20)
    return button


def field(caption_text: str, value: str, placeholder: bool = False) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.append(caption(caption_text))
    shown = label(value, 'xx-field-value')
    if placeholder:
        shown.add_css_class('placeholder')
    root.append(shown)
    return root


def section_label(text: str) -> Gtk.Label:
    widget = caption(text)
    widget.add_css_class('xx-section-label')
    return widget


def card() -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    root.add_css_class('xx-card')
    return root


def setting_row(title: str, subtitle: str = '', trailing=None,
                last: bool = False) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    root.add_css_class('xx-setting')
    if last:
        root.add_css_class('last')
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    title_label = label(title, 'xx-title')
    _fit_single(title_label, _ROW_CHARS)
    middle.append(title_label)
    if subtitle:
        sub = label(subtitle, 'xx-secondary')
        _fit_single(sub, _ROW_CHARS)
        middle.append(sub)
    root.append(middle)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        root.append(trailing)
    return root


def make_switch(active: bool = False, callback=None) -> Gtk.Switch:
    switch = Gtk.Switch()
    switch.add_css_class('xx-switch')
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)
    if callback is not None:
        switch.connect('notify::active',
                       lambda _s, _p: callback(switch.get_active()))
    return switch


def empty_state(text: str, action_label: str | None = None,
                callback=None) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    prompt = label(text, 'xx-empty', xalign=0.5)
    _fit_wrap(prompt)
    prompt.set_halign(Gtk.Align.CENTER)
    root.append(prompt)
    if action_label:
        action = outline_button(action_label, callback)
        action.set_halign(Gtk.Align.CENTER)
        root.append(action)
    return root


def keypad_key(digit: str, sub: str = '', callback=None) -> Gtk.Button:
    key = Gtk.Button()
    key.add_css_class('xx-key')
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.set_valign(Gtk.Align.CENTER)
    digit_label = label(digit, 'xx-key-digit', xalign=0.5)
    _fit_exact(digit_label, digit, _KEY_DIGIT_CHARS)
    box.append(digit_label)
    if sub:
        sub_label = label(sub.upper(), 'xx-key-sub', xalign=0.5)
        _fit_exact(sub_label, sub, _KEY_SUB_CHARS)
        box.append(sub_label)
    key.set_child(box)
    if callback is not None:
        key.connect('clicked', lambda _b: callback(digit))
    return key


def call_action(text: str, active: bool = False, callback=None) -> Gtk.Button:
    classes = ['xx-callaction']
    if active:
        classes.append('active')
    return _button(text.upper(), classes, callback)


def end_call_button(callback=None) -> Gtk.Button:
    return _icon_text_button('call-start-symbolic', 'End call',
                             ['xx-endcall'], callback)


def bubble(text: str, outgoing: bool = True) -> Gtk.Label:
    widget = label(text, 'xx-bubble-out' if outgoing else 'xx-bubble-in')
    _fit_wrap(widget)
    if outgoing:
        widget.set_halign(Gtk.Align.END)
    return widget


def day_separator(text: str) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-day-sep-box')
    root.set_halign(Gtk.Align.FILL)
    root.set_valign(Gtk.Align.CENTER)
    before = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
    before.set_hexpand(True)
    before.set_valign(Gtk.Align.CENTER)
    root.append(before)
    tag = caption(text)
    tag.set_xalign(0.5)
    tag.set_valign(Gtk.Align.CENTER)
    _fit_exact(tag, tag.get_text(), _STANDALONE_CHARS)
    root.append(tag)
    after = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
    after.set_hexpand(True)
    after.set_valign(Gtk.Align.CENTER)
    root.append(after)
    return root


def dialog_box(title: str) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    root.add_css_class('xx-dialog')
    title_label = label(title, 'xx-dialog-title')
    _fit_single(title_label, _ROW_CHARS)
    root.append(title_label)
    return root


def menu_row(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-menu-row'], callback, _WIDE_BUTTON_CHARS)


def text_entry(placeholder: str = '') -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.add_css_class('xx-entry')
    entry.set_placeholder_text(placeholder)
    entry.set_max_width_chars(_ENTRY_CHARS)
    return entry


def page(header, body, bottom=None, fab_widget=None) -> Gtk.Box:
    """Assemble a phone page: fixed header, scrollable body, fixed bottom
    bar. The body scrolls vertically (never horizontally); ``fab_widget``
    overlays the scroll area so it sits above ``bottom`` and never
    overlaps it."""
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.add_css_class('xx-page')
    root.append(header)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)
    body.set_hexpand(True)
    scrolled.set_child(body)
    if fab_widget is not None:
        fab_widget.set_margin_bottom(20)
        area = Gtk.Overlay()
        area.set_vexpand(True)
        area.set_child(scrolled)
        area.add_overlay(fab_widget)
        root.append(area)
    else:
        root.append(scrolled)
    if bottom is not None:
        root.append(bottom)
    return root
