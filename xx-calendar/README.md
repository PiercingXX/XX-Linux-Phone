# xx-calendar (Linux)

Linux phone port of the PiercingXX Android app `xx-calendar`. Built for
XX-WM and any phoc/Phosh session: Python + GTK4/libadwaita, text-first.

Status: spec + scaffold — see `PORT_SPEC.md`. Later lanes build features
from that spec.

## What it is

An owner of its own event store (SQLite): Schedule, Day, Week, and
Month views with quiet-by-default reminders. It reads the XX-WM shell
theme from `~/.config/xx-wm/config.json` when present and falls back to
libadwaita dark/light otherwise. There is no in-app theme picker. EDS is
import/publish only — see `PORT_SPEC.md` Revision 2.

This lane ships the scaffold only: an `Adw.Application` with one
placeholder page that applies the shell theme.

## Run from the checkout

```bash
PYTHONPATH=src python3 -m xx_calendar
```

Needs GTK4 + libadwaita with Python bindings (`gi`). No pip packages.

## Install

```bash
meson setup build
meson install -C build
```

This installs the Python package to the datadir (like XX-WM does), a
`xx-calendar` launcher in bindir, the `.desktop` file with app id
`io.piercingxx.XXCalendar`, and the metainfo file.

## Test

```bash
python3 -m pytest -q tests
ruff check src tests
```
