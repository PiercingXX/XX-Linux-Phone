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


def test_reboot_does_not_reopen_bypass():
    """A post-reboot elapsed value aliasing the marker must stay closed."""
    marker_wall = NOW_WALL - 30 * 3600 * 1000
    marker_elapsed = 2 * 3600 * 1000
    now_elapsed = 3 * 3600 * 1000
    assert not emergency_window_active(NOW_WALL, marker_wall,
                                       now_elapsed, marker_elapsed,
                                       "boot-B", "boot-A")


def test_same_boot_elapsed_fresh_keeps_open():
    assert emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1,
                                   10000, 5000, "boot-A", "boot-A")


def test_unknown_boot_ids_keep_legacy_readings():
    assert emergency_window_active(NOW_WALL, NOW_WALL - WINDOW - 1,
                                   10000, 5000)
    assert emergency_window_active(NOW_WALL, NOW_WALL - 1000, 10000,
                                   5000000000, "boot-A", None)


def test_store_bypass_closes_after_reboot(store):
    wall = NOW_WALL - 30 * 3600 * 1000
    store.record_emergency(wall, 2 * 3600 * 1000, "boot-A")
    assert store.read_emergency_boot() == "boot-A"
    assert store.emergency_active(wall + 1_000, 2 * 3600 * 1000 + 1_000,
                                  "boot-A")
    assert not store.emergency_active(NOW_WALL, 3 * 3600 * 1000,
                                      "boot-B")


def test_emergency_boot_column_migrates(tmp_path):
    from xx_dialer.store import Store

    path = tmp_path / "calls.db"
    handle = Store(path)
    handle._db.execute(
        "ALTER TABLE emergency_marker DROP COLUMN lastEmergencyBoot")
    handle._db.commit()
    handle.close()
    migrated = Store(path)
    try:
        migrated.record_emergency(NOW_WALL, 5_000)
        assert migrated.read_emergency() == (NOW_WALL, 5_000)
    finally:
        migrated.close()


def test_marker_round_trips_through_the_store(store):
    assert store.read_emergency() is None
    assert not store.emergency_active(NOW_WALL, NOW_ELAPSED)
    store.record_emergency(NOW_WALL - 1000, NOW_ELAPSED - 1000)
    assert store.read_emergency() == (NOW_WALL - 1000, NOW_ELAPSED - 1000)
    assert store.emergency_active(NOW_WALL, NOW_ELAPSED)
    store.record_emergency(NOW_WALL - WINDOW - 1, NOW_ELAPSED - WINDOW - 1)
    assert not store.emergency_active(NOW_WALL, NOW_ELAPSED)
