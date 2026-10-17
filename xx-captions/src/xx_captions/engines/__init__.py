"""Backend choice: sherpa when ready, else vosk, else an explicit gap.

Probing never raises and never downloads: a backend is available only
when its Python package imports AND its model files are installed.
Without either backend the selection names "none" with a human-readable
reason so the service can show "no engine installed" instead of
crashing.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from xx_captions import config, models
from xx_captions.engine import EngineError


@dataclass(frozen=True)
class Selection:
    name: str
    engine: object | None
    reason: str


def _have_package(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def sherpa_status(home: Path | None = None) -> tuple[bool, str]:
    model_id = config.load(home).get("model", config.SHERPA_ID)
    if model_id not in config.MODEL_CATALOG:
        model_id = config.SHERPA_ID
    if not _have_package("sherpa_onnx"):
        return False, ("sherpa_onnx package not installed "
                       f"(model {model_id!r} unavailable)")
    if not models.is_installed(model_id, home):
        return False, f"sherpa model {model_id!r} not installed"
    return True, f"sherpa model {model_id!r} ready"


def vosk_status(home: Path | None = None) -> tuple[bool, str]:
    if not _have_package("vosk"):
        return False, "vosk package not installed"
    if not models.is_installed(config.VOSK_ID, home):
        return False, f"vosk model {config.VOSK_ID!r} not installed"
    return True, f"vosk model {config.VOSK_ID!r} ready"


def select_engine(home: Path | None = None, sherpa_factory=None,
                  vosk_factory=None) -> Selection:
    """Pick the best ready backend; never raises, never downloads."""
    ok, detail = sherpa_status(home)
    if ok:
        try:
            from xx_captions.engines import sherpa as sherpa_mod

            model_id = config.load(home).get("model", config.SHERPA_ID)
            if model_id not in config.MODEL_CATALOG:
                model_id = config.SHERPA_ID
            if sherpa_factory is not None:
                engine = sherpa_factory(models.model_dir(model_id, home))
            else:
                engine = sherpa_mod.SherpaEngine(
                    models.model_dir(model_id, home))
            return Selection("sherpa-onnx", engine, detail)
        except EngineError as exc:
            detail = str(exc)
        except ImportError as exc:
            detail = f"sherpa backend failed to load: {exc}"
    sherpa_detail = detail
    ok, detail = vosk_status(home)
    if ok:
        try:
            from xx_captions.engines import vosk as vosk_mod

            if vosk_factory is not None:
                engine = vosk_factory(models.model_dir(config.VOSK_ID, home))
            else:
                engine = vosk_mod.VoskEngine(
                    models.model_dir(config.VOSK_ID, home))
            return Selection("vosk", engine, detail)
        except EngineError as exc:
            detail = str(exc)
        except ImportError as exc:
            detail = f"vosk backend failed to load: {exc}"
    return Selection("none", None,
                     f"no engine installed ({sherpa_detail}; {detail})")


__all__ = ["Selection", "select_engine", "sherpa_status", "vosk_status"]
