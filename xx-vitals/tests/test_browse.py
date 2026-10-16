"""Browse categories: tracked groups open metrics, the rest stay honest
"not tracked" placeholders, and search finds both levels."""

from xx_vitals.core import browse
from xx_vitals.core.browse import MetricKey


def test_six_categories_with_expected_tracked_flags():
    assert [cat.id for cat in browse.CATEGORIES] == [
        "activity", "body", "hr", "nutrition", "sleep", "cycle",
    ]
    assert browse.CATEGORIES[0].tracked is True
    assert browse.CATEGORIES[1].tracked is True
    assert all(cat.tracked is False for cat in browse.CATEGORIES[2:])


def test_tracked_groups_carry_their_metrics():
    assert browse.metrics_for("activity") == (
        MetricKey.STEPS, MetricKey.DISTANCE, MetricKey.ACTIVE_MIN, MetricKey.EFFORT,
    )
    assert browse.metrics_for("body") == (MetricKey.WEIGHT, MetricKey.BMI)
    assert browse.metrics_for("sleep") == ()
    assert browse.metrics_for("nope") == ()


def test_not_tracked_tag_is_honest():
    assert browse.NOT_TRACKED_TAG == "not tracked"


def test_metric_glyphs_are_single_text_chars():
    for key in MetricKey:
        glyph = browse.metric_glyph(key)
        assert isinstance(glyph, str) and len(glyph) == 1


def test_blank_query_returns_all_categories():
    assert browse.search_rows("") == list(browse.CATEGORIES)
    assert browse.search_rows("   ") == list(browse.CATEGORIES)


def test_search_finds_categories_case_insensitively():
    rows = browse.search_rows("sleep")
    assert [row.id for row in rows] == ["sleep"]
    rows = browse.search_rows("ACTIVITY")
    assert "activity" in [row.id for row in rows]


def test_search_finds_metrics_as_single_rows():
    rows = browse.search_rows("steps")
    metric_rows = [row for row in rows if row.id == "metric-STEPS"]
    assert len(metric_rows) == 1
    assert metric_rows[0].metrics == (MetricKey.STEPS,)
    assert metric_rows[0].tracked is True
