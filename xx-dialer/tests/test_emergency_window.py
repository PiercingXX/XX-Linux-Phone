"""Port of EmergencyWindowTest (app/.../telecom/EmergencyWindowTest.kt).

The 24 h post-emergency bypass closes only when BOTH readings agree it is
over; every single-reading corruption errs toward open.
"""
from xx_dialer.store import EMERGENCY_WINDOW_MS, emergency_window_active

NOW_WALL = 1800000000000
NOW_ELAPSED = 500000
WINDOW = EMERGENCY_WINDOW_MS


def test_no_marker_is_closed():
    assert not emergency_window_active(NOW_WALL, None, NOW_ELAPSED, None)


def test_both_readings_fresh_is_open():
    assert emergency_window_active(NOW_WALL, NOW_WALL - WINDOW // 2,
                                   NOW_ELAPSED,
                                   NOW_ELAPSED - WINDOW // 2)


def test_both_readings_stale_is_closed():
    assert not emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1,
                                       NOW_ELAPSED,
                                       NOW_ELAPSED - WINDOW - 1)


def test_exactly_24h_old_is_stale():
    assert not emergency_window_active(NOW_WALL, NOW_WALL - WINDOW,
                                       NOW_ELAPSED, NOW_ELAPSED - WINDOW)


def test_clock_moved_forward_wall_stale_elapsed_keeps_open():
    assert emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 3600000,
                                   NOW_ELAPSED, NOW_ELAPSED - 1000)


def test_clock_moved_backward_wall_clamps_fresh_window_open():
    assert emergency_window_active(NOW_WALL, NOW_WALL + 86400000,
                                   NOW_ELAPSED, NOW_ELAPSED - WINDOW - 1000)


def test_short_uptime_elapsed_fresh_keeps_open_when_wall_stale():
    assert emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1, 10000,
                                   5000)
    assert not emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1,
                                       10000, None)


def test_reboot_makes_elapsed_unusable_stale_wall_closes_window():
    assert not emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1,
                                       10000, 5000000000)


def test_reboot_makes_elapsed_unusable_fresh_wall_keeps_open():
    assert emergency_window_active(NOW_WALL, NOW_WALL - 1000, 10000,
                                   5000000000)


def test_marker_round_trips_through_the_store(store):
    assert store.read_emergency() is None
    assert not store.emergency_active(NOW_WALL, NOW_ELAPSED)
    store.record_emergency(NOW_WALL - 1000, NOW_ELAPSED - 1000)
    assert store.read_emergency() == (NOW_WALL - 1000, NOW_ELAPSED - 1000)
    assert store.emergency_active(NOW_WALL, NOW_ELAPSED)
    store.record_emergency(NOW_WALL - WINDOW - 1, NOW_ELAPSED - WINDOW - 1)
    assert not store.emergency_active(NOW_WALL, NOW_ELAPSED)
