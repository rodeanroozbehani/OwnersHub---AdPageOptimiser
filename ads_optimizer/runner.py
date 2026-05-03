"""Orchestrates the full, light, and content flows.

Resilience features built into every run:
  - Dormant guard: if no active campaign for 30+ days, writes .dormant and skips
    future runs until the file is deleted.
  - Campaign activity check: if the ads window shows zero spend/impressions, Claude
    is not called (no wasted API cost on stale data).
  - Claude retry: on failure, writes data/retry_pending.json. The next scheduled run
    detects it, logs a retry notice, and tries again with fresh data. After 3
    consecutive failures the file is cleared and a CRITICAL is logged.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ads_mock, website_analyzer
from .ads_client import AdsClient
from .claude_client import ClaudeClient, ClaudeError
from .reporter import (
    append_history,
    make_history_entry,
    prune_old_snapshots,
    read_last_full_run,
    utc_now_iso,
    write_markdown_report,
)
from .thresholds import evaluate_changes
from .website_analyzer import WebsiteAnalyzer


logger = logging.getLogger(__name__)

_RETRY_MAX = 3      # give up after this many consecutive Claude failures
_DORMANT_DAYS = 30  # write .dormant marker after this many days with no active campaign


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _retry_file(project_root: Path) -> Path:
    return project_root / "data" / "retry_pending.json"


def _dormant_file(project_root: Path) -> Path:
    return project_root / ".dormant"


def _project_paths(config: dict[str, Any], project_root: Path) -> dict[str, Path]:
    storage = config["storage"]
    return {
        "reports": project_root / storage["reports_dir"],
        "history": project_root / storage["history_file"],
        "snapshots": project_root / storage["snapshots_dir"],
    }


def _today_snapshot_dir(snapshots_dir: Path) -> Path:
    return snapshots_dir / date.today().isoformat()


def _empty_ads_data(start: date, end: date) -> dict[str, Any]:
    """Minimal ads_data dict used when the live API fetch fails."""
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": (end - start).days + 1,
        "source": "error",
        "totals": {
            "impressions": 0, "clicks": 0,
            "cost_aud": "0.00", "conversions": 0.0,
            "ctr": 0.0, "avg_cpc_aud": "0.00", "conversion_rate": 0.0,
        },
        "campaigns": [],
    }


def _threshold_to_dict(result: Any) -> dict[str, Any]:
    return {
        "breached": result.breached,
        "reasons": result.reasons,
        "deltas": result.deltas,
    }


# ---------------------------------------------------------------------------
# Campaign-activity guard
# ---------------------------------------------------------------------------

def _has_active_campaign(ads_data: dict[str, Any]) -> bool:
    """True if the ads report shows any impressions or spend in the window."""
    totals = ads_data.get("totals", {})
    try:
        has_impressions = int(totals.get("impressions", 0)) > 0
        has_spend = float(str(totals.get("cost_aud", "0"))) > 0
    except (TypeError, ValueError):
        return False
    return has_impressions or has_spend


# ---------------------------------------------------------------------------
# Dormancy helpers
# ---------------------------------------------------------------------------

def _is_dormant(project_root: Path) -> bool:
    """True if the .dormant marker file exists (skips the run)."""
    df = _dormant_file(project_root)
    if df.exists():
        logger.warning(
            "System is DORMANT (.dormant file exists). Skipping run. "
            "Delete '%s' to resume once campaigns are restarted.", df,
        )
        return True
    return False


def _days_since_last_active(history_path: Path) -> int | None:
    """Scan history newest-first. Return days since the most recent entry that had
    an active campaign. Returns None if history is empty or has no such entry."""
    if not history_path.exists():
        return None
    try:
        entries = json.loads(history_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(entries, list):
        return None
    now = datetime.now(timezone.utc)
    for entry in reversed(entries):
        # Entries written before this feature have no active_campaign key — treat as active
        if entry.get("active_campaign", True):
            ts_str = entry.get("run_timestamp_utc") or entry.get("timestamp_utc", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                return (now - ts).days
            except (ValueError, AttributeError):
                continue
    return None


def _check_dormant_threshold(project_root: Path, history_path: Path) -> None:
    """Write the .dormant marker if campaigns have been inactive for _DORMANT_DAYS."""
    df = _dormant_file(project_root)
    if df.exists():
        return
    days = _days_since_last_active(history_path)
    if days is not None and days >= _DORMANT_DAYS:
        df.write_text(
            f"Dormant: no active campaign detected for {days} days as of "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.\n"
            "Delete this file to resume scheduled runs after restarting your campaigns.\n",
            encoding="utf-8",
        )
        logger.critical(
            "DORMANT: No active campaign detected for %d days. "
            "All scheduled runs are now suspended. "
            "Delete '%s' to resume after restarting your campaigns.",
            days, df,
        )


# ---------------------------------------------------------------------------
# Claude retry helpers
# ---------------------------------------------------------------------------

def _read_retry_state(project_root: Path) -> dict[str, Any] | None:
    rf = _retry_file(project_root)
    if not rf.exists():
        return None
    try:
        return json.loads(rf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _note_retry_if_pending(project_root: Path) -> None:
    """Log a notice if a prior run failed Claude. Clears state if max retries exhausted."""
    state = _read_retry_state(project_root)
    if state is None:
        return
    count = state.get("retry_count", 1)
    first_failure = state.get("failed_at", "unknown")
    if count >= _RETRY_MAX:
        logger.critical(
            "Claude API unreachable for %d consecutive attempts (first failure: %s). "
            "Automatic retries exhausted. "
            "Check your ANTHROPIC_API_KEY and Anthropic service status. "
            "Delete '%s' to reset and re-enable retries.",
            count, first_failure, _retry_file(project_root),
        )
        _retry_file(project_root).unlink(missing_ok=True)
    else:
        logger.info(
            "Claude retry: attempt %d/%d (first failure: %s). "
            "Fetching fresh data and retrying.",
            count + 1, _RETRY_MAX, first_failure,
        )


def _on_claude_success(project_root: Path) -> None:
    """Clear any pending retry state after a successful Claude call."""
    rf = _retry_file(project_root)
    if rf.exists():
        rf.unlink(missing_ok=True)
        logger.info("Claude retry state cleared — call succeeded.")


def _on_claude_failure(project_root: Path, error: Exception) -> None:
    """Record a Claude failure. Increment retry count. Log CRITICAL if max reached."""
    rf = _retry_file(project_root)
    rf.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_retry_state(project_root) or {}
    new_count = existing.get("retry_count", 0) + 1
    state: dict[str, Any] = {
        "failed_at": existing.get("failed_at", utc_now_iso()),  # preserve original failure time
        "last_failure_at": utc_now_iso(),
        "retry_count": new_count,
        "error": str(error)[:500],
    }
    tmp = rf.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, rf)

    if new_count >= _RETRY_MAX:
        logger.critical(
            "Claude API unreachable for %d consecutive attempts. "
            "No further automatic retries will be made. "
            "Delete '%s' to reset after resolving the issue.",
            new_count, rf,
        )
    else:
        logger.warning(
            "Claude call failed (attempt %d/%d). "
            "Will retry automatically on the next scheduled run.",
            new_count, _RETRY_MAX,
        )


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------

def run_full(config: dict[str, Any], project_root: Path, *, dry_run: bool = False) -> Path | None:
    # Dormant guard — skip in dry-run so testing always works
    if not dry_run and _is_dormant(project_root):
        return None

    _note_retry_if_pending(project_root)

    paths = _project_paths(config, project_root)
    run_ts = utc_now_iso()
    snapshot_dir = _today_snapshot_dir(paths["snapshots"])
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    lookback = int(config["lookback_days"]["full"])
    end = date.today()
    start = end - timedelta(days=lookback - 1)

    notes: list[str] = []
    claude_output: dict[str, Any] | None = None

    ads_client = AdsClient(config)
    try:
        ads_report = ads_client.fetch_metrics(start, end)
        ads_data = ads_mock.report_to_dict(ads_report)
        campaign_active = _has_active_campaign(ads_data)
    except Exception as exc:
        logger.warning("Ads fetch failed (%s) — continuing with empty data.", exc)
        notes.append(f"Ads fetch error: {str(exc)[:150]}")
        ads_data = _empty_ads_data(start, end)
        campaign_active = True  # don't skip analysis just because the API is unavailable

    if not campaign_active:
        notes.append("No active campaign data in this window — Claude call skipped.")
        logger.info("run_full: no active campaign detected — skipping Claude and website analysis.")
        # Still write a minimal history entry and check dormancy
        entry = make_history_entry(
            mode="full",
            run_timestamp_utc=run_ts,
            ads_data=ads_data,
            website_data=None,
            claude_output=None,
            threshold_result=None,
            report_path=None,
            snapshot_dir=None,
            notes=notes,
        )
        entry["active_campaign"] = False
        append_history(entry, paths["history"])
        _check_dormant_threshold(project_root, paths["history"])
        return None

    analyzer = WebsiteAnalyzer(config)
    web_report = analyzer.analyze(snapshot_dir, fetched_at_utc=run_ts)
    website_data = website_analyzer.report_to_dict(web_report)

    previous = read_last_full_run(paths["history"])

    if dry_run:
        notes.append("DRY RUN: skipped Claude call.")
    else:
        try:
            client = ClaudeClient(config)
            prompt_path = project_root / "prompts" / "optimizer.txt"
            claude_output = client.optimize(
                prompt_path=prompt_path,
                ads_data=ads_data,
                website_data=website_data,
                previous_summary=previous,
                ownershub_context=config["ownershub_context"],
                budget_aud=float(config["ads"]["daily_budget_aud"]),
                screenshot_paths=web_report.screenshot_paths or None,
            )
            _on_claude_success(project_root)
        except ClaudeError as exc:
            logger.error("Claude call failed: %s", exc)
            notes.append(f"Claude error: {exc}")
            _on_claude_failure(project_root, exc)

    report_path = write_markdown_report(
        claude_output=claude_output,
        ads_data=ads_data,
        website_data=website_data,
        mode="full",
        run_timestamp_utc=run_ts,
        out_dir=paths["reports"],
        threshold_result=None,
        notes=notes,
    )

    entry = make_history_entry(
        mode="full",
        run_timestamp_utc=run_ts,
        ads_data=ads_data,
        website_data=website_data,
        claude_output=claude_output,
        threshold_result=None,
        report_path=report_path,
        snapshot_dir=snapshot_dir,
        notes=notes,
    )
    entry["active_campaign"] = True
    append_history(entry, paths["history"])

    retention = int(config["storage"].get("snapshot_retention_days", 90))
    prune_old_snapshots(paths["snapshots"], retention)
    _check_dormant_threshold(project_root, paths["history"])

    logger.info("Full run complete. Report: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Light run
# ---------------------------------------------------------------------------

def run_light(config: dict[str, Any], project_root: Path, *, dry_run: bool = False) -> Path | None:
    if not dry_run and _is_dormant(project_root):
        return None

    _note_retry_if_pending(project_root)

    paths = _project_paths(config, project_root)
    run_ts = utc_now_iso()

    lookback = int(config["lookback_days"]["light"])
    end = date.today()
    start = end - timedelta(days=lookback - 1)

    notes: list[str] = []
    claude_output: dict[str, Any] | None = None
    website_data: dict[str, Any] | None = None
    snapshot_dir: Path | None = None

    ads_client = AdsClient(config)
    try:
        ads_report = ads_client.fetch_metrics(start, end)
        ads_data = ads_mock.report_to_dict(ads_report)
        campaign_active = _has_active_campaign(ads_data)
    except Exception as exc:
        logger.warning("Ads fetch failed (%s) — continuing with empty data.", exc)
        notes.append(f"Ads fetch error: {str(exc)[:150]}")
        ads_data = _empty_ads_data(start, end)
        campaign_active = True

    if not campaign_active:
        notes.append("No active campaign data in this window — light pass skipped.")
        logger.info("run_light: no active campaign detected.")
        previous = read_last_full_run(paths["history"])
        threshold_result = evaluate_changes(
            current_summary=ads_data,
            previous_summary=previous,
            thresholds=config["thresholds"],
        )
        report_path = write_markdown_report(
            claude_output=None,
            ads_data=ads_data,
            website_data=None,
            mode="light",
            run_timestamp_utc=run_ts,
            out_dir=paths["reports"],
            threshold_result=_threshold_to_dict(threshold_result),
            notes=notes,
        )
        entry = make_history_entry(
            mode="light",
            run_timestamp_utc=run_ts,
            ads_data=ads_data,
            website_data=None,
            claude_output=None,
            threshold_result=_threshold_to_dict(threshold_result),
            report_path=report_path,
            snapshot_dir=None,
            notes=notes,
        )
        entry["active_campaign"] = False
        append_history(entry, paths["history"])
        retention = int(config["storage"].get("snapshot_retention_days", 90))
        prune_old_snapshots(paths["snapshots"], retention)
        _check_dormant_threshold(project_root, paths["history"])
        return report_path

    previous = read_last_full_run(paths["history"])
    threshold_result = evaluate_changes(
        current_summary=ads_data,
        previous_summary=previous,
        thresholds=config["thresholds"],
    )

    if not threshold_result.breached:
        notes.append("No significant change. Light pass logged without Claude call.")
        logger.info("Light run: no thresholds breached. Deltas: %s", threshold_result.deltas)
    else:
        logger.info("Light run: thresholds breached: %s", threshold_result.reasons)
        snapshot_dir = _today_snapshot_dir(paths["snapshots"])
        analyzer = WebsiteAnalyzer(config)
        web_report = analyzer.analyze(snapshot_dir, fetched_at_utc=run_ts)
        website_data = website_analyzer.report_to_dict(web_report)

        if dry_run:
            notes.append("DRY RUN: skipped Claude call despite thresholds breached.")
        else:
            try:
                client = ClaudeClient(config)
                prompt_path = project_root / "prompts" / "light_check.txt"
                claude_output = client.optimize(
                    prompt_path=prompt_path,
                    ads_data=ads_data,
                    website_data=website_data,
                    previous_summary=previous,
                    ownershub_context=config["ownershub_context"],
                    budget_aud=float(config["ads"]["daily_budget_aud"]),
                    screenshot_paths=web_report.screenshot_paths or None,
                )
                _on_claude_success(project_root)
            except ClaudeError as exc:
                logger.error("Claude call failed: %s", exc)
                notes.append(f"Claude error: {exc}")
                _on_claude_failure(project_root, exc)

    report_path = write_markdown_report(
        claude_output=claude_output,
        ads_data=ads_data,
        website_data=website_data,
        mode="light",
        run_timestamp_utc=run_ts,
        out_dir=paths["reports"],
        threshold_result=_threshold_to_dict(threshold_result),
        notes=notes,
    )

    entry = make_history_entry(
        mode="light",
        run_timestamp_utc=run_ts,
        ads_data=ads_data,
        website_data=website_data,
        claude_output=claude_output,
        threshold_result=_threshold_to_dict(threshold_result),
        report_path=report_path,
        snapshot_dir=snapshot_dir,
        notes=notes,
    )
    entry["active_campaign"] = True
    append_history(entry, paths["history"])

    retention = int(config["storage"].get("snapshot_retention_days", 90))
    prune_old_snapshots(paths["snapshots"], retention)
    _check_dormant_threshold(project_root, paths["history"])

    logger.info("Light run complete. Report: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Content run
# ---------------------------------------------------------------------------

def run_content(config: dict[str, Any], project_root: Path, *, dry_run: bool = False) -> Path | None:
    if not dry_run and _is_dormant(project_root):
        return None

    _note_retry_if_pending(project_root)

    from .content_optimizer import ContentOptimizer

    optimizer = ContentOptimizer(config, project_root)
    try:
        report_path = optimizer.run(dry_run=dry_run)
        _on_claude_success(project_root)
        logger.info("Content run complete. Review: %s", report_path)
        return report_path
    except ClaudeError as exc:
        logger.error("Content optimizer: Claude call failed: %s", exc)
        _on_claude_failure(project_root, exc)
        return None  # clean exit; retry happens on next scheduled run
