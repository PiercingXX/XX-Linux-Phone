"""Port of EnhancementWindowsTest (app/.../telecom/FactSourceTest seam).

Repeat-caller (15 min) and recent-outgoing (48 h) window edges: the exact
boundary millisecond is outside, one millisecond inside is inside.
"""
from xx_dialer.store import in_recent_outgoing_window, in_repeat_window

NOW = 1800000000000


def test_repeat_1459_is_inside_1500_is_outside():
    fourteen_fifty_nine = 14 * 60 * 1000 + 59 * 1000
    fifteen = 15 * 60 * 1000
    assert in_repeat_window(NOW, NOW - fourteen_fifty_nine)
    assert in_repeat_window(NOW, NOW - fifteen + 1)
    assert not in_repeat_window(NOW, NOW - fifteen)
    assert not in_repeat_window(NOW, NOW - fifteen - 1000)


def test_recent_outgoing_4759_is_inside_4800_is_outside():
    forty_seven = 47 * 60 * 60 * 1000 + 59 * 60 * 1000
    forty_eight = 48 * 60 * 60 * 1000
    assert in_recent_outgoing_window(NOW, NOW - forty_seven)
    assert in_recent_outgoing_window(NOW, NOW - forty_eight + 1)
    assert not in_recent_outgoing_window(NOW, NOW - forty_eight)
    assert not in_recent_outgoing_window(NOW, NOW - forty_eight - 1000)
