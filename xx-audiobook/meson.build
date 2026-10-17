project(
  'xx-audiobook',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The shared design system must be installed first (meson setup build &&
# meson install -C build in common/); the app imports xx_ui at runtime and
# never vendors a copy.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package not found: install common/ first')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))
datadir = get_option('datadir')

app_id = 'io.piercingxx.XXAudiobook'
app_subdir = 'xx-audiobook'
app_datadir = join_paths(datadir, app_subdir)

# Python package: installed flat into the datadir like XX-WM does, so the
# launcher runs `python3 -m xx_audiobook`-style entry with sibling imports.
install_data(
  [
    'src/xx_audiobook/__init__.py',
    'src/xx_audiobook/__main__.py',
    'src/xx_audiobook/main.py',
    'src/xx_audiobook/paths.py',
    'src/xx_audiobook/prefs.py',
    'src/xx_audiobook/auth.py',
    'src/xx_audiobook/api_routes.py',
    'src/xx_audiobook/applog.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_audiobook'),
)

# Launcher -> $prefix/bin/xx-audiobook
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-audiobook.in',
  output: 'xx-audiobook',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry -> $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAudiobook.desktop.in',
  output: 'io.piercingxx.XXAudiobook.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Metainfo -> $prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXAudiobook.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# Symbolic icon -> $prefix/share/icons/hicolor/scalable/apps/
install_data(
  'data/icons/io.piercingxx.XXAudiobook.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus session activation: the .desktop is DBusActivatable, so the bus
# must know how to start the app. Without this file the first launch
# from the drawer fails to activate. Name= must equal the owned name.
# (M2 adds io.piercingxx.XXAudiobook.Player.service for the player daemon,
# which must own its own name, never this one.)
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAudiobook.service.in',
  output: 'io.piercingxx.XXAudiobook.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)
