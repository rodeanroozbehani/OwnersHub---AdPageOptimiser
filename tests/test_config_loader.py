"""Smoke tests for ads_optimizer.config_loader."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ads_optimizer.config_loader import ConfigError, load_config  # noqa: E402


_VALID_CONFIG = """
website:
  url: https://example.com/
ads:
  mode: mock
  daily_budget_aud: 67
thresholds:
  spend_change: 0.2
  conversion_change: 0.2
  ctr_change: 0.15
lookback_days:
  full: 14
  light: 3
claude:
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
storage:
  reports_dir: reports
  history_file: data/history.json
  snapshots_dir: data/site-snapshots
  logs_file: logs/run.log
"""


def test_loads_valid_config(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_VALID_CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg["website"]["url"] == "https://example.com/"
    assert cfg["ads"]["mode"] == "mock"


def test_missing_required_key_raises(tmp_path: Path):
    bad = _VALID_CONFIG.replace("daily_budget_aud: 67\n", "")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert "ads.daily_budget_aud" in str(exc.value)


def test_live_mode_requires_customer_id(tmp_path: Path):
    bad = _VALID_CONFIG.replace("mode: mock", "mode: live")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert "customer_id" in str(exc.value)


def test_env_var_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_text = _VALID_CONFIG.replace(
        "url: https://example.com/", "url: ${OH_TEST_URL}"
    )
    monkeypatch.setenv("OH_TEST_URL", "https://expanded.example/")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg["website"]["url"] == "https://expanded.example/"


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_invalid_mode_raises(tmp_path: Path):
    bad = _VALID_CONFIG.replace("mode: mock", "mode: weird")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)
