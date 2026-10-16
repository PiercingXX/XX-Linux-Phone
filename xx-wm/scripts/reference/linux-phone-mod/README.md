# linux-phone-mod (vendored reference)

Snapshot of the installer from https://github.com/PiercingXX/linux-phone-mod,
vendored 2026-07-18. That repo is **deprecated** once Piercing WM ships its own
`scripts/install.sh` — these files are the porting source for Workstreams 9 and 17
in `todo.md`, not runnable parts of this project.

- `linux-phone-mod.sh` — whiptail main menu (cached sudo, network check, piercing-dots pull)
- `step-1.sh` — base deps, fonts, GPS enable (apt/PureOS era)
- `apps.sh` — app set: Waydroid, Neovim nightly, Yazi, UFW, Tailscale (static tgz)

Known desktop-era baggage to drop when porting: Homebrew (broken on musl),
gnome-tweaks/papirus/mscorefonts, hardcoded apt. The sound assets from that repo
already live renamed in `launcher/data/sounds/`.
