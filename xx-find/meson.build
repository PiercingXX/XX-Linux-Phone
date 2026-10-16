project(
  'xx-find',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# Install only. This file MUST NOT enable, start, preset, or create
# symlinks for any unit, and MUST NOT install autostart entries: the
# agent ships disabled (no [Install] section in its unit) and refuses
# to run until the owner enables and pairs it in Settings.

python.install_sources(
  'src/xx_find/__init__.py',
  'src/xx_find/__main__.py',
  'src/xx_find/actions.py',
  'src/xx_find/agent.py',
  'src/xx_find/config.py',
  'src/xx_find/crypto.py',
  'src/xx_find/decrypt.py',
  'src/xx_find/erase.py',
  'src/xx_find/keystore.py',
  'src/xx_find/location.py',
  'src/xx_find/pairing.py',
  'src/xx_find/providers.py',
  'src/xx_find/protocol.py',
  'src/xx_find/schedule.py',
  'src/xx_find/server.py',
  subdir: 'xx_find',
)

install_data(
  'src/xx-find-agent',
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data(
  'src/xx-find-server',
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data(
  'src/xx-find-decrypt',
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# owner.js ships inside the installed package next to server.py, which
# embeds it verbatim into the owner UI pages (see server._owner_js).
python.install_sources(
  'src/xx_find/owner.js',
  subdir: 'xx_find',
)

install_data(
  'data/systemd/xx-find-agent.service',
  install_dir: join_paths(get_option('prefix'), 'lib/systemd/user'),
)
