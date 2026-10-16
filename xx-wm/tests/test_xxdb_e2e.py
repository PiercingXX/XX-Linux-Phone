"""xxdb end-to-end loopback: real TLS daemon + client over 127.0.0.1.

Covers pair -> shell echo -> screenshot (stubbed) with mutual TLS, plus
the unpaired-refused path. Needs `openssl` for certificates.
"""
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
from xxdb_daemon import XxdbDaemon

needs_openssl = pytest.mark.skipif(shutil.which('openssl') is None,
                                   reason='needs openssl for certificates')

_FAKE_PNG = (b'\x89PNG\r\n\x1a\n' + b'stub-screenshot-bytes')


def _make_cert(directory: Path, name: str):
    key_path = directory / f'{name}.key.pem'
    cert_path = directory / f'{name}.cert.pem'
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
         '-keyout', str(key_path), '-out', str(cert_path),
         '-days', '2', '-nodes', '-subj', f'/CN={name}'],
        check=True, capture_output=True, timeout=120)
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding='utf-8'))
    return key_path, cert_path, cert_path.read_text(encoding='utf-8'), \
        hashlib.sha256(der).hexdigest(), der


class _Peer:
    def __init__(self, host, port, key_path=None, cert_path=None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        if cert_path is not None:
            context.load_cert_chain(str(cert_path), str(key_path))
        raw = socket.create_connection((host, port), timeout=10)
        self.sock = context.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(10)
        der = self.sock.getpeercert(binary_form=True)
        self.server_fp = hashlib.sha256(bytes(der)).hexdigest()
        self._file = self.sock.makefile('rwb')

    def request(self, payload: dict) -> dict:
        self._file.write((json.dumps(payload) + '\n').encode())
        self._file.flush()
        line = self._file.readline()
        assert line, 'phone closed the connection'
        return json.loads(line.decode())

    def close(self):
        try:
            self._file.close()
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def loopback_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(xxdb, 'interface_entries',
                        lambda: [('lo', False, ['127.0.0.1'], True)])
    home = tmp_path / 'home'
    home.mkdir()
    daemon = XxdbDaemon(
        cert_dir=tmp_path / 'phone-certs',
        trust_store=xxdb.TrustStore(path=tmp_path / 'trusted.json'),
        allow_loopback=True,
        screenshot_provider=lambda: _FAKE_PNG,
        shell_log_path=tmp_path / 'shell.log',
        ipc_forwarder=lambda verb: None,
        input_injector=lambda action, params: (True, 'ok'),
        sudo_check=lambda: False,
        shell_restarter=lambda: 999,
    )
    daemon._home = str(home)
    addrs, port = daemon.start()
    assert addrs == ['127.0.0.1']
    assert 37000 <= port <= 37999
    yield daemon, port, home
    daemon.stop()


@needs_openssl
def test_e2e_pair_shell_screenshot(tmp_path, loopback_daemon):
    daemon, port, _home = loopback_daemon
    _key, cert_path, host_pem, _fp, host_der = _make_cert(tmp_path, 'host')

    stranger = _Peer('127.0.0.1', port)
    try:
        response = stranger.request({'id': 1, 'cmd': 'shell',
                                     'argv': ['echo', 'nope']})
        assert response['ok'] is False
        assert response['error'].startswith('not-paired')
    finally:
        stranger.close()

    code, _expires = daemon.pairing.issue()
    mac = xxdb.pairing_mac(code, daemon.phone_fp, host_der)
    # The pairing connection carries no client cert (the phone has nothing
    # to verify it against yet); the HMAC binds the embedded host cert.
    first = _Peer('127.0.0.1', port)
    try:
        assert first.server_fp == daemon.phone_fp
        response = first.request({'id': 1, 'cmd': 'pair', 'code': code,
                                  'host_cert_pem': host_pem,
                                  'host_name': 'e2e-laptop', 'mac': mac})
        assert response == {'id': 1, 'ok': True,
                            'phone_fp': daemon.phone_fp,
                            'host_fp': response['host_fp']}
        assert daemon.trust.contains(response['host_fp'])
    finally:
        first.close()

    second = _Peer('127.0.0.1', port, tmp_path / 'host.key.pem', cert_path)
    try:
        response = second.request({'id': 2, 'cmd': 'hello'})
        assert response['ok'] is True
        response = second.request({'id': 3, 'cmd': 'shell',
                                   'argv': ['echo', 'hello-xxdb']})
        assert response['ok'] is True
        assert response['stdout'] == 'hello-xxdb\n'
        response = second.request({'id': 4, 'cmd': 'screenshot'})
        assert response['ok'] is True
        import base64
        assert base64.b64decode(response['data']) == _FAKE_PNG
    finally:
        second.close()


@needs_openssl
def test_e2e_pair_burn_and_logout(tmp_path, loopback_daemon):
    daemon, port, _home = loopback_daemon
    _key, cert_path, host_pem, _fp, host_der = _make_cert(tmp_path, 'host2')
    code, _expires = daemon.pairing.issue()
    peer = _Peer('127.0.0.1', port)
    try:
        for attempt in range(3):
            response = peer.request(
                {'id': attempt, 'cmd': 'pair', 'code': '000000',
                 'host_cert_pem': host_pem, 'host_name': 'x',
                 'mac': xxdb.pairing_mac('000000', daemon.phone_fp,
                                         host_der)})
            assert response['ok'] is False
        assert response['error'] == 'pairing code burned'
        response = peer.request(
            {'id': 99, 'cmd': 'pair', 'code': code,
             'host_cert_pem': host_pem, 'host_name': 'x',
             'mac': xxdb.pairing_mac(code, daemon.phone_fp, host_der)})
        assert response['ok'] is False
    finally:
        peer.close()
