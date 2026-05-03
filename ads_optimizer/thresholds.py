"""Pure functions to evaluate period-over-period change in ads metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThresholdResult:
    breached: bool
    reasons: list[str]
    deltas: dict[str, float]


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0 if current == 0 else 1.0  # 100% change if previous was zero
    return (current - previous) / previous


def _per_day(value: float, days: int) -> float:
    return (value / days) if days > 0 else 0.0


def evaluate_changes(
    current_summary: dict[str, Any],
    previous_summary: dict[str, Any] | None,
    thresholds: dict[str, float],
) -> ThresholdResult:
    """Compare current run totals against previous full-run baseline.

    Both summaries must contain a `totals` dict and a `days` int. Compares
    *per-day* averages so light (3-day) vs full (14-day) windows are commensurate.

    Thresholds keys: spend_change, conversion_change, ctr_change.
    Spend / conversion deltas trigger on absolute change in either direction.
    CTR triggers only on a *drop* below the configured threshold.
    """
    if previous_summary is None:
        return ThresholdResult(
            breached=True,
            reasons=["no previous baseline available"],
            deltas={},
        )

    cur_totals = current_summary["totals"]
    prev_totals = previous_summary["totals"]
    cur_days = current_summary.get("days", 1)
    prev_days = previous_summary.get("days", 1)

    cur_spend_pd = _per_day(float(cur_totals["cost_aud"]), cur_days)
    prev_spend_pd = _per_day(float(prev_totals["cost_aud"]), prev_days)
    cur_conv_pd = _per_day(float(cur_totals["conversions"]), cur_days)
    prev_conv_pd = _per_day(float(prev_totals["conversions"]), prev_days)
    cur_ctr = float(cur_totals.get("ctr") or 0.0)
    prev_ctr = float(prev_totals.get("ctr") or 0.0)

    spend_delta = _pct_change(cur_spend_pd, prev_spend_pd)
    conv_delta = _pct_change(cur_conv_pd, prev_conv_pd)
    ctr_delta = _pct_change(cur_ctr, prev_ctr)

    deltas = {
        "spend_per_day": spend_delta,
        "conversions_per_day": conv_delta,
        "ctr": ctr_delta,
    }

    reasons: list[str] = []
    if abs(spend_delta) >= thresholds["spend_change"]:
        reasons.append(
            f"spend/day changed {spend_delta:+.1%} "
            f"(threshold ±{thresholds['spend_change']:.0%})"
        )
    if abs(conv_delta) >= thresholds["conversion_change"]:
        reasons.append(
            f"conversions/day changed {conv_delta:+.1%} "
            f"(threshold ±{thresholds['conversion_change']:.0%})"
        )
    if ctr_delta <= -thresholds["ctr_change"]:
        reasons.append(
            f"CTR dropped {ctr_delta:+.1%} "
            f"(threshold -{thresholds['ctr_change']:.0%})"
        )

    return ThresholdResult(
        breached=bool(reasons),
        reasons=reasons,
        deltas=deltas,
    )
