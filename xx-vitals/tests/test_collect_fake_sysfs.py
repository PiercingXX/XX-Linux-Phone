"""Collector over a fake sysfs tree: never the real devices.

The sampler entry points take their sysfs root explicitly, so these
tests point them at a tmp dir: a hardware counter file drives the
verbatim rebase path end to end, and rewritten accelerometer files feed
the resident sampler through the real scale-and-parse code. Nothing
here may name the host device tree (see the guard below).
"""

import math
import random
from pathlib import Path

from xx_vitals import prefs
from xx_vitals.collect import sensors
from xx_vitals.collect.resident import MOVING_HZ
from xx_vitals.collect.resident import ResidentSampler
from xx_vitals.collect.service import (
    OUTCOME_OK,
    SOURCE_HARDWARE,
    run_once,
)
from xx_vitals.store.db import Database

TODAY = "2026-08-27"
ELAPSED = 1_000_000
WALL = 1_000_000_000
SCALE = 0.001


def _db():
    db = Database(":memory:")
    db.init()
    return db


def _device(root, name="iio:device0"):
    device = root / name
    device.mkdir(parents=True)
    return device


def test_hardware_counter_file_drives_rebase_and_persistence(tmp_path):
    dev = _device(tmp_path)
    counter = dev / "in_steps_input"
    counter.write_text("8000\n", encoding="utf-8")
    db = _db()
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=1.70)

    first = sensors.read_hardware_counter(tmp_path, monotonic_s=1000.0, wall_clock_s=1000.0)
    result = run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=first.elapsed_realtime_ms,
        wall_ms=first.wall_clock_ms, hardware=first,
    )
    assert result.outcome == OUTCOME_OK and result.source == SOURCE_HARDWARE
    assert result.steps == 0 and db.count_dailies() == 0

    counter.write_text("8900\n", encoding="utf-8")
    second = sensors.read_hardware_counter(tmp_path, monotonic_s=1100.0, wall_clock_s=1100.0)
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=second.elapsed_realtime_ms, wall_ms=second.wall_clock_ms,
        hardware=second,
    )
    assert result.steps == 900
    row = db.get_daily("phone", TODAY)
    assert row is not None and row.steps == 900
    assert db.pending_count() == 1


def test_counter_reset_file_rebases_without_attributing(tmp_path):
    dev = _device(tmp_path)
    counter = dev / "in_steps_input"
    counter.write_text("8000\n", encoding="utf-8")
    db = _db()
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=1.70)
    first = sensors.read_hardware_counter(tmp_path, monotonic_s=1000.0, wall_clock_s=1000.0)
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=first.elapsed_realtime_ms,
        wall_ms=first.wall_clock_ms, hardware=first,
    )
    counter.write_text("900\n", encoding="utf-8")
    second = sensors.read_hardware_counter(tmp_path, monotonic_s=1100.0, wall_clock_s=1100.0)
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=second.elapsed_realtime_ms, wall_ms=second.wall_clock_ms,
        hardware=second,
    )
    assert result.steps == 0 and result.rebooted
    assert db.count_dailies() == 0
    assert cfg.step_baseline is not None and cfg.step_baseline.counter == 900.0


def _write_sample(dev, sample):
    x, y, z = sample
    (dev / "in_accel_x_raw").write_text(f"{int(x / SCALE)}\n", encoding="utf-8")
    (dev / "in_accel_y_raw").write_text(f"{int(y / SCALE)}\n", encoding="utf-8")
    (dev / "in_accel_z_raw").write_text(f"{int(z / SCALE)}\n", encoding="utf-8")


def _sequenced_reader(dev, samples):
    pending = list(samples)

    def read():
        _write_sample(dev, pending.pop(0) if pending else samples[-1])
        return sensors.read_accel_sysfs_sample(dev.parent)

    return read


def _walk(count=100, seed=3, freq_hz=2.0):
    rng = random.Random(seed)
    out = []
    for index in range(count):
        sway = 1.2 * math.sin(2 * math.pi * freq_hz * index / MOVING_HZ)
        out.append((0.0, 0.0, 9.81 + sway + 0.05 * rng.gauss(0, 1)))
    return out


def _still_xyz(seed):
    rng = random.Random(seed)
    return (0.0, 0.0, 9.81 + 0.02 * rng.gauss(0, 1))


def test_accel_files_drive_resident_sampler_end_to_end(tmp_path):
    dev = _device(tmp_path)
    (dev / "in_accel_scale").write_text(f"{SCALE}\n", encoding="utf-8")
    committed: list[int] = []
    sampler = ResidentSampler(commit=committed.append)

    stamp = 0.0
    reader = _sequenced_reader(dev, [_still_xyz(1000 + index) for index in range(30)])
    for _ in range(30):
        stamp += 2.0
        sample = reader()
        assert sample is not None
        sampler.advance(stamp, [(stamp, sample[0], sample[1], sample[2])])
    assert sampler.total_counted == 0

    walking = _walk(count=int(30.0 * MOVING_HZ), seed=3, freq_hz=1.9)
    reader = _sequenced_reader(dev, walking)
    chunk = int(5.0 * MOVING_HZ)
    for index in range(0, len(walking), chunk):
        part = walking[index: index + chunk]
        batch = []
        for offset in range(len(part)):
            sample = reader()
            assert sample is not None
            batch.append((stamp + (index + offset) / MOVING_HZ,
                          sample[0], sample[1], sample[2]))
        stamp = batch[-1][0]
        sampler.advance(stamp, batch)
    sampler.flush()
    assert 45 <= sampler.total_counted <= 66, sampler.total_counted
    assert sum(committed) == sampler.total_counted


def test_no_test_names_the_host_device_tree():
    marker = "bus" + chr(47) + "iio"
    offenders = [
        path.name for path in Path(__file__).parent.glob("test_*.py")
        if marker in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], offenders
