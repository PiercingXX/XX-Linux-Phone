# XX-WM privileged helper

Some shell features need root: battery charge limits (sysfs), per-app
network blocking (nftables + cgroupv2), cgroup freezing on devices
without systemd's `FreezeUnit`, and system cache cleanup (package
caches, journal vacuum). The shell itself never runs as root —
those operations go through a small D-Bus system service,
`io.piercingxx.XXWM.Helper1`, and the shell talks to it via
`launcher/src/helper_client.py`.

## Layout

| File | Installed to | Purpose |
|---|---|---|
| `launcher/helper/xx_wm_helper.py` | `$libexecdir/xx-wm-helper` (mode 755) | daemon (Gio + stdlib only) |
| `launcher/data/dbus-1/system-services/io.piercingxx.XXWM.Helper1.service` | `$datadir/dbus-1/system-services/` | D-Bus activation, `User=root` |
| `launcher/data/dbus-1/system.d/io.piercingxx.XXWM.Helper1.conf` | `$datadir/dbus-1/system.d/` | bus policy: root owns, any caller may message the helper interfaces |
| `launcher/data/polkit-1/actions/io.piercingxx.XXWM.Helper.policy` | `$datadir/polkit-1/actions/` | four actions, `allow_active=yes`, others `no` |
| `launcher/data/systemd/xx-wm-helper-boot.service` | `$datadir/systemd/system/` | oneshot `--apply-saved` at boot |
| `launcher/data/openrc/xx-wm-helper-boot` | `$sysconfdir/init.d/` | OpenRC equivalent |
| `launcher/src/helper_client.py` | shell datadir | async shell-side client |

Interface `io.piercingxx.XXWM.Helper1` at
`/io/piercingxx/XXWM/Helper1`: `GetCapabilities() → a{sv}`,
`SetChargeLimit(u)`, `SetAppNetwork(s, b)`, `FreezeCgroup(s, b)`,
`CleanSystem(as) → as`.
The daemon exits after 60 s without a method call; the bus re-activates
it on demand.

## Authorization

Every mutating method resolves the caller's uid with
`org.freedesktop.DBus.GetConnectionUnixUser` and checks polkit
`CheckAuthorization` for subject `system-bus-name` = sender against its
action (`…set-charge-limit`, `…app-network`, `…freeze`,
`…clean-system`), all async.
Denial (or an unresolvable sender) returns
`io.piercingxx.XXWM.Helper1.Error.NotAuthorized`.
`GetCapabilities` is intentionally unauthenticated — it only reports
what the device supports so the shell can hide unsupported tiles.

`CheckAuthorization` is called with flags 0 (non-interactive): the
helper is reached from shell code paths that have no polkit agent to
answer a challenge, so an interactive prompt could never complete.
Active-session callers are still covered because every helper action
defaults to `allow_active=yes`.

## Confinement

- `SetChargeLimit` clamps to 50..100, writes every
  `charge_control_end_threshold` (plus `start = limit − 5` where the
  kernel exposes it), and persists to
  `/var/lib/xx-wm-helper/state.json` (atomic tmp + rename, mode 600).
- `SetAppNetwork`/`FreezeCgroup` accept only canonical absolute paths
  under `/sys/fs/cgroup/user.slice/user-<caller uid>.slice/` — `..`,
  redundant separators, trailing slashes, NUL bytes, other users'
  slices, and symlink escapes are all rejected as NotAuthorized.
- nftables is driven exclusively through argv lists (`nft add table
  inet xxwm`, our own `output` chain + one
  `socket cgroupv2 level N "rel/path" drop` rule per blocked cgroup,
  reconciled by flush + re-add). Only our chain is ever flushed; other
  tables are untouched. No `shell=True` anywhere in the daemon.
- The match level is the cgroup's depth below the cgroup2 mount, and
  the match path is mount-relative, double-quoted inside its argv
  element so systemd escapes survive nft's parsing.
- Freeze state is transient and never persisted; charge limit and the
  blocked set are re-applied at boot by `--apply-saved` (each leg
  reports errors independently so one failure cannot skip the other).
- `CleanSystem` accepts only its fixed allowlist (`apk-cache`,
  `paccache-keep`, `paccache-uninstalled`, `apt-clean`,
  `journal-vacuum`) as argv lists — never a package removal, never a
  free-form command. `apk-cache` runs only when `/etc/apk/cache`
  exists, `journal-vacuum` only on systemd; missing binaries are
  skipped, and the reply lists what actually ran. Non-destructive, so
  its action stays `allow_active=yes` with the non-interactive check.

## Shell client

`helper_client` exposes async `get_capabilities` (cached per session,
`refresh=True` / `invalidate_capabilities()` to re-probe),
`set_charge_limit`, `set_app_network`, `freeze_cgroup`, `clean_system`
— each calling `on_done(ok, result)` exactly once on the main loop. An
uninstalled or unactivatable helper reports `HELPER_NOT_INSTALLED`
(`'helper not installed'`) instead of raising.
