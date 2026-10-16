"""Collector: hardware-counter one-shot plus the resident sampler loop.

Source preference is the cumulative hardware step counter with the
Android rebase rules verbatim, sampled by a 15-minute timer tick —
sampling a cumulative counter every 15 min is exactly right, so the
timer+oneshot path stays ONLY for that case. Without a hardware
counter there is no burst extrapolation anymore (a 10 s burst every
15 min counted ~1% of the day as if it were the whole day): the
long-running resident service in collect/resident polls the
accelerometer cheaply while still and counts continuously while
moving, flushing every minute and on SIGTERM.

Honesty rules (same as StepSampler): a null read writes nothing and
leaves the baseline untouched; a cold start records the baseline and
attributes nothing; a reboot/suspend clock discontinuity rebases and
attributes nothing that tick. Missing height/weight stay null on the
row — never fabricated zeroes. No estimate is ever stored: the steps
column holds only observed counts.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

if __package__ in (None, ""):  # installed script run: find the app dir
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))
from xx_vitals.collect.resident import (
    MOVING_HZ,
    STILL_POLL_S,
    ResidentSampler,
)
from xx_vitals.collect.sampler import (
    StepReading,
    map_steps,
    rebase,
)
from xx_vitals.core.home import (
    BIT_ACTIVE_MIN,
    BIT_CALORIES,
    BIT_DISTANCE,
    BIT_EFFORT,
)

SOURCE_PHONE = "phone"
SOURCE_HARDWARE = "hardware"

OUTCOME_DISABLED = "disabled"
OUTCOME_NO_SOURCE = "no-source"
OUTCOME_OK = "ok"


@dataclass(frozen=True)
class CollectResult:
    outcome: str
    steps: int
    source: str
    date: str | None
    rebooted: bool


def _derive_for_total(total: int, height_metres: float, weight_kg: float | None):
    mapped = map_steps(total, [], height_metres * 100.0, weight_kg or 0.0)
    distance = mapped.distance_m if height_metres > 0 else None
    calories = (
        mapped.calories_kcal
        if height_metres > 0 and weight_kg is not None and weight_kg > 0
        else None
    )
    mask = BIT_ACTIVE_MIN | BIT_EFFORT
    if distance is not None:
        mask |= BIT_DISTANCE
    if calories is not None:
        mask |= BIT_CALORIES
    return distance, calories, mapped.active_min, mapped.effort_points, mask


def _baseline_of(config) -> StepReading | None:
    stored = getattr(config, "step_baseline", None)
    if stored is None:
        return None
    try:
        return StepReading(
            counter_value=float(stored.counter),
            elapsed_realtime_ms=int(stored.elapsed_ms),
            wall_clock_ms=int(stored.wall_ms),
        )
    except (ValueError, TypeError, AttributeError):
        return None


def _save_baseline(config, reading: StepReading, source: str, floor: float | None) -> None:
    from xx_vitals.prefs import StepBaseline

    config.step_baseline = StepBaseline(
        counter=float(reading.counter_value),
        elapsed_ms=int(reading.elapsed_realtime_ms),
        wall_ms=int(reading.wall_clock_ms),
        source=source,
    )
    if floor is not None and floor > 0:
        config.step_noise_floor = float(floor)


def _accumulate(db, config, date: str, delta: int, now_ms: int):
    from xx_vitals.store import entries
    from xx_vitals.store.db import DailyMetric

    existing = db.get_daily(SOURCE_PHONE, date)
    total = (existing.steps if existing is not None and existing.steps is not None else 0) + delta
    weight_row = db.latest_weight()
    weight_kg = weight_row.weight_kg if weight_row is not None else None
    distance, calories, active, effort, mask = _derive_for_total(
        total, config.height_metres, weight_kg
    )
    if existing is None:
        merged = DailyMetric(
            source_id=SOURCE_PHONE, date=date, steps=total,
            distance_m=distance, active_min=active, effort_points=effort,
            calories_kcal=calories, derived_mask=mask,
        )
    else:
        merged = DailyMetric(
            source_id=existing.source_id, date=existing.date, steps=total,
            distance_m=distance, active_min=active, effort_points=effort,
            calories_kcal=calories, hr_avg=existing.hr_avg, hr_min=existing.hr_min,
            hr_max=existing.hr_max, hr_sample_count=existing.hr_sample_count,
            weight_avg_kg=existing.weight_avg_kg, weight_min_kg=existing.weight_min_kg,
            weight_max_kg=existing.weight_max_kg, derived_mask=mask,
            batch_id=existing.batch_id,
        )
    db.upsert_daily(merged)
    entries.enqueue_daily(db, merged, now_ms)


def _restamp(db, config, date: str, now_ms: int) -> bool:
    from xx_vitals.store import entries

    existing = db.get_daily(SOURCE_PHONE, date)
    if existing is None or existing.steps is None:
        return False
    weight_row = db.latest_weight()
    weight_kg = weight_row.weight_kg if weight_row is not None else None
    distance, calories, active, effort, mask = _derive_for_total(
        existing.steps, config.height_metres, weight_kg
    )
    updated = type(existing)(
        **{**existing.__dict__, "distance_m": distance, "active_min": active,
           "effort_points": effort, "calories_kcal": calories, "derived_mask": mask}
    )
    db.upsert_daily(updated)
    entries.enqueue_daily(db, updated, now_ms)
    return True


def run_once(
    db,
    config,
    *,
    now_ms: int,
    today: str,
    elapsed_ms: int,
    wall_ms: int,
    hardware: StepReading | None,
    on_save: Callable | None = None,
) -> CollectResult:
    """One hardware-counter pass. Pure orchestration for the timer tick.

    Without a hardware counter there is nothing to sample here — the
    resident sampler owns the accelerometer — so a null reading writes
    nothing and leaves the baseline untouched.
    """
    if not getattr(config, "collect_enabled", False):
        return CollectResult(outcome=OUTCOME_DISABLED, steps=0, source="", date=None, rebooted=False)

    last = _baseline_of(config)
    baseline_source = getattr(getattr(config, "step_baseline", None), "source", "") or ""

    if hardware is None:
        return CollectResult(
            outcome=OUTCOME_NO_SOURCE, steps=0, source="", date=None, rebooted=False
        )

    if baseline_source and baseline_source not in ("", SOURCE_HARDWARE):
        _save_baseline(config, hardware, SOURCE_HARDWARE, None)
        if on_save is not None:
            on_save(config)
        return CollectResult(
            outcome=OUTCOME_OK, steps=0, source=SOURCE_HARDWARE,
            date=today, rebooted=True,
        )
    delta = rebase(last, hardware)
    _save_baseline(config, hardware, SOURCE_HARDWARE, None)
    if on_save is not None:
        on_save(config)
    if delta.steps > 0:
        _accumulate(db, config, today, delta.steps, now_ms)
    else:
        _restamp(db, config, today, now_ms)
    return CollectResult(
        outcome=OUTCOME_OK, steps=delta.steps, source=SOURCE_HARDWARE,
        date=today, rebooted=delta.rebooted,
    )


def build_resident_sampler(db, config, *, inhibitor=None, save_config=None) -> ResidentSampler:
    """Wire a ResidentSampler to the database, config and sleep inhibitor."""
    from xx_vitals.collect.inhibit import SleepInhibitor

    held_inhibitor = inhibitor if inhibitor is not None else SleepInhibitor()

    def commit(steps: int) -> None:
        now_ms = int(time.time() * 1000)
        _accumulate(db, config, _local_today(), steps, now_ms)

    def on_save() -> None:
        if save_config is not None:
            save_config(config)

    return ResidentSampler(
        noise_floor=getattr(config, "step_noise_floor", None),
        commit=commit,
        on_save=on_save,
        inhibitor=held_inhibitor,
        inhibit_allowed=lambda: bool(getattr(config, "count_while_screen_off", False)),
    )


def run_resident(
    db,
    config,
    *,
    read_sample,
    stop: threading.Event,
    sampler: ResidentSampler | None = None,
    save_config=None,
    still_poll_s: float = STILL_POLL_S,
    moving_batch_s: float = 1.0,
    sample_wait_s: float = 1.0 / MOVING_HZ,
    clock=None,
) -> ResidentSampler:
    """Drive the resident sampler until `stop` is set. No sleeping inside."""

    active = sampler if sampler is not None else build_resident_sampler(
        db, config, save_config=save_config
    )
    now = clock if clock is not None else time.monotonic
    while not stop.is_set():
        if active.mode == "still":
            stop.wait(still_poll_s)
            if stop.is_set():
                break
            try:
                sample = read_sample()
            except Exception:
                sample = None
            if sample is None:
                continue
            stamp = now()
            active.advance(stamp, [(stamp, sample[0], sample[1], sample[2])])
        else:
            batch: list[tuple] = []
            deadline = now() + moving_batch_s
            while len(batch) < int(MOVING_HZ * moving_batch_s) and now() < deadline:
                stop.wait(sample_wait_s)
                if stop.is_set():
                    break
                try:
                    sample = read_sample()
                except Exception:
                    sample = None
                if sample is not None:
                    batch.append((now(), sample[0], sample[1], sample[2]))
            if batch:
                active.advance(batch[-1][0], batch)
    active.flush()
    if save_config is not None and active.floor is not None:
        config.step_noise_floor = float(active.floor)
        try:
            save_config(config)
        except OSError:
            pass
    return active


def run_one_shot(db, config, *, on_save=None) -> CollectResult:
    """Single hardware-counter pass for the 15-minute timer tick."""
    from xx_vitals.collect import sensors as sensor_backend

    now_ms = int(time.time() * 1000)
    elapsed_ms = int(time.monotonic() * 1000)
    hardware = sensor_backend.read_hardware_counter()
    return run_once(
        db, config, now_ms=now_ms, today=_local_today(),
        elapsed_ms=elapsed_ms, wall_ms=now_ms, hardware=hardware,
        on_save=on_save,
    )


def _local_today() -> str:
    return datetime.now().astimezone().date().isoformat()


def main(argv: list[str] | None = None) -> int:
    """xx-vitals-collect entry point: dispatch on the probed step source."""
    from xx_vitals.collect import sensors as sensor_backend
    from xx_vitals.paths import ensure_app_dirs
    from xx_vitals.prefs import load as load_config
    from xx_vitals.prefs import save as save_config

    args = list(argv) if argv is not None else []
    ensure_app_dirs()
    config = load_config()
    if not config.collect_enabled:
        return 0
    from xx_vitals.core import applog
    from xx_vitals.store.db import Database

    log = applog.get_logger()
    db = Database()
    db.init()
    if "--one-shot" in args:
        result = run_one_shot(db, config, on_save=lambda cfg: save_config(cfg))
        log.info("one-shot %s steps=%d source=%s date=%s",
                 result.outcome, result.steps, result.source, result.date)
        if result.outcome == OUTCOME_OK and result.steps:
            print(f"xx-vitals-collect: +{result.steps} steps ({result.source}) for {result.date}")
        return 0
    if sensor_backend.find_step_counter_node() is not None:
        result = run_one_shot(db, config, on_save=lambda cfg: save_config(cfg))
        log.info("one-shot %s steps=%d source=%s date=%s",
                 result.outcome, result.steps, result.source, result.date)
        if result.outcome == OUTCOME_OK and result.steps:
            print(f"xx-vitals-collect: +{result.steps} steps ({result.source}) for {result.date}")
        return 0
    if not sensor_backend.accelerometer_available():
        return 0
    sampler = build_resident_sampler(db, config, save_config=lambda cfg: save_config(cfg))
    stop = threading.Event()

    def _handle_term(_signum, _frame) -> None:
        sampler.handle_sigterm()
        try:
            save_config(config)
        except OSError:
            pass
        log.info("resident stop")
        stop.set()

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)
    reader = sensor_backend.sysfs_sample_reader()
    log.info("resident start")
    run_resident(db, config, read_sample=reader, stop=stop, sampler=sampler,
                 save_config=lambda cfg: save_config(cfg))
    log.info("resident exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
