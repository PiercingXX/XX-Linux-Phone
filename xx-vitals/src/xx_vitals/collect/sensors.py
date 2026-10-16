"""Sensor backends for the collector: hardware step counter first, burst next.

Source preference (PORT_SPEC.md section 3): a cumulative hardware step
counter (some Qualcomm/MediaTek parts expose an iio channel such as
`in_steps_input`) wins and flows through the Android rebase logic
verbatim; otherwise a short accelerometer burst from raw iio sysfs
(`in_accel_{x,y,z}_raw` times `in_accel_scale`) feeds the peak filter in
collect/burst. Every read is an injected callable in tests, so unit
tests never touch the system bus or sysfs outside a tmp dir.

iio-sensor-proxy (`net.hadess.SensorProxy` on the SYSTEM bus) is a
presence signal only. Its introspection offers accelerometer, light,
compass and proximity state (HasAccelerometer, AccelerometerOrientation,
LightLevel, ProximityNear, HasCompass plus Claim/Release methods) — NO
step counter and NO raw accelerometer vector — so bursts are never read
from it. A proxy that claims an accelerometer only tells us a burst from
sysfs is worth attempting.

D-Bus and sysfs are touched only inside functions, never at import, so
this module imports headless. Blocking D-Bus/sysfs calls happen only in
the one-shot collector process or a worker thread — never on the GTK
main loop.
"""

from __future__ import annotations

import time
from pathlib import Path

from xx_vitals.collect.sampler import StepReading

SENSOR_PROXY_BUS = "net.hadess.SensorProxy"
SENSOR_PROXY_PATH = "/net/hadess/SensorProxy"
SENSOR_PROXY_IFACE = "net.hadess.SensorProxy"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

BURST_SAMPLES = 500
BURST_INTERVAL_S = 0.02

STEP_COUNTER_BASENAMES = ("in_steps_raw", "in_step_counter_raw", "in_steps_input")
STEP_COUNTER_PREFIXES = ("in_steps", "in_step_counter", "in_pedometer_steps")


def _now_ms(wall_clock_s: float | None = None) -> int:
    return int((wall_clock_s if wall_clock_s is not None else time.time()) * 1000)


def _elapsed_ms(monotonic_s: float | None = None) -> int:
    return int((monotonic_s if monotonic_s is not None else time.monotonic()) * 1000)


def find_step_counter_node(sysfs_root: str | Path = "/sys/bus/iio/devices") -> Path | None:
    """First sysfs file that looks like a hardware step-counter channel."""
    try:
        devices = sorted(Path(sysfs_root).iterdir())
    except OSError:
        return None
    for device in devices:
        if not device.is_dir():
            continue
        for name in STEP_COUNTER_BASENAMES:
            candidate = device / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        try:
            entries = sorted(device.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(STEP_COUNTER_PREFIXES):
                try:
                    if entry.is_file():
                        return entry
                except OSError:
                    continue
    return None


def read_hardware_counter(
    sysfs_root: str | Path = "/sys/bus/iio/devices",
    monotonic_s: float | None = None,
    wall_clock_s: float | None = None,
) -> StepReading | None:
    """One cumulative hardware counter read, or None when absent/unreadable."""
    node = find_step_counter_node(sysfs_root)
    if node is None:
        return None
    try:
        raw = node.read_text(encoding="utf-8").strip().split()[0]
        counter = float(raw)
    except (OSError, ValueError, IndexError):
        return None
    return StepReading(
        counter_value=counter,
        elapsed_realtime_ms=_elapsed_ms(monotonic_s),
        wall_clock_ms=_now_ms(wall_clock_s),
    )


def find_accel_device(sysfs_root: str | Path = "/sys/bus/iio/devices") -> Path | None:
    """First iio device dir carrying raw x/y/z accelerometer channels."""
    try:
        devices = sorted(Path(sysfs_root).iterdir())
    except OSError:
        return None
    for device in devices:
        if not device.is_dir():
            continue
        if all((device / name).is_file() for name in ("in_accel_x_raw", "in_accel_y_raw", "in_accel_z_raw")):
            return device
    return None


def read_accel_sysfs_sample(sysfs_root: str | Path = "/sys/bus/iio/devices") -> tuple[float, float, float] | None:
    """One accelerometer sample from raw iio sysfs, or None when absent."""
    device = find_accel_device(sysfs_root)
    if device is None:
        return None
    try:
        scale_text = (device / "in_accel_scale").read_text(encoding="utf-8").strip()
        scale = float(scale_text.split()[0])
    except (OSError, ValueError, IndexError):
        scale = 1.0
    try:
        x = float((device / "in_accel_x_raw").read_text(encoding="utf-8").strip().split()[0])
        y = float((device / "in_accel_y_raw").read_text(encoding="utf-8").strip().split()[0])
        z = float((device / "in_accel_z_raw").read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return (x * scale, y * scale, z * scale)


def sensor_proxy_properties() -> dict | None:
    """GetAll net.hadess.SensorProxy properties, or None when unreachable.

    Blocking Gio call: only for the collector process or a worker thread.
    The reply packs out-args as a one-tuple, so unpack()[0] is the dict.
    """
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    try:
        conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = conn.call_sync(
            SENSOR_PROXY_BUS, SENSOR_PROXY_PATH, PROPERTIES_IFACE, "GetAll",
            Gio.Variant("(s)", (SENSOR_PROXY_IFACE,)), Gio.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 5000, None,
        )
    except Exception:
        return None
    try:
        props = reply.unpack()[0]
        return dict(props)
    except (AttributeError, IndexError, ValueError):
        return None


def sensor_proxy_has_accelerometer(props: dict | None = None) -> bool:
    """True when iio-sensor-proxy reports an accelerometer.

    Only the HasAccelerometer property counts: the proxy exports no raw
    accelerometer vector, so any other key is not an accelerometer.
    """
    if props is None:
        props = sensor_proxy_properties()
    if not props:
        return False
    claimed = props.get("HasAccelerometer")
    if claimed is None:
        return False
    try:
        return bool(claimed.unpack() if hasattr(claimed, "unpack") else claimed)
    except Exception:
        return False


def sysfs_sample_reader(sysfs_root: str | Path = "/sys/bus/iio/devices"):
    """Sample reader polling raw iio sysfs accelerometer channels.

    Each call returns one (x, y, z) tuple scaled to physical units, or
    None when the device is absent. Raw sysfs is the only burst source:
    iio-sensor-proxy exposes orientation, not acceleration vectors.
    Blocking: worker thread / collector only.
    """

    def read_sample() -> tuple[float, float, float] | None:
        try:
            return read_accel_sysfs_sample(sysfs_root)
        except Exception:
            return None

    return read_sample


def accelerometer_available(
    sysfs_root: str | Path = "/sys/bus/iio/devices",
    props: dict | None = None,
    probe_dbus: bool = True,
) -> bool:
    """True when either iio-sensor-proxy or raw iio sysfs offers accel."""
    if probe_dbus:
        try:
            if sensor_proxy_has_accelerometer(props):
                return True
        except Exception:
            pass
        if props is not None:
            return find_accel_device(sysfs_root) is not None
    return find_accel_device(sysfs_root) is not None


def probe_source(
    sysfs_root: str | Path = "/sys/bus/iio/devices",
    props: dict | None = None,
    probe_dbus: bool = True,
) -> str:
    """Ordered step-source probe: "hardware", "burst", or "none".

    The proxy is consulted first but only as a presence signal (it has
    no counter and no raw vector); a kernel step-counter channel wins
    wherever one exists, otherwise an accelerometer — via the proxy
    claim or raw sysfs — means a burst is worth attempting.
    """
    if find_step_counter_node(sysfs_root) is not None:
        return "hardware"
    if accelerometer_available(sysfs_root, props, probe_dbus):
        return "burst"
    return "none"


def read_burst_samples(
    read_sample,
    count: int = BURST_SAMPLES,
    interval_s: float = BURST_INTERVAL_S,
    sleep=None,
) -> list[tuple[float, float, float]]:
    """Poll one burst of accelerometer samples, skipping missed reads."""
    rest = sleep if sleep is not None else time.sleep
    out: list[tuple[float, float, float]] = []
    for _ in range(count):
        try:
            sample = read_sample()
        except Exception:
            sample = None
        if sample is not None:
            out.append((float(sample[0]), float(sample[1]), float(sample[2])))
        if interval_s > 0:
            rest(interval_s)
    return out
