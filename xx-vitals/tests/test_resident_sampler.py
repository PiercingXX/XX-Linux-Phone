"""Resident sampler: day trace, bump rejection, SIGTERM flush, inhibitor gate.

The burst-only model banked a 10 s sample as the day's steps; these
tests pin the replacement contract. A synthetic day (long still
periods plus three walks of 500/1,200/300 steps at varied cadence)
through the resident sampler on an injected clock must total within
±5% of 2,000, single table-bump spikes must count nothing, a SIGTERM
must persist everything counted so far, and the logind delay
inhibitor may be held only while moving with the screen-off setting
on — its fd closed the moment still returns.
"""

import math
import random
import threading

from xx_vitals import prefs
from xx_vitals.collect.inhibit import SleepInhibitor
from xx_vitals.collect.resident import (
    MODE_MOVING,
    MODE_STILL,
    ResidentSampler,
    measure_cpu,
)

HZ = 40.0


def _walk(secs, freq_hz, seed):
    rng = random.Random(seed)
    count = int(secs * HZ)
    return [
        (
            0.0, 0.0,
            9.81 + 1.2 * math.sin(2 * math.pi * freq_hz * index / HZ)
            + 0.05 * rng.gauss(0, 1),
        )
        for index in range(count)
    ]


def _still_sample(stamp, seed):
    rng = random.Random(seed)
    return (stamp, 0.0, 0.0, 9.81 + 0.02 * rng.gauss(0, 1))


def _still_dense(start, secs, seed):
    rng = random.Random(seed)
    count = int(secs * HZ)
    return [
        (start + index / HZ, 0.0, 0.0, 9.81 + 0.02 * rng.gauss(0, 1))
        for index in range(count)
    ]


def _feed(sampler, start, xyz_list, chunk_s=5.0):
    chunk = max(1, int(chunk_s * HZ))
    stamp = start
    for index in range(0, len(xyz_list), chunk):
        part = xyz_list[index: index + chunk]
        stamp = start + (index + len(part)) / HZ
        sampler.advance(stamp, [(start + (index + j) / HZ, x, y, z) for j, (x, y, z) in enumerate(part)])
    return stamp


class _Lease:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeInhibitor:
    def __init__(self) -> None:
        self.takes = 0
        self.releases = 0
        self.leases: list[_Lease] = []
        self._held = None

    @property
    def held(self) -> bool:
        return self._held is not None

    def acquire(self) -> bool:
        if self._held is not None:
            return True
        lease = _Lease()
        self.leases.append(lease)
        self._held = lease
        self.takes += 1
        return True

    def release(self) -> None:
        if self._held is None:
            return
        self._held.close()
        self._held = None
        self.releases += 1


def _day_trace(sampler):
    stamp = 0.0
    for index in range(30):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 1000 + index)])
    for steps, freq_hz, seed in ((500, 1.6, 11), (1200, 1.9, 22), (300, 2.2, 33)):
        stamp = _feed(sampler, stamp, _walk(steps / freq_hz, freq_hz, seed))
        gap = _still_dense(stamp, 90.0, seed)
        chunk = int(5.0 * HZ)
        for index in range(0, len(gap), chunk):
            part = gap[index: index + chunk]
            sampler.advance(part[-1][0], part)
            stamp = part[-1][0]
    return stamp


def test_synthetic_day_trace_totals_within_five_percent():
    committed: list[int] = []
    sampler = ResidentSampler(commit=committed.append)
    _day_trace(sampler)
    sampler.flush()
    assert sampler.mode == MODE_STILL
    assert abs(sampler.total_counted - 2000) / 2000 <= 0.05, sampler.total_counted
    assert sum(committed) == sampler.total_counted


def test_minute_flush_persists_without_sigterm():
    committed: list[int] = []
    sampler = ResidentSampler(commit=committed.append)
    stamp = 0.0
    for index in range(10):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 300 + index)])
    stamp = _feed(sampler, stamp, _walk(40.0, 1.9, 77))
    assert sampler.pending_steps > 0
    assert committed == []
    gap = _still_dense(stamp, 90.0, 78)
    chunk = int(5.0 * HZ)
    for index in range(0, len(gap), chunk):
        part = gap[index: index + chunk]
        sampler.advance(part[-1][0], part)
    assert committed and sum(committed) == sampler.total_counted
    assert sampler.pending_steps == 0


def test_still_poll_ignores_table_bumps():
    committed: list[int] = []
    fake = _FakeInhibitor()
    sampler = ResidentSampler(commit=committed.append, inhibitor=fake,
                              inhibit_allowed=lambda: True)
    stamp = 0.0
    for index in range(20):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 9000 + index)])
    stamp += 2.0
    sampler.advance(stamp, [(stamp, 0.0, 0.0, 12.81)])
    sampler.advance(stamp + 0.5, [(stamp + 0.5, 0.0, 0.0, 6.21)])
    for index in range(10):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 9100 + index)])
    assert sampler.mode == MODE_STILL
    assert sampler.total_counted == 0
    assert committed == []
    assert fake.takes == 0


def test_sigterm_persists_everything_counted():
    committed: list[int] = []
    sampler = ResidentSampler(commit=committed.append)
    stamp = 0.0
    for index in range(10):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 400 + index)])
    stamp = _feed(sampler, stamp, _walk(60.0, 1.9, 55))
    assert sampler.pending_steps > 0
    flushed = sampler.handle_sigterm()
    assert flushed > 0
    assert sampler.pending_steps == 0
    assert sum(committed) == sampler.total_counted


def test_inhibitor_taken_only_in_motion_with_setting_on():
    committed: list[int] = []
    fake = _FakeInhibitor()
    sampler = ResidentSampler(commit=committed.append, inhibitor=fake,
                              inhibit_allowed=lambda: True)
    stamp = 0.0
    for index in range(10):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 600 + index)])
    assert fake.takes == 0
    stamp = _feed(sampler, stamp, _walk(60.0, 1.9, 66))
    assert sampler.mode == MODE_MOVING
    assert fake.takes >= 1 and fake.held
    gap = _still_dense(stamp, 60.0, 67)
    chunk = int(5.0 * HZ)
    for index in range(0, len(gap), chunk):
        part = gap[index: index + chunk]
        sampler.advance(part[-1][0], part)
    assert sampler.mode == MODE_STILL
    assert not fake.held and fake.releases >= 1
    assert fake.leases and all(lease.closed for lease in fake.leases)


def test_inhibitor_never_taken_with_setting_off():
    committed: list[int] = []
    fake = _FakeInhibitor()
    sampler = ResidentSampler(commit=committed.append, inhibitor=fake,
                              inhibit_allowed=lambda: False)
    stamp = 0.0
    for index in range(10):
        stamp += 2.0
        sampler.advance(stamp, [_still_sample(stamp, 700 + index)])
    stamp = _feed(sampler, stamp, _walk(60.0, 1.9, 68))
    assert sampler.total_counted > 0
    assert fake.takes == 0 and not fake.held


def test_screen_off_setting_defaults_off_and_round_trips(tmp_path):
    assert prefs.AppConfig().count_while_screen_off is False
    path = tmp_path / "config.json"
    cfg = prefs.AppConfig(collect_enabled=True, count_while_screen_off=True)
    prefs.save(cfg, path)
    assert prefs.load(path).count_while_screen_off is True


def test_real_inhibitor_shape_never_touches_bus():
    held = SleepInhibitor(take=lambda: None)
    assert held.acquire() is False and not held.held
    held.release()
    lease = _Lease()
    held = SleepInhibitor(take=lambda: lease)
    assert held.acquire() is True and held.held
    held.release()
    assert not held.held and lease.closed


def test_cpu_measurement_reports_both_modes():
    numbers = measure_cpu()
    assert numbers["still_wakeups_per_s"] == 0.5
    assert numbers["moving_wakeups_per_s"] == HZ
    assert numbers["still_cpu_percent"] >= 0.0
    assert numbers["moving_cpu_percent"] >= 0.0
    assert numbers["moving_cpu_percent"] > numbers["still_cpu_percent"]


def test_resident_loop_counts_and_stops_cleanly(tmp_path):
    import time

    from xx_vitals.collect import service as collect_service
    from xx_vitals.store.db import Database

    rng = random.Random(2026)
    reads: list[tuple[float, float, float]] = [
        (0.0, 0.0, 9.81 + 0.02 * rng.gauss(0, 1)) for _ in range(40)
    ]
    for index in range(int(120.0 * HZ)):
        reads.append((
            0.0, 0.0,
            9.81 + 1.2 * math.sin(2 * math.pi * 1.9 * index / HZ)
            + 0.05 * rng.gauss(0, 1),
        ))
    reads.extend([(0.0, 0.0, 9.81 + 0.02 * rng.gauss(0, 1)) for _ in range(40)])
    pending = list(reads)
    clock = [0.0]

    def fake_clock() -> float:
        return clock[0]

    def fake_read():
        clock[0] += 1.0 / HZ
        if pending:
            return pending.pop(0)
        stop.set()
        return None

    db = Database(tmp_path / "vitals.db")
    db.init()
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=1.70)
    stop = threading.Event()
    sampler = collect_service.build_resident_sampler(db, cfg)
    thread = threading.Thread(
        target=collect_service.run_resident,
        kwargs={
            "db": db, "config": cfg, "read_sample": fake_read, "stop": stop,
            "sampler": sampler, "still_poll_s": 0.001, "moving_batch_s": 0.05,
            "sample_wait_s": 0, "clock": fake_clock,
        },
        daemon=True,
    )
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    row = db.get_daily("phone", collect_service._local_today())
    assert row is not None and row.steps and row.steps > 150
    deadline = time.time() + 5
    while db.pending_count() == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert db.pending_count() >= 1
