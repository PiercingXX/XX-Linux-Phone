"""Interface filtering with a fake sysfs tree and fake ioctl."""
import socket
import struct

from netif import allowed_bind_addrs, interface_names, is_cellular


def _fake_sysfs(root, *names, up=True):
    for name in names:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        flags = 0x1 | 0x1000 if up else 0x0
        (folder / "flags").write_text(hex(flags))


def _fake_ioctl(mapping):
    def call(fd, request, buf):
        import array

        name = bytes(buf[:16]).split(b"\0")[0].decode()
        addr = mapping.get(name)
        if addr is None:
            raise OSError("no addr")
        raw = socket.inet_aton(addr)
        if isinstance(buf, array.array):
            buf[20:24] = array.array("B", raw)
        else:
            buf[20:24] = raw
        return 0

    return call


def test_cellular_names_excluded():
    for name in ("wwan0", "rmnet_data0", "ccmni0", "pdp0", "WWAN1"):
        assert is_cellular(name)
    for name in ("wlan0", "eth0", "end0", "usb0", "lo"):
        assert not is_cellular(name)


def test_only_up_non_cellular_listed(tmp_path):
    _fake_sysfs(tmp_path, "wlan0", "wwan0", "eth9")
    (tmp_path / "eth9" / "flags").write_text("0x0")
    ioctl = _fake_ioctl({"wlan0": "192.168.1.5", "wwan0": "10.9.9.9"})
    assert allowed_bind_addrs(str(tmp_path), ioctl=ioctl) == ["192.168.1.5"]


def test_loopback_opt_in(tmp_path):
    _fake_sysfs(tmp_path, "lo")
    ioctl = _fake_ioctl({"lo": "127.0.0.1"})
    assert allowed_bind_addrs(str(tmp_path), ioctl=ioctl) == []
    assert allowed_bind_addrs(str(tmp_path), include_loopback=True,
                              ioctl=ioctl) == ["127.0.0.1"]


def test_missing_sysfs_is_empty():
    assert interface_names("/nonexistent-sysfs") == []
    assert allowed_bind_addrs("/nonexistent-sysfs") == []


def test_membership_request_packing():
    packed = struct.pack("4s4s", socket.inet_aton("224.0.0.167"),
                         socket.inet_aton("0.0.0.0"))
    assert len(packed) == 8
    assert socket.inet_ntoa(packed[:4]) == "224.0.0.167"
