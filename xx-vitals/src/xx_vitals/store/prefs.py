"""Config store path required by PORT_SPEC.md (re-exports prefs)."""

from xx_vitals.prefs import AppConfig, default_config_path, load, save

__all__ = ["AppConfig", "default_config_path", "load", "save"]
