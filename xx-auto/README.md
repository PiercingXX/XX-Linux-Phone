# xx-auto

Driving screen for a phone in a car mount. Linux phone port of the
PiercingXX Android xx-auto app (`ref/xx-auto`).

Shows the active MPRIS player (title, artist, position, play/pause/
prev/next plus the player's own action buttons when it offers the
`io.piercingxx.XXAuto.Actions1` extension), a quick-pick player sheet,
a Maps tile, and a Calls tile with starred contacts handed to
xx-dialer over `tel:`. Optional car watcher (`xx-auto --watch`,
systemd user unit) opens the screen on car Bluetooth connect.

Local-only: no network. Settings in `~/.config/xx-auto/settings.json`.

```sh
cd xx-auto && meson setup build --prefix=/usr && sudo meson install -C build
sh tools/snap_all.sh lane_shots
```

See [PORT_SPEC.md](PORT_SPEC.md) for the Android → Linux mapping.
License: GPL-3.0. See [LICENSE](../LICENSE).
