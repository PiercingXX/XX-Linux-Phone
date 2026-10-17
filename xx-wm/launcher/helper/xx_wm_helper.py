#!@python@
"""Privileged helper for XX-WM features that need root.

D-Bus system service ``io.piercingxx.XXWM.Helper1`` (D-Bus activated,
exits after 60 s idle). The shell itself never runs as root; it talks
to this service through :mod:`helper_client`.

Only ``Gio``/``GLib`` (imported lazily so the pure logic below stays
importable without PyGObject) and the standard library are used: no
``shell=True`` anywhere, ``nft`` is always invoked with argv lists.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time
from pathlib import PurePosixPath

BUS_NAME = 'io.piercingxx.XXWM.Helper1'
OBJECT_PATH = '/io/piercingxx/XXWM/Helper1'
INTERFACE = BUS_NAME

ERROR_NOT_AUTHORIZED = BUS_NAME + '.Error.NotAuthorized'
ERROR_INVALID_ARGS = 'org.freedesktop.DBus.Error.InvalidArgs'
ERROR_FAILED = 'org.freedesktop.DBus.Error.Failed'

ACTION_CHARGE = BUS_NAME + '.set-charge-limit'
ACTION_NETWORK = BUS_NAME + '.app-network'
ACTION_FREEZE = BUS_NAME + '.freeze'
ACTION_CLEAN = BUS_NAME + '.clean-system'

POLKIT_BUS = 'org.freedesktop.PolicyKit1'
POLKIT_PATH = '/org/freedesktop/PolicyKit1/Authority'
POLKIT_IFACE = POLKIT_BUS + '.Authority'
DBUS_BUS = 'org.freedesktop.DBus'
DBUS_PATH = '/org/freedesktop/DBus'
DBUS_IFACE = DBUS_BUS

NFT_FAMILY = 'inet'
NFT_TABLE = 'xxwm'
NFT_CHAIN = 'output'

SYSFS_DEFAULT = '/sys'
CGROUP_ROOT_DEFAULT = '/sys/fs/cgroup'
MOUNTS_FILE_DEFAULT = '/proc/mounts'
STATE_DIR_DEFAULT = '/var/lib/xx-wm-helper'
STATE_FILE_NAME = 'state.json'

CHARGE_MIN = 50
CHARGE_MAX = 100
CHARGE_START_DELTA = 5

IDLE_EXIT_SECONDS = 60

INTROSPECT_XML = """\
<node>
  <interface name="io.piercingxx.XXWM.Helper1">
    <method name="GetCapabilities">
      <arg name="capabilities" type="a{sv}" direction="out"/>
    </method>
    <method name="SetChargeLimit">
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="SetAppNetwork">
      <arg name="cgroup_path" type="s" direction="in"/>
      <arg name="allowed" type="b" direction="in"/>
    </method>
    <method name="FreezeCgroup">
      <arg name="cgroup_path" type="s" direction="in"/>
      <arg name="frozen" type="b" direction="in"/>
    </method>
    <method name="CleanSystem">
      <arg name="categories" type="as" direction="in"/>
      <arg name="cleaned" type="as" direction="out"/>
    </method>
  </interface>
</node>
"""

# Cache cleanup the shell may run as root. Fixed argv lists only: no
# package is ever removed, no firewall or service state changes, so the
# polkit action stays allow_active=yes like the other entries here.
CLEAN_SYSTEM_ALLOWLIST = {
    'apk-cache': ['apk', 'cache', 'clean'],
    'paccache-keep': ['paccache', '-rk2'],
    'paccache-uninstalled': ['paccache', '-ruk0'],
    'apt-clean': ['apt-get', 'clean'],
    'journal-vacuum': ['journalctl', '--vacuum-time=2weeks',
                       '--vacuum-size=500M'],
}

APK_CACHE_DIR_DEFAULT = '/etc/apk/cache'
SYSTEMD_DIR_DEFAULT = '/run/systemd/system'


class HelperError(Exception):
    """A helper failure mapped to a D-Bus error name."""

    def __init__(self, dbus_name: str, message: str) -> None:
        super().__init__(message)
        self.dbus_name = dbus_name


def _denied(message: str) -> HelperError:
    return HelperError(ERROR_NOT_AUTHORIZED, message)


def _failed(message: str) -> HelperError:
    return HelperError(ERROR_FAILED, message)


def clamp_charge_limit(percent: int) -> int:
    """Clamp a requested charge limit into the safe 50..100 window."""
    try:
        value = int(percent)
    except (TypeError, ValueError):
        raise _failed('charge limit is not a number') from None
    return max(CHARGE_MIN, min(CHARGE_MAX, value))


def _default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=30,
    )


class HelperCore:
    """Pure helper logic: sysfs/nft/cgroup work with injectable roots.

    ``sysfs``/``cgroup_root``/``mounts_file``/``state_file``/``nft_bin``
    exist so tests can point the helper at tmp dirs and a fake ``nft``;
    in production they keep their defaults. ``run`` replaces
    :func:`subprocess.run` (same argv-list contract, no shell).
    """

    def __init__(
        self,
        *,
        sysfs: str = SYSFS_DEFAULT,
        cgroup_root: str = CGROUP_ROOT_DEFAULT,
        mounts_file: str = MOUNTS_FILE_DEFAULT,
        state_file: str = os.path.join(STATE_DIR_DEFAULT, STATE_FILE_NAME),
        nft_bin: str = 'nft',
        run=_default_run,
    ) -> None:
        self.sysfs = sysfs
        self.cgroup_root = cgroup_root
        self.mounts_file = mounts_file
        self.state_file = state_file
        self.nft_bin = nft_bin
        self._run = run

    # ---------------------------------------------------------- capabilities

    def battery_dirs(self) -> list[str]:
        """Every power_supply entry exposing a charge-end threshold."""
        base = os.path.join(self.sysfs, 'class', 'power_supply')
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []
        found = []
        for name in names:
            end = os.path.join(base, name, 'charge_control_end_threshold')
            if os.path.isfile(end):
                found.append(os.path.join(base, name))
        return found

    def nft_available(self) -> bool:
        return shutil.which(self.nft_bin) is not None

    def cgroup2_mounted(self) -> bool:
        """True when ``cgroup_root`` sits under a mounted cgroup2 hierarchy."""
        if not os.path.isdir(self.cgroup_root):
            return False
        try:
            with open(self.mounts_file, 'r', encoding='utf-8') as handle:
                for line in handle:
                    fields = line.split()
                    if len(fields) >= 3 and fields[2] == 'cgroup2':
                        mountpoint = fields[1]
                        if (self.cgroup_root == mountpoint
                                or self.cgroup_root.startswith(mountpoint + '/')):
                            return True
        except OSError:
            return False
        return False

    def get_capabilities(self) -> dict:
        """Capability probe for the shell (no authorization required)."""
        return {
            'charge_limit_supported': bool(self.battery_dirs()),
            'nftables': self.nft_available(),
            'cgroup2': self.cgroup2_mounted(),
            'freezer': os.path.isfile(
                os.path.join(self.cgroup_root, 'cgroup.freeze')),
        }

    # ------------------------------------------------------------ charge limit

    def _write_file(self, path: str, text: str) -> None:
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(text)
        except OSError as err:
            raise _failed('%s: %s' % (path, err.strerror or err)) from None

    def set_charge_limit(self, percent: int) -> int:
        """Clamp ``percent`` to 50..100, write every battery, persist."""
        limit = clamp_charge_limit(percent)
        batteries = self.battery_dirs()
        if not batteries:
            raise _failed('no battery with charge_control_end_threshold found')
        for battery in batteries:
            self._write_file(
                os.path.join(battery, 'charge_control_end_threshold'),
                '%d\n' % limit,
            )
            start = os.path.join(battery, 'charge_control_start_threshold')
            if os.path.isfile(start):
                self._write_file(start, '%d\n' % (limit - CHARGE_START_DELTA))
        state = self.load_state()
        state['charge_limit'] = limit
        self.save_state(state)
        return limit

    # -------------------------------------------------------- path validation

    def user_slice_prefix(self, uid: int) -> str:
        return '%s/user.slice/user-%d.slice' % (self.cgroup_root, uid)

    def validate_app_cgroup(self, path: str, uid: int) -> str:
        """Confine ``path`` to the caller's own user slice.

        Anything malformed (non-absolute, ``..``, redundant separators,
        trailing slash, NUL) or outside
        ``<cgroup>/user.slice/user-<uid>.slice/`` raises NotAuthorized —
        from the caller's perspective it is simply not permitted. Symlinks
        are resolved too so a link inside the slice cannot point out.
        """
        prefix = self.user_slice_prefix(int(uid))
        if not isinstance(path, str) or not path or '\x00' in path:
            raise _denied('invalid cgroup path')
        if not posixpath.isabs(path):
            raise _denied('cgroup path must be absolute: %r' % (path,))
        if posixpath.normpath(path) != path:
            raise _denied('cgroup path is not canonical: %r' % (path,))
        if path != prefix and not path.startswith(prefix + '/'):
            raise _denied('cgroup path outside your user slice: %r' % (path,))
        real = os.path.realpath(path)
        real_prefix = os.path.realpath(prefix)
        if real != real_prefix and not real.startswith(real_prefix + '/'):
            raise _denied('cgroup path escapes your user slice: %r' % (path,))
        return path

    # ------------------------------------------------------------------ nft

    def cgroup_nft_level(self, path: str) -> int:
        """Depth of ``path`` below the cgroup2 mount for ``socket cgroupv2
        level N`` matching: nft compares the ancestor at that depth, so a
        full-path match needs the path's own depth."""
        rel = posixpath.relpath(path, self.cgroup_root)
        if rel == '.':
            return 1
        return max(1, len(PurePosixPath(rel).parts))

    def cgroup_nft_match(self, path: str) -> str:
        """Match string for ``path``: relative to the cgroup2 mount (as the
        kernel reports it) and double-quoted inside the argv element so
        systemd-style escapes (``\\\\x2d``) survive nft's own parsing."""
        rel = posixpath.relpath(path, self.cgroup_root)
        return '"%s"' % rel

    def _nft(self, argv: list[str], *, allow_exists: bool = False) -> str:
        cmd = [self.nft_bin] + argv
        try:
            proc = self._run(cmd)
        except OSError as err:
            raise _failed('nft failed to start: %s' % err) from None
        stderr = getattr(proc, 'stderr', '') or ''
        if proc.returncode != 0:
            if allow_exists and ('already exists' in stderr
                                 or 'File exists' in stderr):
                return ''
            raise _failed('nft %s: %s' % (
                ' '.join(argv), stderr.strip() or 'exit %d' % proc.returncode))
        return getattr(proc, 'stdout', '') or ''

    def apply_blocked(self, blocked: list[str]) -> None:
        """Reconcile the ``inet xxwm`` table with ``blocked`` cgroup paths:
        ensure table + output chain, flush our chain, add one drop rule
        per path. Our chain is the only thing flushed — other tables are
        never touched."""
        self._nft(['add', 'table', NFT_FAMILY, NFT_TABLE], allow_exists=True)
        self._nft([
            'add', 'chain', NFT_FAMILY, NFT_TABLE, NFT_CHAIN,
            '{', 'type', 'filter', 'hook', 'output', 'priority', '0',
            ';', 'policy', 'accept', ';', '}',
        ], allow_exists=True)
        self._nft(['flush', 'chain', NFT_FAMILY, NFT_TABLE, NFT_CHAIN])
        for path in sorted(blocked):
            self._nft([
                'add', 'rule', NFT_FAMILY, NFT_TABLE, NFT_CHAIN,
                'socket', 'cgroupv2', 'level',
                str(self.cgroup_nft_level(path)),
                self.cgroup_nft_match(path), 'drop',
            ])

    def set_app_network(self, cgroup_path: str, allowed: bool, uid: int) -> bool:
        """Block (``allowed`` False) or unblock a caller-owned app cgroup,
        then persist the blocked set and re-apply the nft chain."""
        path = self.validate_app_cgroup(cgroup_path, uid)
        if not self.nft_available():
            raise _failed('nftables not available')
        state = self.load_state()
        blocked = [p for p in state.get('blocked', []) if isinstance(p, str)]
        if allowed and path in blocked:
            blocked.remove(path)
        elif not allowed and path not in blocked:
            blocked.append(path)
        state['blocked'] = sorted(blocked)
        self.apply_blocked(state['blocked'])
        self.save_state(state)
        return bool(allowed)

    # -------------------------------------------------------- clean system

    def clean_system(self, categories, *,
                     apk_cache_dir: str = APK_CACHE_DIR_DEFAULT,
                     systemd_dir: str = SYSTEMD_DIR_DEFAULT) -> dict:
        """Run fixed-allowlist cache cleanups, skipping unusable entries.

        ``categories`` must all name :data:`CLEAN_SYSTEM_ALLOWLIST`;
        anything else raises InvalidArgs, so callers can never smuggle
        in a free-form command. ``apk-cache`` runs only when the apk
        cache dir exists, ``journal-vacuum`` only on systemd, and any
        entry whose binary is missing is skipped. Returns
        ``{'cleaned': [...], 'skipped': [...]}``.
        """
        if not isinstance(categories, (list, tuple)):
            raise HelperError(ERROR_INVALID_ARGS,
                              'categories must be a string array')
        selected = []
        for category in categories:
            if not isinstance(category, str) \
                    or category not in CLEAN_SYSTEM_ALLOWLIST:
                raise HelperError(ERROR_INVALID_ARGS,
                                  'unknown clean category: %r' % (category,))
            if category not in selected:
                selected.append(category)
        cleaned: list[str] = []
        skipped: list[str] = []
        for category in selected:
            if category == 'apk-cache' \
                    and not os.path.isdir(apk_cache_dir):
                skipped.append(category)
                continue
            if category == 'journal-vacuum' \
                    and not os.path.isdir(systemd_dir):
                skipped.append(category)
                continue
            argv = CLEAN_SYSTEM_ALLOWLIST[category]
            if shutil.which(argv[0]) is None:
                skipped.append(category)
                continue
            try:
                proc = self._run(argv)
            except OSError as err:
                raise _failed('clean %s failed to start: %s'
                              % (category, err)) from None
            if getattr(proc, 'returncode', 1) == 127:
                skipped.append(category)
                continue
            if getattr(proc, 'returncode', 1) != 0:
                stderr = (getattr(proc, 'stderr', '') or '').strip()
                raise _failed('clean %s: %s'
                              % (category, stderr or 'exit %d'
                                 % getattr(proc, 'returncode', 1)))
            cleaned.append(category)
        return {'cleaned': cleaned, 'skipped': skipped}

    # ---------------------------------------------------------------- freeze

    def freeze_cgroup(self, cgroup_path: str, frozen: bool, uid: int) -> bool:
        """Write cgroup.freeze for a caller-owned cgroup (cgroup v2).

        Used on devices without systemd's FreezeUnit. Freeze state is
        inherently transient, so unlike the charge limit and the blocked
        set it is not persisted."""
        path = self.validate_app_cgroup(cgroup_path, uid)
        if not self.cgroup2_mounted():
            raise _failed('cgroup v2 not mounted')
        self._write_file(os.path.join(path, 'cgroup.freeze'),
                         '1\n' if frozen else '0\n')
        return bool(frozen)

    # ----------------------------------------------------------------- state

    def load_state(self) -> dict:
        """Saved ``{'charge_limit': int|None, 'blocked': [str]}``; missing
        or corrupt state degrades to defaults rather than failing boot."""
        try:
            with open(self.state_file, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return {'charge_limit': None, 'blocked': []}
        if not isinstance(raw, dict):
            return {'charge_limit': None, 'blocked': []}
        limit = raw.get('charge_limit')
        blocked = raw.get('blocked')
        return {
            'charge_limit': limit if isinstance(limit, int) else None,
            'blocked': [p for p in blocked if isinstance(p, str)]
            if isinstance(blocked, list) else [],
        }

    def save_state(self, state: dict) -> None:
        """Atomic persist (tmp file + rename) so a crash mid-write can
        never leave half a JSON document for the next boot."""
        directory = os.path.dirname(self.state_file) or '.'
        try:
            os.makedirs(directory, mode=0o755, exist_ok=True)
            tmp = os.path.join(directory, '.state.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_file)
        except OSError as err:
            raise _failed('cannot persist state: %s'
                          % (err.strerror or err)) from None

    def apply_saved(self) -> dict:
        """Boot re-apply: charge limit, then the nft blocked set. One leg
        failing must not skip the other, so errors are collected and
        reported together."""
        state = self.load_state()
        applied: dict = {'charge_limit': None, 'blocked': [], 'errors': []}
        limit = state.get('charge_limit')
        if isinstance(limit, int):
            try:
                applied['charge_limit'] = self.set_charge_limit(limit)
            except HelperError as err:
                applied['errors'].append(str(err))
        blocked = [p for p in state.get('blocked', []) if isinstance(p, str)]
        if blocked:
            if not self.nft_available():
                applied['errors'].append('nftables not available')
            else:
                try:
                    self.apply_blocked(blocked)
                    applied['blocked'] = sorted(blocked)
                except HelperError as err:
                    applied['errors'].append(str(err))
        return applied


# ------------------------------------------------------- authorization (Gio)

def _require_gio():
    """Gio/GLib are imported here, not at module top, so the logic above
    stays importable (and unit-testable) without PyGObject."""
    from gi.repository import Gio, GLib
    return Gio, GLib


def get_connection_uid(connection, sender, callback) -> None:
    """Async ``GetConnectionUnixUser``: ``callback(uid|None, error|None)``."""
    Gio, GLib = _require_gio()

    def _on_user(conn, result) -> None:
        try:
            uid = int(conn.call_finish(result).unpack()[0])
        except Exception as err:
            callback(None, str(err))
            return
        callback(uid, None)

    connection.call(
        DBUS_BUS, DBUS_PATH, DBUS_IFACE, 'GetConnectionUnixUser',
        GLib.Variant('(s)', (sender,)),
        None, Gio.DBusCallFlags.NONE, 5_000, None, _on_user,
    )


def check_polkit_authorization(connection, sender, action, callback) -> None:
    """Async polkit check with subject ``system-bus-name`` = sender.

    ``callback(authorized, uid, error)`` fires exactly once: ``uid`` is
    the sender's Unix uid for slice scoping, ``error`` a detail string.
    """
    Gio, GLib = _require_gio()

    def _on_uid(uid, uid_err) -> None:
        if uid_err is not None or uid is None:
            callback(False, None, uid_err or 'unknown sender')
            return

        def _on_checked(conn, result) -> None:
            try:
                # CheckAuthorization returns ONE out-arg, the struct
                # (bba{ss}) = (is_authorized, is_challenge, details), so
                # the reply unpacks to ((bool, bool, {...}),) and only
                # [0][0] is the verdict. bool() of the struct itself is
                # always True and would authorize every caller.
                authorized = bool(conn.call_finish(result).unpack()[0][0])
            except Exception as err:
                callback(False, uid, str(err))
                return
            callback(authorized, uid, None)

        # Params are one struct of four: the subject struct, the action id,
        # the details dict, the flags. Nested plain tuples convert; a
        # pre-built Variant in struct position does not on this PyGObject.
        params = GLib.Variant(
            '((sa{sv})sa{ss}u)',
            (('system-bus-name', {'name': GLib.Variant('s', sender)}),
             action, {}, 0),
        )
        try:
            connection.call(
                POLKIT_BUS, POLKIT_PATH, POLKIT_IFACE, 'CheckAuthorization',
                params,
                None, Gio.DBusCallFlags.NONE, 10_000, None, _on_checked,
            )
        except Exception as err:
            callback(False, uid, str(err))

    get_connection_uid(connection, sender, _on_uid)


_METHOD_ACTIONS = {
    'SetChargeLimit': ACTION_CHARGE,
    'SetAppNetwork': ACTION_NETWORK,
    'FreezeCgroup': ACTION_FREEZE,
    'CleanSystem': ACTION_CLEAN,
}


class HelperService:
    """D-Bus object: auth dispatch around a :class:`HelperCore`.

    ``authorize``/``get_uid`` are injectable for tests; in production
    they are :func:`check_polkit_authorization` (which also resolves the
    uid). ``now`` injects the clock for the idle timer.
    """

    def __init__(self, connection=None, core=None, authorize=None,
                 now=time.monotonic) -> None:
        self.connection = connection
        self.core = core if core is not None else HelperCore()
        self._authorize = authorize or check_polkit_authorization
        self._now = now
        self._last_activity = self._now()
        self._registration_id: int | None = None

    def touch(self) -> None:
        self._last_activity = self._now()

    def idle_expired(self) -> bool:
        """True once 60 s passed without a method call (pure; the GLib
        timer in :func:`main` quits the loop on it)."""
        return (self._now() - self._last_activity) >= IDLE_EXIT_SECONDS

    # ------------------------------------------------------------- dispatch

    def register(self) -> None:
        Gio, _GLib = _require_gio()
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECT_XML)
        self._registration_id = self.connection.register_object(
            OBJECT_PATH, node.interfaces[0],
            self._on_method_call, None, None,
        )

    def unregister(self) -> None:
        if self.connection is not None and self._registration_id is not None:
            self.connection.unregister_object(self._registration_id)
            self._registration_id = None

    def _on_method_call(self, conn, sender, obj_path, iface, method,
                        params, invocation) -> None:
        self.touch()
        if method == 'GetCapabilities':
            try:
                caps = self.core.get_capabilities()
            except Exception as err:
                invocation.return_dbus_error(ERROR_FAILED, str(err))
                return
            Gio, GLib = _require_gio()
            payload = {key: GLib.Variant('b', bool(value))
                       for key, value in caps.items()}
            invocation.return_value(GLib.Variant('(a{sv})', (payload,)))
            return
        action = _METHOD_ACTIONS.get(method)
        if action is None or conn is None:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
            return
        args = params.unpack() if params is not None else ()
        try:
            self._authorize(
                conn, sender, action,
                lambda ok, uid, err: self._on_authorized(
                    invocation, method, args, ok, uid, err),
            )
        except Exception as err:
            invocation.return_dbus_error(ERROR_FAILED, str(err))

    def _on_authorized(self, invocation, method, args, authorized, uid,
                       auth_error) -> None:
        if auth_error is not None or not authorized or uid is None:
            invocation.return_dbus_error(
                ERROR_NOT_AUTHORIZED,
                auth_error or 'not authorized for %s' % (method,))
            return
        try:
            if method == 'SetChargeLimit':
                (percent,) = args
                self.core.set_charge_limit(int(percent))
            elif method == 'SetAppNetwork':
                cgroup_path, allowed = args
                self.core.set_app_network(str(cgroup_path), bool(allowed),
                                          int(uid))
            elif method == 'FreezeCgroup':
                cgroup_path, frozen = args
                self.core.freeze_cgroup(str(cgroup_path), bool(frozen),
                                        int(uid))
            elif method == 'CleanSystem':
                (cats,) = args
                out = self.core.clean_system(
                    [str(c) for c in cats] if isinstance(cats, list) else cats)
                Gio, GLib = _require_gio()
                invocation.return_value(
                    GLib.Variant('(as)', (list(out.get('cleaned', [])),)))
                return
            else:
                invocation.return_dbus_error(
                    ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
                return
        except HelperError as err:
            invocation.return_dbus_error(err.dbus_name, str(err))
        except Exception as err:
            invocation.return_dbus_error(ERROR_FAILED, str(err))
            return
        Gio, GLib = _require_gio()
        invocation.return_value(GLib.Variant('()', ()))


# ------------------------------------------------------------------- entry

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Privileged D-Bus helper for XX-WM (runs as root).')
    parser.add_argument(
        '--apply-saved', action='store_true',
        help='re-apply the persisted charge limit and nft blocks, then exit '
             '(used by the boot service; takes no bus name)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.apply_saved:
        applied = HelperCore().apply_saved()
        for err in applied.get('errors', []):
            print('xx-wm-helper: apply-saved: %s' % err, file=sys.stderr)
        return 0 if not applied.get('errors') else 1

    Gio, GLib = _require_gio()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception as err:
        print('xx-wm-helper: no system bus: %s' % err, file=sys.stderr)
        return 1
    service = HelperService(bus)
    try:
        service.register()
    except Exception as err:
        print('xx-wm-helper: cannot export object: %s' % err, file=sys.stderr)
        return 1
    Gio.bus_own_name_on_connection(
        bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None,
    )
    loop = GLib.MainLoop()

    def _on_tick() -> bool:
        if service.idle_expired():
            loop.quit()
            return False
        return True

    GLib.timeout_add_seconds(15, _on_tick)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        service.unregister()
    return 0


if __name__ == '__main__':
    sys.exit(main())
