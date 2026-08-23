Colemak layout for GNOME Shell's on-screen keyboard.

`us.json` is the squeekboard layout (`../squeekboard/us+colemak.yaml`)
adapted to the JSON schema of gnome-shell's `data/osk-layouts/us.json`
(verified against https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/main/data/osk-layouts/us.json).
Meson installs it to `$datadir/xx-wm/gnome-osk/`; `scripts/install.sh`
offers a guarded step (GDM hosts only) that backs up the stock greeter
layout to `/usr/share/gnome-shell/osk-layouts/us.json.xx-wm-backup`,
then overwrites `us.json` with this one. Restore with:
`sudo cp /usr/share/gnome-shell/osk-layouts/us.json.xx-wm-backup /usr/share/gnome-shell/osk-layouts/us.json`

Known limitation: only the standard layout is ported; the terminal/email/url
input-purpose variants are not expressible in GNOME's OSK format yet.
