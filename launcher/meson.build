project(
  'piercing-shell',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

dependency('gtk4-layer-shell-0', required: true)

prefix       = get_option('prefix')
bindir       = join_paths(prefix, get_option('bindir'))
libexecdir   = join_paths(prefix, get_option('libexecdir'))
datadir_abs  = join_paths(prefix, get_option('datadir'))

shell_subdir = 'piercing-shell'
shell_datadir = join_paths(get_option('datadir'), shell_subdir)

python.install_sources(
  [
    'src/main.py',
    'src/window.py',
    'src/lock_screen.py',
    'src/notification_shade.py',
    'src/app_switcher.py',
    'src/quick_actions.py',
    'src/gesture_config.py',
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
  ],
  subdir: shell_subdir,
)

install_data(
  'src/style.css',
  install_dir: shell_datadir,
)

install_data(
  'data/phoc.ini',
  install_dir: shell_datadir,
)

# Shell Python launcher (runs main.py) → $prefix/bin/piercing-shell
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('shell_dir', join_paths(prefix, shell_datadir))

configure_file(
  input: 'data/piercing-shell.in',
  output: 'piercing-shell',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry → $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.PiercingShell.desktop.in',
  output: 'io.piercingxx.PiercingShell.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

# Session launcher (phoc wrapper) → $prefix/libexec/piercing-session
# Wayland session .desktop → $prefix/share/wayland-sessions/piercingos.session
session_conf = configuration_data()
session_conf.set('bindir', bindir)
session_conf.set('libexecdir', libexecdir)
session_conf.set('datadir', datadir_abs)

configure_file(
  input: 'data/piercing-session.in',
  output: 'piercing-session',
  configuration: session_conf,
  install: true,
  install_dir: get_option('libexecdir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/piercingos.session.in',
  output: 'piercingos.session',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'wayland-sessions'),
)

# systemd user service
configure_file(
  input: 'data/systemd/piercing-shell.service',
  output: 'piercing-shell.service',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'systemd', 'user'),
)
