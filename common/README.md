# common/xx_ui — one design system for the shell and every app

`xx_ui` is the single source of truth for themes and the shared widget
language. Later lanes migrate each app and the shell onto it.

## Usage

```python
import xx_ui
from xx_ui import widgets

theme = xx_ui.apply(app)          # install CSS, force Adw scheme, watch config
xx_ui.on_change(lambda t: redraw(t))  # live theme switches

header = widgets.page_header('Contacts', '9 contacts',
                             actions=[('New', on_new)])
row = widgets.list_row('Ada Lovelace', '+1 555-010-0142', meta='21:44',
                       leading=widgets.avatar('AL', 40))
```

- `xx_ui.theme.load()` reads `~/.config/xx-wm/config.json` (respects
  `XDG_CONFIG_HOME`): `theme`, `custom_background`, `font`,
  `custom_font_family`, `text_size_scale`. Missing or invalid config
  falls back to amoled and never raises.
- `xx_ui.css.render(theme)` renders the `style.css` template.
- `xx_ui.widgets` holds one thin builder per component (plain Gtk
  widgets with `xx-` classes, no subclasses). Pure-CSS components need
  only the class name.
- Gallery: `XX_GALLERY_PAGE=1 python3 common/tools/gallery.py`
- Theme sheets: `scripts/theme_sheet.sh lane_shots/`

See [DESIGN.md](DESIGN.md) for the one-page design rules.
