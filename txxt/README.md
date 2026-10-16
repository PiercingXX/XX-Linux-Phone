# TxxT (Linux)

Linux phone port of the PiercingXX Android SMS/MMS app `txxt`
(GrapheneOS-targeted Kotlin source in `ref/Txxt/` — read-only reference).
Built for XX-WM and any phoc/Phosh session: Python 3.12+, GTK4/libadwaita,
text-first. See [PORT_SPEC.md](PORT_SPEC.md) for the full port plan.

Status: scaffold lane — spec plus a runnable placeholder window that
applies the XX-WM theme. Feature lanes follow `PORT_SPEC.md` §6.

## Run from the checkout

```sh
PYTHONPATH=src python3 -m txxt
```

Needs a display (phoc/Phosh session) plus system `Gtk`, `Adw` GI
libraries. Headless tests need none of that:

```sh
python3 -m pytest -q tests
```

## Install

```sh
meson setup build
meson install -C build
```

Installs the `txxt` package to the datadir, a `txxt` launcher to bindir,
the `io.piercingxx.TxxT` .desktop entry, metainfo, and the
`txxt-daemon.service` user unit.
