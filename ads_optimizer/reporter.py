"""Markdown report writer + atomic JSON history append + snapshot retention."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_history(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    try:
        with history_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        logger.warning("history.json was not a list, resetting in-memory copy")
        return []
    except json.JSONDecodeError as exc:
        logger.error("history.json corrupted (%s); ignoring and starting fresh in-memory", exc)
        return []


def append_history(entry: dict[str, Any], history_path: Path) -> None:
    history = _read_history(history_path)
    history.append(entry)
    _atomic_write_text(history_path, json.dumps(history, indent=2, default=str))


def read_last_full_run(history_path: Path) -> dict[str, Any] | None:
    history = _read_history(history_path)
    for entry in reversed(history):
        if entry.get("mode") == "full":
            return entry
    return None


def prune_old_snapshots(snapshots_dir: Path, retention_days: int) -> int:
    if not snapshots_dir.exists():
        return 0
    cutoff = date.today() - timedelta(days=retention_days)
    removed = 0
    for child in snapshots_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = date.fromisoformat(child.name)
        except ValueError:
            continue  # not a YYYY-MM-DD directory; leave alone
        if child_date < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("reporter: pruned %d snapshot directories older than %d days", removed, retention_days)
    return removed


def _format_currency(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _ads_metrics_table(ads_data: dict[str, Any]) -> str:
    totals = ads_data.get("totals", {})
    rows = [
        ("Source", ads_data.get("source", "?")),
        ("Window", f"{ads_data.get('start_date', '')} → {ads_data.get('end_date', '')} ({ads_data.get('days', '?')} days)"),
        ("Impressions", f"{totals.get('impressions', 0):,}"),
        ("Clicks", f"{totals.get('clicks', 0):,}"),
        ("Spend", _format_currency(totals.get("cost_aud", 0))),
        ("Conversions", f"{totals.get('conversions', 0)}"),
        ("CTR", _format_pct(totals.get("ctr", 0))),
        ("Avg CPC", _format_currency(totals.get("avg_cpc_aud", 0))),
        ("Conv. rate", _format_pct(totals.get("conversion_rate", 0))),
    ]
    body = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return "| Metric | Value |\n|---|---|\n" + body


def _findings_section(title: str, findings: list[dict[str, Any]]) -> str:
    if not findings:
        return f"### {title}\n\n_No findings reported._\n"
    lines = [f"### {title}\n"]
    for f in findings:
        lines.append(f"- **[{f.get('severity', '?').upper()}] {f.get('id', '?')}** — {f.get('summary', '')}")
        if f.get("evidence"):
            lines.append(f"  - Evidence: {f['evidence']}")
        if f.get("area"):
            lines.append(f"  - Area: {f['area']}")
    return "\n".join(lines) + "\n"


def _recommendations_section(recs: list[dict[str, Any]]) -> str:
    if not recs:
        return "### Recommendations\n\n_None._\n"
    lines = ["### Prioritised recommendations\n"]
    for r in sorted(recs, key=lambda x: (x.get("priority") or "P9", x.get("id") or "")):
        lines.append(f"- **{r.get('priority', '?')} · {r.get('category', '?')} · {r.get('id', '?')}** — {r.get('action', '')}")
        if r.get("expected_impact"):
            lines.append(f"  - Expected impact: {r['expected_impact']}")
        if r.get("effort"):
            lines.append(f"  - Effort: {r['effort']}")
    return "\n".join(lines) + "\n"


def _experiments_section(experiments: list[dict[str, Any]]) -> str:
    if not experiments:
        return "### Test plan\n\n_No experiments proposed._\n"
    lines = ["### Test plan\n"]
    for e in experiments:
        lines.append(f"- **{e.get('id', '?')}** — _Hypothesis:_ {e.get('hypothesis', '')}")
        if e.get("variant"):
            lines.append(f"  - Variant: {e['variant']}")
        if e.get("metric"):
            lines.append(f"  - Metric: {e['metric']}")
        if e.get("duration_days"):
            lines.append(f"  - Duration: {e['duration_days']} days")
    return "\n".join(lines) + "\n"


def _website_section(website: dict[str, Any]) -> str:
    lines = ["### Website snapshot\n"]
    lines.append(f"- URL: {website.get('url', '?')}")
    lines.append(f"- Status: HTTP {website.get('status_code', '?')}")
    lines.append(f"- Title: {website.get('title', '')!r}")
    lines.append(f"- Meta description: {website.get('meta_description', '')!r}")
    lines.append(f"- Word count: {website.get('word_count', 0)}")
    lines.append(f"- H1s: {website.get('headings', {}).get('h1', [])}")
    lines.append(f"- Formspree detected: {website.get('formspree_detected', False)}")
    paths = website.get("screenshot_paths") or {}
    if paths:
        lines.append(f"- Screenshots: {', '.join(f'{k}={v}' for k, v in paths.items())}")
    if website.get("screenshot_error"):
        lines.append(f"- Screenshot error: {website['screenshot_error']}")
    return "\n".join(lines) + "\n"


def write_markdown_report(
    *,
    claude_output: dict[str, Any] | None,
    ads_data: dict[str, Any],
    website_data: dict[str, Any] | None,
    mode: str,
    run_timestamp_utc: str,
    out_dir: Path,
    threshold_result: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Render a Markdown report and return its path."""
    today = date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{today}-{mode}.md"

    lines: list[str] = []
    lines.append(f"# OwnersHub Optimiser Report — {today} ({mode.upper()})\n")
    lines.append(f"_Run at {run_timestamp_utc} UTC_\n")

    if notes:
        lines.append("## Run notes\n")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## Ads metrics\n")
    lines.append(_ads_metrics_table(ads_data) + "\n")

    if threshold_result is not None:
        lines.append("## Threshold check\n")
        lines.append(f"- Breached: **{threshold_result.get('breached', False)}**")
        for reason in threshold_result.get("reasons", []) or []:
            lines.append(f"- {reason}")
        deltas = threshold_result.get("deltas") or {}
        for k, v in deltas.items():
            try:
                lines.append(f"- Δ {k}: {float(v):+.1%}")
            except (TypeError, ValueError):
                lines.append(f"- Δ {k}: {v}")
        lines.append("")

    if website_data is not None:
        lines.append("## Website\n")
        lines.append(_website_section(website_data))

    if claude_output is None:
        lines.append("## Claude analysis\n\n_Skipped this run._\n")
    else:
        lines.append("## Assumptions\n")
        for a in claude_output.get("assumptions", []) or []:
            lines.append(f"- {a}")
        lines.append("")
        lines.append("## Findings\n")
        lines.append(_findings_section("Ads findings", claude_output.get("ads_findings", []) or []))
        lines.append(_findings_section("Website findings", claude_output.get("website_findings", []) or []))
        lines.append("## Actions\n")
        lines.append(_recommendations_section(claude_output.get("recommendations", []) or []))
        lines.append(_experiments_section(claude_output.get("experiments", []) or []))

    _atomic_write_text(report_path, "\n".join(lines).rstrip() + "\n")
    return report_path


def make_history_entry(
    *,
    mode: str,
    run_timestamp_utc: str,
    ads_data: dict[str, Any],
    website_data: dict[str, Any] | None,
    claude_output: dict[str, Any] | None,
    threshold_result: dict[str, Any] | None,
    report_path: Path | None,
    snapshot_dir: Path | None,
    notes: list[str] | None,
) -> dict[str, Any]:
    rec_ids = []
    if claude_output:
        for r in claude_output.get("recommendations", []) or []:
            if r.get("id"):
                rec_ids.append(r["id"])
    return {
        "timestamp_utc": run_timestamp_utc,
        "mode": mode,
        "totals": ads_data.get("totals", {}),
        "days": ads_data.get("days"),
        "start_date": ads_data.get("start_date"),
        "end_date": ads_data.get("end_date"),
        "source": ads_data.get("source"),
        "website_summary": (
            None if website_data is None else {
                "url": website_data.get("url"),
                "status_code": website_data.get("status_code"),
                "title": website_data.get("title"),
                "word_count": website_data.get("word_count"),
                "formspree_detected": website_data.get("formspree_detected"),
                "h1": (website_data.get("headings") or {}).get("h1", []),
            }
        ),
        "threshold_result": threshold_result,
        "recommendation_ids": rec_ids,
        "report_path": str(report_path) if report_path else None,
        "snapshot_dir": str(snapshot_dir) if snapshot_dir else None,
        "notes": notes or [],
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
