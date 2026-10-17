# XX-Audiobook (Linux)

Linux phone port of the PiercingXX Android app `xx-audiobook` (source:
`ref/xx-audiobook/xx-audiobook-app`). Self-hosted audiobooks on your own
server — now coming to postmarketOS / Mobian / FuriOS / PureOS phones running
phoc (XX-WM or Phosh).

Status: spec + scaffold. See [PORT_SPEC.md](PORT_SPEC.md) for the full port plan.

The FastAPI server (`ref/xx-audiobook/xx-audiobook-server`) is NOT ported: it
already runs on Linux and stays the backend. The client speaks its contract
(`openapi.json`) exactly; the contract test pins the route table to it.

## Run from the checkout

```sh
PYTHONPATH=src:../common python3 -m xx_audiobook
```

Requires Python 3.12+, GTK4 + libadwaita with PyGObject introspection
(`Gtk`, `Adw`, `Gio`, `GLib`), plus `Secret` (libsecret) and `Gst`
(GStreamer) at playback time (M2). No pip dependencies. The shared `xx_ui`
design system lives in `../common` and must be importable: install
`common/` first (see below) or keep it on `PYTHONPATH` for source runs
and tests (`tests/conftest.py` adds it automatically).

Debug base override (never stored, never backed up):

```sh
XX_AUDIOBOOK_API_BASE=http://127.0.0.1:8750/ PYTHONPATH=src:../common python3 -m xx_audiobook
```

## Layout

| Path | What lives there |
|------|------------------|
| `PORT_SPEC.md` | Feature inventory, Linux mappings, XX-WM integration, milestone plan |
| `meson.build` / `data/` | Install: package to datadir, launcher to bindir, `.desktop`, metainfo, icon, D-Bus activation |
| `src/xx_audiobook/` | `main.py` (Adw.Application + five-tab shell), `paths.py`, `prefs.py`, `auth.py`, `api_routes.py`, `applog.py` |
| `tests/` | pytest smoke tests, headless (no display, no network beyond the loopback fixture) |

## Install

Install `common/` first — the app requires the installed `xx_ui` python
package and never vendors a copy (`meson setup` fails otherwise):

```sh
meson setup build && meson install -C build   # in common/
meson setup build && meson install -C build   # here
```

Seeded screenshots for every theme (from M3, once real pages land):

```sh
python3 tools/demo_seed.py /tmp/vhome
sh tools/snap_all.sh lane_shots
```

Background playback and sync are opt-in systemd user units (M2/M4):

```sh
systemctl --user enable --now xx-audiobook-player.service xx-audiobook-sync.timer
```
