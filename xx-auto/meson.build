project(
  'xx-auto',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

xx_ui_check = run_command(
  python, ['-c', 'import xx_ui'],
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package not found: install common/ first')
endif

prefix     = get_option('prefix')
bindir     = join_paths(prefix, get_option('bindir'))
datadir    = get_option('datadir')

app_id     = 'io.piercingxx.XXAuto'
app_subdir = 'xx-auto'
app_datadir = join_paths(datadir, app_subdir)

install_data(
  [
    'src/xx_auto/__init__.py',
    'src/xx_auto/__main__.py',
    'src/xx_auto/main.py',
    'src/xx_auto/demo.py',
    'src/xx_auto/session_pick.py',
    'src/xx_auto/elapsed.py',
    'src/xx_auto/favourite_pick.py',
    'src/xx_auto/car_decision.py',
    'src/xx_auto/maps_resolve.py',
    'src/xx_auto/maps_launch.py',
    'src/xx_auto/drive_tiles.py',
    'src/xx_auto/settings_store.py',
    'src/xx_auto/applog.py',
    'src/xx_auto/mpris.py',
    'src/xx_auto/contacts_client.py',
    'src/xx_auto/bluez.py',
    'src/xx_auto/car_watch.py',
    'src/xx_auto/backup.py',
    'src/xx_auto/licenses.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_auto'),
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-auto.in',
  output: 'xx-auto',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAuto.desktop.in',
  output: 'io.piercingxx.XXAuto.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.piercingxx.XXAuto.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

systemd_conf = configuration_data()
systemd_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-auto-watch.service.in',
  output: 'xx-auto-watch.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)
