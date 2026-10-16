"""Sensor backends over tmp sysfs: no bus, no real hardware in tests."""

from xx_vitals.collect import sensors
from xx_vitals.collect.sampler import StepReading


def _device(root, name="iio:device0"):
    device = root / name
    device.mkdir(parents=True)
    return device


def test_hardware_counter_reads_tmp_sysfs(tmp_path):
    assert sensors.read_hardware_counter(tmp_path) is None
    dev = _device(tmp_path)
    (dev / "in_accel_x_raw").write_text("0\n", encoding="utf-8")
    assert sensors.read_hardware_counter(tmp_path) is None
    (dev / "in_steps_raw").write_text("8390\n", encoding="utf-8")
    reading = sensors.read_hardware_counter(tmp_path, monotonic_s=12.0, wall_clock_s=1700.0)
    assert isinstance(reading, StepReading)
    assert reading.counter_value == 8390.0
    assert reading.elapsed_realtime_ms == 12_000
    assert reading.wall_clock_ms == 1_700_000


def test_hardware_counter_rejects_garbage(tmp_path):
    dev = _device(tmp_path)
    (dev / "in_step_counter_raw").write_text("not-a-number\n", encoding="utf-8")
    assert sensors.read_hardware_counter(tmp_path) is None


def test_accel_sysfs_sample_applies_scale(tmp_path):
    dev = _device(tmp_path)
    (dev / "in_accel_x_raw").write_text("1000\n", encoding="utf-8")
    (dev / "in_accel_y_raw").write_text("0\n", encoding="utf-8")
    (dev / "in_accel_z_raw").write_text("0\n", encoding="utf-8")
    (dev / "in_accel_scale").write_text("0.001\n", encoding="utf-8")
    assert sensors.read_accel_sysfs_sample(tmp_path) == (1.0, 0.0, 0.0)
    assert sensors.accelerometer_available(tmp_path, probe_dbus=False) is True
    assert sensors.accelerometer_available(tmp_path / "missing", probe_dbus=False) is False


def test_proxy_props_dict_never_touches_bus():
    assert sensors.sensor_proxy_has_accelerometer({}) is False
    assert sensors.sensor_proxy_has_accelerometer({"HasAccelerometer": True}) is True
    assert sensors.sensor_proxy_has_accelerometer({"HasAccelerometer": False}) is False
    assert sensors.sensor_proxy_has_accelerometer({"Accelerometer": (0.0, 0.0, 9.8)}) is False


def test_hardware_counter_prefers_in_steps_input(tmp_path):
    dev = _device(tmp_path)
    (dev / "in_steps_input").write_text("1234\n", encoding="utf-8")
    reading = sensors.read_hardware_counter(tmp_path, monotonic_s=5.0, wall_clock_s=900.0)
    assert reading is not None and reading.counter_value == 1234.0


def test_sysfs_reader_returns_scaled_sample_without_bus(tmp_path):
    dev = _device(tmp_path)
    (dev / "in_accel_x_raw").write_text("0\n", encoding="utf-8")
    (dev / "in_accel_y_raw").write_text("0\n", encoding="utf-8")
    (dev / "in_accel_z_raw").write_text("9810\n", encoding="utf-8")
    (dev / "in_accel_scale").write_text("0.001\n", encoding="utf-8")
    assert sensors.sysfs_sample_reader(tmp_path)() == (0.0, 0.0, 9.81)
    assert sensors.sysfs_sample_reader(tmp_path / "missing")() is None


def test_probe_source_prefers_hardware_then_burst_then_none(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sensors.probe_source(empty, props={}, probe_dbus=True) == "none"
    assert sensors.probe_source(empty, props={"HasAccelerometer": True}) == "burst"
    accel = tmp_path / "accel"
    dev = _device(accel)
    for name in ("in_accel_x_raw", "in_accel_y_raw", "in_accel_z_raw"):
        (dev / name).write_text("0\n", encoding="utf-8")
    assert sensors.probe_source(accel, props={}, probe_dbus=True) == "burst"
    (dev / "in_steps_input").write_text("42\n", encoding="utf-8")
    assert sensors.probe_source(accel, props={"HasAccelerometer": True}) == "hardware"


def test_burst_poll_skips_missed_reads_without_sleeping():
    calls = iter([(1.0, 0.0, 9.0), None, (0.0, 1.0, 9.0)])
    slept = []
    out = sensors.read_burst_samples(
        lambda: next(calls, None), count=3, interval_s=0.02,
        sleep=lambda seconds: slept.append(seconds),
    )
    assert out == [(1.0, 0.0, 9.0), (0.0, 1.0, 9.0)]
    assert slept == [0.02, 0.02, 0.02]
