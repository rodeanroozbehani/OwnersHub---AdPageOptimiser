"""Smoke tests for ads_optimizer.thresholds."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ads_optimizer.thresholds import evaluate_changes  # noqa: E402


THRESHOLDS = {"spend_change": 0.20, "conversion_change": 0.20, "ctr_change": 0.15}


def _summary(*, cost: float, conversions: float, ctr: float, days: int) -> dict:
    return {
        "days": days,
        "totals": {
            "cost_aud": cost,
            "conversions": conversions,
            "ctr": ctr,
        },
    }


def test_no_previous_baseline_breaches():
    result = evaluate_changes(
        current_summary=_summary(cost=180, conversions=4, ctr=0.04, days=3),
        previous_summary=None,
        thresholds=THRESHOLDS,
    )
    assert result.breached is True
    assert "no previous baseline" in result.reasons[0]


def test_quiet_run_does_not_breach():
    # Same per-day spend/conversions/CTR — light vs full window
    prev = _summary(cost=14 * 60, conversions=14 * 1.0, ctr=0.04, days=14)
    cur = _summary(cost=3 * 60, conversions=3 * 1.0, ctr=0.04, days=3)
    result = evaluate_changes(cur, prev, THRESHOLDS)
    assert result.breached is False, result.reasons


def test_spend_spike_breaches():
    prev = _summary(cost=14 * 60, conversions=14 * 1.0, ctr=0.04, days=14)
    cur = _summary(cost=3 * 90, conversions=3 * 1.0, ctr=0.04, days=3)  # +50% spend/day
    result = evaluate_changes(cur, prev, THRESHOLDS)
    assert result.breached is True
    assert any("spend/day" in r for r in result.reasons)


def test_conversion_drop_breaches():
    prev = _summary(cost=14 * 60, conversions=14 * 1.0, ctr=0.04, days=14)
    cur = _summary(cost=3 * 60, conversions=3 * 0.5, ctr=0.04, days=3)  # -50% conv/day
    result = evaluate_changes(cur, prev, THRESHOLDS)
    assert result.breached is True
    assert any("conversions/day" in r for r in result.reasons)


def test_ctr_drop_breaches_but_ctr_rise_does_not():
    prev = _summary(cost=14 * 60, conversions=14 * 1.0, ctr=0.04, days=14)
    cur_drop = _summary(cost=3 * 60, conversions=3 * 1.0, ctr=0.025, days=3)  # ~-37% CTR
    cur_rise = _summary(cost=3 * 60, conversions=3 * 1.0, ctr=0.06, days=3)   # +50% CTR

    drop_result = evaluate_changes(cur_drop, prev, THRESHOLDS)
    rise_result = evaluate_changes(cur_rise, prev, THRESHOLDS)

    assert drop_result.breached is True
    assert any("CTR dropped" in r for r in drop_result.reasons)
    assert rise_result.breached is False, rise_result.reasons
