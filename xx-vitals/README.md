# XX-Vitals (Linux)

Linux phone port of the PiercingXX Android app `xx-vitals` (source: `ref/xx-vitals/`).
A cleanroom Google Fit for GrapheneOS, self-hosted on the owner's Synology NAS —
now coming to postmarketOS / Mobian / FuriOS / PureOS phones running phoc
(XX-WM or Phosh).

Status: spec + scaffold. See [PORT_SPEC.md](PORT_SPEC.md) for the full port plan.

## Run from the checkout

```sh
PYTHONPATH=src:../common python3 -m xx_vitals
```

Requires Python 3.12+, GTK4 + libadwaita with PyGObject introspection
(`Gtk`, `Adw`, `Gio`, `GLib`). No pip dependencies. The shared `xx_ui`
design system lives in `../common` and must be importable: install
`common/` first (see below) or keep it on `PYTHONPATH` for source runs
and tests (`tests/conftest.py` and `tools/` add it automatically).

## Layout

| Path | What lives there |
|------|------------------|
| `PORT_SPEC.md` | Feature inventory, Linux mappings, XX-WM integration, milestone plan |
| `meson.build` / `data/` | Install: package to datadir, launchers to bindir, `.desktop`, metainfo, systemd user units |
| `src/xx_vitals/` | `main.py` (Adw.Application + four-tab shell), `theme.py`, `prefs.py`, `units.py`, `sync_wire.py`, `core/` view models, `sync/auth.py` + `sync/status.py` + `sync/worker.py`, `ui/` thin pages |
| `tests/` | pytest smoke tests, headless (no display) |

## Install

Install `common/` first — the app requires the installed `xx_ui` python
package and never vendors a copy (`meson setup` fails otherwise):

```sh
meson setup build && meson install -C build   # in common/
meson setup build && meson install -C build   # here
```

Seeded screenshots for every theme:

```sh
python3 tools/demo_seed.py /tmp/vhome
sh tools/snap_all.sh lane_shots
```

Background collection/sync are opt-in systemd user timers:

```sh
systemctl --user enable --now xx-vitals-collect.timer xx-vitals-sync.timer
```
