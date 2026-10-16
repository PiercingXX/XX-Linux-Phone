"""App preferences: goals, profile, units, sync host. Pure stdlib (headless-safe).

Mirrors profile/ProfileRepository.kt semantics: storage is always metric
(kg, metres); the units toggle only changes rendering. A fresh install
defaults to IMPERIAL display; an explicitly saved choice is always honored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xx_vitals.paths import config_dir, ensure_parent_for

DEFAULT_STEPS_GOAL = 10_000
DEFAULT_EFFORT_GOAL = 90
DEFAULT_WEEKLY_EFFORT_GOAL = 150

VALID_UNITS = ("metric", "imperial")


@dataclass
class StepBaseline:
    counter: float = 0.0
    elapsed_ms: int = 0
    wall_ms: int = 0
    source: str = ""


@dataclass
class AppConfig:
    steps_goal: int = DEFAULT_STEPS_GOAL
    effort_goal: int = DEFAULT_EFFORT_GOAL
    weekly_effort_goal: int = DEFAULT_WEEKLY_EFFORT_GOAL
    height_metres: float = 0.0
    birth_year: int = 0
    units: str = "imperial"
    show_journal_tab: bool = True
    show_browse_tab: bool = True
    show_profile_tab: bool = True
    server_url: str = ""
    xxchat_url: str = ""
    relogin_required: bool = False
    allowed_cleartext_hosts: list[str] = field(default_factory=list)
    collect_enabled: bool = False
    count_while_screen_off: bool = False
    step_baseline: StepBaseline | None = None
    step_noise_floor: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        cfg = cls()
        try:
            if int(data.get("steps_goal", cfg.steps_goal)) > 0:
                cfg.steps_goal = int(data["steps_goal"])
        except (ValueError, TypeError, KeyError):
            pass
        try:
            if int(data.get("effort_goal", cfg.effort_goal)) > 0:
                cfg.effort_goal = int(data["effort_goal"])
        except (ValueError, TypeError, KeyError):
            pass
        try:
            if int(data.get("weekly_effort_goal", cfg.weekly_effort_goal)) > 0:
                cfg.weekly_effort_goal = int(data["weekly_effort_goal"])
        except (ValueError, TypeError, KeyError):
            pass
        try:
            height = float(data.get("height_metres", 0.0))
            if height >= 0:
                cfg.height_metres = height
        except (ValueError, TypeError):
            pass
        try:
            year = int(data.get("birth_year", 0))
            if year >= 0:
                cfg.birth_year = year
        except (ValueError, TypeError):
            pass
        units = data.get("units", "imperial")
        if units in VALID_UNITS:
            cfg.units = units
        for key in ("show_journal_tab", "show_browse_tab", "show_profile_tab"):
            if isinstance(data.get(key), bool):
                setattr(cfg, key, data[key])
        server_url = data.get("server_url", "")
        if isinstance(server_url, str):
            cfg.server_url = server_url.strip()
        xxchat_url = data.get("xxchat_url", "")
        if isinstance(xxchat_url, str):
            cfg.xxchat_url = xxchat_url.strip()
        if isinstance(data.get("relogin_required"), bool):
            cfg.relogin_required = data["relogin_required"]
        hosts = data.get("allowed_cleartext_hosts", [])
        if isinstance(hosts, list):
            cfg.allowed_cleartext_hosts = [str(h) for h in hosts if h]
        if isinstance(data.get("collect_enabled"), bool):
            cfg.collect_enabled = data["collect_enabled"]
        if isinstance(data.get("count_while_screen_off"), bool):
            cfg.count_while_screen_off = data["count_while_screen_off"]
        baseline = data.get("step_baseline")
        if isinstance(baseline, dict):
            try:
                counter = float(baseline.get("counter", 0.0))
                elapsed = int(baseline.get("elapsed_ms", 0))
                wall = int(baseline.get("wall_ms", 0))
            except (ValueError, TypeError):
                pass
            else:
                source = baseline.get("source", "")
                cfg.step_baseline = StepBaseline(
                    counter=counter, elapsed_ms=elapsed, wall_ms=wall,
                    source=source if isinstance(source, str) else "",
                )
        try:
            floor = data.get("step_noise_floor")
            if floor is not None and float(floor) > 0:
                cfg.step_noise_floor = float(floor)
        except (ValueError, TypeError):
            pass
        return cfg

    def to_dict(self) -> dict:
        doc = asdict(self)
        if self.step_baseline is None:
            doc.pop("step_baseline", None)
        if self.step_noise_floor is None:
            doc.pop("step_noise_floor", None)
        return doc


def default_config_path() -> Path:
    return config_dir() / "config.json"


def load(path: Path | str | None = None) -> AppConfig:
    """Load config from path, falling back to defaults when missing/invalid."""
    cfg_path = Path(path) if path is not None else default_config_path()
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppConfig()
    if not isinstance(data, dict):
        return AppConfig()
    return AppConfig.from_dict(data)


def save(cfg: AppConfig, path: Path | str | None = None) -> None:
    """Persist config atomically."""
    cfg_path = Path(path) if path is not None else default_config_path()
    ensure_parent_for(cfg_path)
    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(cfg_path)
