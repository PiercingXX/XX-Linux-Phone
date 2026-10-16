# xx-email (Linux)

Linux phone port of the PiercingXX Android app `xx-email` — a
telemetry-free Gmail client. Built for XX-WM and any phoc/Phosh session:
Python + GTK4/libadwaita, text-first.

Status: setup (milestone 4) + mailbox/thread/search (milestone 6)
+ compose/outbox (milestone 7) + background/notifications/snooze
(milestone 8) lanes — see [PORT_SPEC.md](PORT_SPEC.md). The app opens
a first-run setup screen (bring-your-own Google OAuth client ID,
browser sign-in, first sync) and then the mailbox NavigationView
stack (tabs, folders, thread with plain-text bodies, operator +
offline search) over the L1/L2 store + sync engine, with a systemd
user timer polling every 15 minutes (`xx-email --daemon-tick`),
grouped new-mail notifications, and durable snooze wakes.

No pip dependencies: stdlib + GI system libraries only.

## Run from the checkout

```sh
PYTHONPATH=src python3 -m xx_email
```

Needs GTK4, libadwaita, and PyGObject on the system (no display is needed
for the test gate below).

## Tests and lint (lane gate)

```sh
../xx-wm/.venv/bin/python -m pytest -q tests
../xx-wm/.venv/bin/ruff check src tests
```

Tests cover the headless-pure modules (`theme`, `config`); GTK code is
compile-checked but needs a phone/compositor to run.

## Install

```sh
meson setup build --prefix=/usr
meson install -C build
```

Enable the background poll (15-minute systemd user timer; longer
`sync_minutes` and the shell battery saver are enforced by the tick
itself, so no unit edit is needed):

```sh
systemctl --user daemon-reload
systemctl --user enable --now xx-email-sync.timer
```

This installs the package to the datadir (`.../share/xx-email/`), a
launcher to bindir (`xx-email`), the `.desktop` file (app id
`io.piercingxx.XXEmail`), and AppStream metadata.
