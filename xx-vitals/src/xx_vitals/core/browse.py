"""Browse categories. Port of ui/browse/BrowseScreen.kt + CategoryRow.kt.

Activity and Body open metric lists backed by local sources; Heart Rate,
Nutrition, Sleep and Cycle stay honest "not tracked" placeholders and never
imply an empty chart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

NOT_TRACKED_TAG = "not tracked"


class MetricKey(Enum):
    STEPS = "steps"
    DISTANCE = "distance"
    ACTIVE_MIN = "active_min"
    EFFORT = "effort"
    WEIGHT = "weight"
    BMI = "bmi"


METRIC_LABELS = {
    MetricKey.STEPS: "Steps",
    MetricKey.DISTANCE: "Distance",
    MetricKey.ACTIVE_MIN: "Active minutes",
    MetricKey.EFFORT: "Effort",
    MetricKey.WEIGHT: "Weight",
    MetricKey.BMI: "BMI",
}

METRIC_GLYPHS = {
    MetricKey.STEPS: "◆",
    MetricKey.DISTANCE: "→",
    MetricKey.ACTIVE_MIN: "◷",
    MetricKey.EFFORT: "◇",
    MetricKey.WEIGHT: "▤",
    MetricKey.BMI: "▣",
}


@dataclass(frozen=True)
class BrowseCategory:
    id: str
    label: str
    glyph: str
    tracked: bool
    metrics: tuple = ()


CATEGORIES: tuple = (
    BrowseCategory(
        id="activity", label="Activity", glyph="◈", tracked=True,
        metrics=(MetricKey.STEPS, MetricKey.DISTANCE, MetricKey.ACTIVE_MIN, MetricKey.EFFORT),
    ),
    BrowseCategory(
        id="body", label="Body measurements", glyph="▤", tracked=True,
        metrics=(MetricKey.WEIGHT, MetricKey.BMI),
    ),
    BrowseCategory(id="hr", label="Heart rate", glyph="∿", tracked=False),
    BrowseCategory(id="nutrition", label="Nutrition", glyph="▣", tracked=False),
    BrowseCategory(id="sleep", label="Sleep", glyph="☾", tracked=False),
    BrowseCategory(id="cycle", label="Cycle tracking", glyph="◎", tracked=False),
)

_SEARCHABLE_METRICS = (
    MetricKey.STEPS, MetricKey.DISTANCE, MetricKey.ACTIVE_MIN,
    MetricKey.EFFORT, MetricKey.WEIGHT, MetricKey.BMI,
)


def metric_glyph(key: MetricKey) -> str:
    return METRIC_GLYPHS[key]


def search_rows(query: str) -> list[BrowseCategory]:
    needle = query.strip().lower()
    if not needle:
        return list(CATEGORIES)
    rows = [cat for cat in CATEGORIES if needle in cat.label.lower()]
    for key in _SEARCHABLE_METRICS:
        if needle in METRIC_LABELS[key].lower():
            rows.append(
                BrowseCategory(
                    id=f"metric-{key.name}", label=METRIC_LABELS[key],
                    glyph=metric_glyph(key), tracked=True, metrics=(key,),
                )
            )
    seen: dict[str, BrowseCategory] = {}
    for row in rows:
        seen.setdefault(row.id, row)
    return list(seen.values())


def metrics_for(category_id: str) -> tuple:
    for category in CATEGORIES:
        if category.id == category_id:
            return category.metrics
    return ()
