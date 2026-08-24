project(
  'xx-wm',
  version: '0.1.1',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

dependency('gtk4-layer-shell-0', required: true)

prefix       = get_option('prefix')
bindir       = join_paths(prefix, get_option('bindir'))
libexecdir   = join_paths(prefix, get_option('libexecdir'))
datadir_abs  = join_paths(prefix, get_option('datadir'))

shell_subdir = 'xx-wm'
shell_datadir = join_paths(get_option('datadir'), shell_subdir)

# The shell is a flat set of modules, not an installable package: it uses
# plain imports (`from window import ...`) and finds sibling assets via
# Path(__file__).with_name(). So every .py must land in the SAME directory
# as style.css (shell_datadir) and run as `python3 <shell_datadir>/main.py`,
# which puts that directory on sys.path. Installing to site-packages breaks
# both the flat imports and the asset lookups.
install_data(
  [
    'src/main.py',
    'src/window.py',
    'src/lock_screen.py',
    'src/notification_shade.py',
    'src/app_switcher.py',
    'src/quick_actions.py',
    'src/gesture_config.py',
    'src/gesture_bindings.py',
    'src/system_status.py',
    'src/first_boot.py',
    'src/call_ui.py',
    'src/dialer.py',
    'src/sms.py',
    'src/ipc.py',
    'src/shell_log.py',
    'src/notif_daemon.py',
    'src/modem_monitor.py',
    'src/app_index.py',
    'src/config.py',
    'src/contacts.py',
    'src/als_brightness.py',
    'src/home_launcher.py',
    'src/default_layout.py',
    'src/backup.py',
    'src/update_checker.py',
    'src/display_manager.py',
    'src/power_menu.py',
    'src/back_gesture.py',
    'src/app_item_actions.py',
    'src/calendar_grid.py',
    'src/dnd.py',
    'src/focus_mode.py',
    'src/font_theme.py',
    'src/sound.py',
    'src/system_settings.py',
    'src/weather.py',
  ],
  install_dir: shell_datadir,
)

install_data(
  'src/style.css',
  install_dir: shell_datadir,
)

install_data(
  'data/phoc.ini',
  install_dir: shell_datadir,
)

# Per-device phoc.ini scale fragments (20.5). install.sh picks one and copies
# it over phoc.ini at install time; shipping them lets the fragments travel
# with the package even when the repo is not present on the device.
install_data(
  'data/phoc/fairphone-5.ini',
  'data/phoc/furiphone-flx1.ini',
  'data/phoc/librem-5.ini',
  'data/phoc/tablet.ini',
  install_dir: join_paths(shell_datadir, 'phoc'),
)

# Vendored Wayland protocol XML (core + wlr foreign-toplevel-management).
# Used by the shell's pywayland client to bind globals at runtime.
install_data(
  'data/wayland.xml',
  'data/wlr-foreign-toplevel-management-unstable-v1.xml',
  install_dir: shell_datadir,
)

# Vendored pywayland protocol classes (generated from the XMLs above by the
# pywayland scanner; upstream pywayland ships no wlr module). Installed as a
# package subdirectory of shell_datadir so `import wayland_proto` resolves
# with the shell dir on sys.path. See src/wayland_proto/README.md.
install_data(
  'src/wayland_proto/__init__.py',
  'src/wayland_proto/wayland.py',
  'src/wayland_proto/wlr_foreign_toplevel_management_unstable_v1.py',
  install_dir: join_paths(shell_datadir, 'wayland_proto'),
)

install_data(
  'data/sounds/ringtone.mp3',
  'data/sounds/notify.wav',
  'data/sounds/comm-on.wav',
  'data/sounds/alert.wav',
  install_dir: join_paths(shell_datadir, 'sounds'),
)

# PiercingXX Colemak layouts for squeekboard (vendored from
# furi-phone-colemak-keyboard). install.sh symlinks this tree to
# ~/.local/share/squeekboard/keyboards/ — squeekboard only reads the user
# path. Subdirs are input-purpose variants squeekboard picks natively.
install_data(
  'data/squeekboard/us+colemak.yaml',
  'data/squeekboard/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard'),
)
install_data(
  'data/squeekboard/terminal/us+colemak.yaml',
  'data/squeekboard/terminal/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'terminal'),
)
install_data(
  'data/squeekboard/email/us+colemak.yaml',
  'data/squeekboard/email/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'email'),
)
install_data(
  'data/squeekboard/url/us+colemak.yaml',
  'data/squeekboard/url/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'url'),
)

# PiercingXX Colemak layout adapted to GNOME Shell's OSK JSON format (24).
# GDM's greeter OSK picks layouts from /usr/share/gnome-shell/osk-layouts/
# by name; install.sh offers a guarded step to overwrite us.json there
# (with backup). Shipping it lets that step work without the repo present.
install_data(
  'data/gnome-osk/us.json',
  install_dir: join_paths(shell_datadir, 'gnome-osk'),
)

# Shell Python launcher (runs main.py) → $prefix/bin/xx-wm
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('shell_dir', join_paths(prefix, shell_datadir))
launcher_conf.set('bindir', bindir)

configure_file(
  input: 'data/xx-wm.in',
  output: 'xx-wm',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# IPC client (lisgd bindings, wake hooks) → $prefix/bin/xx-wm-ipc
configure_file(
  input: 'data/xx-wm-ipc.in',
  output: 'xx-wm-ipc',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry → $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXWM.desktop.in',
  output: 'io.piercingxx.XXWM.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

# Session launcher (phoc wrapper) → $prefix/libexec/xx-wm-session
# Wayland session entry → $prefix/share/wayland-sessions/xx-wm.desktop
# Display managers only scan *.desktop here — a .session suffix is ignored.
session_conf = configuration_data()
session_conf.set('bindir', bindir)
session_conf.set('libexecdir', libexecdir)
session_conf.set('datadir', datadir_abs)

configure_file(
  input: 'data/xx-wm-session.in',
  output: 'xx-wm-session',
  configuration: session_conf,
  install: true,
  install_dir: get_option('libexecdir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/xx-wm.desktop.in',
  output: 'xx-wm.desktop',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'wayland-sessions'),
)

# systemd user service
configure_file(
  input: 'data/systemd/xx-wm.service',
  output: 'xx-wm.service',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'systemd', 'user'),
)

# OpenRC init script (postmarketOS default images)
install_data(
  'data/openrc/xx-wm',
  install_dir: join_paths(get_option('sysconfdir'), 'init.d'),
  install_mode: 'rwxr-xr-x',
)

# logind drop-in: let the shell own the power key instead of instant-poweroff
install_data(
  'data/logind/10-xx-wm-power.conf',
  install_dir: join_paths(get_option('sysconfdir'), 'systemd', 'logind.conf.d'),
)
