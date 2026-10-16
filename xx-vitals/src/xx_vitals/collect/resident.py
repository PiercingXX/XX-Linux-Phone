"""Motion-gated resident step sampler: cheap polls while still, dense while moving.

A 10 s burst every 15 min covers ~1% of the day, and banking its count
as the day's steps presented fiction as measured data. This sampler
instead runs as a long-lived process. While still it reads the
accelerometer once every ~2 s; when a poll deviates from the still
baseline it buffers dense samples as a candidate, confirms motion from
the dense magnitude variance, then counts steps continuously at ~40 Hz
with the peak filter from collect/burst until ~20 s pass with no
motion evidence. Counted-but-unstored steps flush to the database
every minute and on SIGTERM, so a kill loses at most a minute.

Single table bumps never start a session: one deviating poll only
opens a candidate, and the dense variance of still air rejects it, so
no steps and no sleep inhibitor come out of a bump. No estimate is
ever stored — the steps column holds only observed counts.

No I/O at import and no real clock inside: the caller feeds
timestamped samples through advance(), which makes a synthetic day
trace with an injected clock the natural test. Persistence and the
inhibitor gate arrive as injected callables.
"""

from __future__ import annotations

import math

from xx_vitals.collect.burst import (
    DEFAULT_NOISE_FLOOR,
    count_burst_steps,
    learn_noise_floor,
)

STILL_POLL_S = 2.0
MOVING_HZ = 40.0
MOTION_DEV = 0.5
CONFIRM_WINDOW_S = 2.0
CONFIRM_VAR = 0.09
CANDIDATE_TIMEOUT_S = 6.0
EVIDENCE_WINDOW_S = 5.0
EVIDENCE_VAR = 0.02
STILL_AFTER_S = 20.0
PERSIST_EVERY_S = 60.0
BASELINE_ALPHA = 0.05
STILL_HISTORY_CAP = 200

MODE_STILL = "still"
MODE_CANDIDATE = "candidate"
MODE_MOVING = "moving"


def magnitude(sample: tuple[float, float, float]) -> float:
    x, y, z = sample[0], sample[1], sample[2]
    return math.sqrt(x * x + y * y + z * z)


def variance(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


class ResidentSampler:
    """Motion-gated step counter over injected timestamped samples.

    `commit(steps)` stores freshly counted steps (database + queue in
    production); `on_save()` persists sampler state such as the learned
    noise floor; `inhibit_allowed()` gates the logind delay inhibitor.
    """

    def __init__(
        self,
        *,
        noise_floor: float | None = None,
        commit=None,
        on_save=None,
        inhibitor=None,
        inhibit_allowed=None,
    ) -> None:
        self.floor = noise_floor
        self._commit = commit
        self._on_save = on_save
        self._inhibitor = inhibitor
        self._inhibit_allowed = inhibit_allowed
        self.mode = MODE_STILL
        self.pending_steps = 0
        self.total_counted = 0
        self._baseline: float | None = None
        self._last_pick: float | None = None
        self._still_xyz: list[tuple[float, float, float]] = []
        self._candidate: list[tuple[float, tuple[float, float, float]]] = []
        self._candidate_start = 0.0
        self._session: list[tuple[float, float, float]] = []
        self._credited = 0
        self._evidence_t = 0.0
        self._now = 0.0
        self._last_flush: float | None = None

    def _inhibit_ok(self) -> bool:
        try:
            return bool(self._inhibit_allowed()) if self._inhibit_allowed is not None else False
        except Exception:
            return False

    def _ensure_floor(self) -> float:
        if self.floor is None:
            self.floor = learn_noise_floor(self._still_xyz)
        return self.floor

    def advance(self, now: float, samples: list[tuple]) -> int:
        """Feed timestamped (t, x, y, z) samples; returns newly counted steps."""
        self._now = now
        counted = 0
        for item in samples:
            stamp, xyz = item[0], (float(item[1]), float(item[2]), float(item[3]))
            if self.mode == MODE_STILL:
                self._feed_still(stamp, xyz)
            elif self.mode == MODE_CANDIDATE:
                self._feed_candidate(stamp, xyz)
            else:
                self._session.append(xyz)
        if self.mode == MODE_MOVING:
            counted = self._recount(now)
            self._watch_evidence(now)
        if self._last_flush is None:
            self._last_flush = now
        elif now - self._last_flush >= PERSIST_EVERY_S:
            self.flush()
        return counted

    def _feed_still(self, stamp: float, xyz: tuple[float, float, float]) -> None:
        if self._last_pick is not None and stamp - self._last_pick < STILL_POLL_S:
            return
        self._last_pick = stamp
        mag = magnitude(xyz)
        if self._baseline is None:
            self._baseline = mag
            self._remember_still(xyz)
            return
        if abs(mag - self._baseline) > MOTION_DEV:
            self.mode = MODE_CANDIDATE
            self._candidate = [(stamp, xyz)]
            self._candidate_start = stamp
            return
        self._baseline += BASELINE_ALPHA * (mag - self._baseline)
        self._remember_still(xyz)

    def _remember_still(self, xyz: tuple[float, float, float]) -> None:
        self._still_xyz.append(xyz)
        if len(self._still_xyz) > STILL_HISTORY_CAP:
            del self._still_xyz[: len(self._still_xyz) - STILL_HISTORY_CAP]

    def _feed_candidate(self, stamp: float, xyz: tuple[float, float, float]) -> None:
        self._candidate.append((stamp, xyz))
        span = stamp - self._candidate_start
        if span >= CONFIRM_WINDOW_S:
            recent = [magnitude(v) for t, v in self._candidate if t >= stamp - CONFIRM_WINDOW_S]
            if len(recent) >= 8 and variance(recent) > CONFIRM_VAR:
                self._enter_moving(stamp)
                return
        if span >= CANDIDATE_TIMEOUT_S:
            mags = [magnitude(v) for _, v in self._candidate]
            self._baseline = sum(mags) / len(mags)
            for _, v in self._candidate:
                self._remember_still(v)
            self._candidate = []
            self._last_pick = stamp
            self.mode = MODE_STILL

    def _enter_moving(self, stamp: float) -> None:
        self.mode = MODE_MOVING
        self._session = [xyz for _, xyz in self._candidate]
        self._candidate = []
        self._credited = 0
        self._evidence_t = stamp
        self._ensure_floor()
        if self._inhibitor is not None and self._inhibit_ok():
            self._inhibitor.acquire()

    def _recount(self, now: float) -> int:
        assert self.floor is not None
        total = count_burst_steps(list(self._session), MOVING_HZ, self.floor)
        new = total - self._credited
        if new > 0:
            self._credited = total
            self.pending_steps += new
            self.total_counted += new
            self._evidence_t = now
        return new

    def _recent_mags(self, window_s: float) -> list[float]:
        if not self._session:
            return []
        mags = [magnitude(xyz) for xyz in self._session]
        want = max(2, int(window_s * MOVING_HZ))
        return mags[-want:]

    def _watch_evidence(self, now: float) -> None:
        recent = self._recent_mags(EVIDENCE_WINDOW_S)
        if len(recent) >= 8 and variance(recent) > EVIDENCE_VAR:
            self._evidence_t = now
        if now - self._evidence_t >= STILL_AFTER_S:
            self.flush()
            if self._inhibitor is not None:
                self._inhibitor.release()
            recent = self._recent_mags(EVIDENCE_WINDOW_S)
            if recent:
                self._baseline = sum(recent) / len(recent)
            self._session = []
            self._credited = 0
            self._last_pick = now
            self.mode = MODE_STILL

    def flush(self) -> int:
        """Store counted-but-unstored steps now; returns steps stored."""
        flushed = self.pending_steps
        if flushed > 0 and self._commit is not None:
            self._commit(flushed)
        self.pending_steps = 0
        self._last_flush = self._now
        if flushed > 0 and self._on_save is not None:
            try:
                self._on_save()
            except Exception:
                pass
        return flushed

    def handle_sigterm(self) -> int:
        """SIGTERM path: lose at most the current minute, then let go."""
        flushed = self.flush()
        if self._inhibitor is not None:
            self._inhibitor.release()
        return flushed


def _pseudo_hiss(index: int, scale: float) -> float:
    return scale * (float((index * 2654435761) % 1000) / 1000.0 - 0.5)


def measure_cpu() -> dict:
    """Wakeups/s and approximate CPU% per mode over a synthetic run.

    Pure computation on synthetic magnitudes — no real /sys — timed
    with time.process_time(). The still loop mirrors one accelerometer
    read plus the window-variance gate every 2 s for a simulated hour;
    the moving loop mirrors 40 Hz sampling with a session recount every
    5 s for a simulated minute. Wall-clock ratios, so a faster or
    slower phone CPU shifts the percentages; treat them as order of
    magnitude, not a device promise.
    """
    import time as _time

    still_proc_start = _time.process_time()
    baseline = 9.81
    window = [9.81] * 8
    for index in range(1800):
        mag = 9.81 + _pseudo_hiss(index, 0.04)
        window.append(mag)
        del window[0]
        mean = sum(window) / len(window)
        _var = sum((value - mean) ** 2 for value in window) / len(window)
        _hit = abs(mag - baseline) > MOTION_DEV
        baseline += BASELINE_ALPHA * (mag - baseline)
    still_proc = _time.process_time() - still_proc_start

    moving_proc_start = _time.process_time()
    floor = DEFAULT_NOISE_FLOOR
    session: list[tuple[float, float, float]] = []
    for index in range(2400):
        sway = 1.2 * math.sin(2 * math.pi * 1.9 * index / MOVING_HZ)
        session.append((0.0, 0.0, 9.81 + sway + _pseudo_hiss(index, 0.1)))
        if len(session) % 200 == 0:
            count_burst_steps(list(session), MOVING_HZ, floor)
    moving_proc = _time.process_time() - moving_proc_start

    return {
        "still_wakeups_per_s": round(1.0 / STILL_POLL_S, 3),
        "still_cpu_percent": still_proc / 3600.0 * 100.0,
        "moving_wakeups_per_s": MOVING_HZ,
        "moving_cpu_percent": moving_proc / 60.0 * 100.0,
    }
