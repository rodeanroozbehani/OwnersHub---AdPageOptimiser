"""Load and validate config.yaml. Expands ${VAR} references from the environment."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


REQUIRED_KEYS: list[tuple[str, ...]] = [
    ("website", "url"),
    ("ads", "mode"),
    ("ads", "daily_budget_aud"),
    ("thresholds", "spend_change"),
    ("thresholds", "conversion_change"),
    ("thresholds", "ctr_change"),
    ("lookback_days", "full"),
    ("lookback_days", "light"),
    ("claude", "model"),
    ("claude", "api_key_env"),
    ("storage", "reports_dir"),
    ("storage", "history_file"),
    ("storage", "snapshots_dir"),
    ("storage", "logs_file"),
]

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ConfigError(ValueError):
    """Raised when config.yaml is missing required keys or invalid."""


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))
        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def _get_nested(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = config
    for part in path:
        if not isinstance(cursor, dict) or part not in cursor:
            return _MISSING
        cursor = cursor[part]
    return cursor


_MISSING = object()


def _validate(config: dict[str, Any]) -> None:
    missing: list[str] = []
    for key_path in REQUIRED_KEYS:
        if _get_nested(config, key_path) is _MISSING:
            missing.append(".".join(key_path))
    if missing:
        raise ConfigError(
            "config.yaml is missing required keys: " + ", ".join(missing)
        )

    mode = config["ads"]["mode"]
    if mode not in ("mock", "live"):
        raise ConfigError(f"ads.mode must be 'mock' or 'live', got: {mode!r}")
    if mode == "live" and not config["ads"].get("customer_id"):
        raise ConfigError("ads.mode is 'live' but ads.customer_id is empty")


def load_config(path: str | Path) -> dict[str, Any]:
    """Load YAML config, expand ${ENV_VARS}, validate required keys."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config.yaml top level must be a mapping")

    expanded = _expand_env_vars(raw)
    _validate(expanded)
    return expanded
