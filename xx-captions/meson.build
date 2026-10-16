project(
  'xx-captions',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))

python.install_sources(
  'src/xx_captions/__init__.py',
  'src/xx_captions/__main__.py',
  'src/xx_captions/audio.py',
  'src/xx_captions/capture.py',
  'src/xx_captions/client.py',
  'src/xx_captions/config.py',
  'src/xx_captions/dbus_api.py',
  'src/xx_captions/engine.py',
  'src/xx_captions/models.py',
  'src/xx_captions/overlay.py',
  'src/xx_captions/overlay_state.py',
  'src/xx_captions/resample.py',
  'src/xx_captions/service.py',
  'src/xx_captions/store.py',
  'src/xx_captions/theme.py',
  subdir: 'xx_captions',
)

python.install_sources(
  'src/xx_captions/engines/__init__.py',
  'src/xx_captions/engines/sherpa.py',
  'src/xx_captions/engines/vosk.py',
  subdir: 'xx_captions/engines',
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())

configure_file(
  input: 'data/xx-captions.in',
  output: 'xx-captions',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/io.piercingxx.Captions1.service.in',
  output: 'io.piercingxx.Captions1.service',
  configuration: launcher_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'services'),
)
