# XX-Contacts (Linux)

> The phone's contact store: contacts live in xx-contacts' own database.

Linux port of the PiercingXX Android contacts app. Contacts are stored in
xx-contacts' own SQLite database (lossless vCard) and served to the rest of
the phone over D-Bus (`io.piercingxx.Contacts1`): the XX-WM shell, the dialer,
TxxT and email look names up there. The `xx-contacts --service` process is the
only writer and is D-Bus activated. Evolution Data Server is supported only as
a one-time import source and an optional one-way publish target.
Full behavior spec: [PORT_SPEC.md](PORT_SPEC.md).

## Run from the checkout

```sh
PYTHONPATH=src:../common python3 -m xx_contacts
```

Needs GTK4 + libadwaita via PyGObject. No pip dependencies. The visual
design comes from the shared `xx_ui` package in `common/` (themes, one
stylesheet, widget builders): install `common` first, or put it on
`PYTHONPATH` when running from source.

## Install

```sh
meson setup ../common build-common
meson install -C build-common
meson setup build
meson install -C build
```

Installs the package to the datadir, a `xx-contacts` launcher to bindir,
and the `.desktop` / metainfo entries (`io.piercingxx.XXContacts`).
