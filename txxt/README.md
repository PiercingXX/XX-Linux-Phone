# TxxT (Linux)

Linux phone port of the PiercingXX Android SMS/MMS app `txxt`
(GrapheneOS-targeted Kotlin source in `ref/Txxt/` — read-only reference).
Built for XX-WM and any phoc/Phosh session: Python 3.12+, GTK4/libadwaita,
text-first. See [PORT_SPEC.md](PORT_SPEC.md) for the full port plan.

The UI runs on the shared `xx_ui` design system in `../common`
(themes, one stylesheet, widget builders, live theme switching).
Install `common` first, then this app; `meson setup` fails loudly when
the `xx_ui` package is missing.

## Run from the checkout

```sh
PYTHONPATH=src:../common python3 -m txxt
```

Needs a display (phoc/Phosh session) plus system `Gtk`, `Adw` GI
libraries. Headless tests need none of that:

```sh
python3 -m pytest -q tests
```

(`tests/conftest.py` puts `../common` on `sys.path`, so no extra setup
is needed for tests.)

## Install

```sh
meson setup build
meson install -C build
```

Installs the `txxt` package to the datadir, a `txxt` launcher to bindir,
the `io.piercingxx.TxxT` .desktop entry, metainfo, and the
`txxt-daemon.service` user unit. Requires the `xx_ui` package installed
by `common/meson.build`.
