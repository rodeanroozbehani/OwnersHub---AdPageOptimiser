"""Content optimiser: reads local HTML, screenshots the live site, calls Claude for
CRO-focused before/after recommendations. Outputs a Markdown review document plus an
optional proposed index.html with all changes pre-applied for manual diff/review."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .ads_client import AdsClient
from .ads_mock import content_insights_to_dict
from .reporter import append_history, utc_now_iso
from .website_analyzer import WebsiteAnalyzer


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML parsing helpers (local file version — no network call)
# ---------------------------------------------------------------------------

def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ", strip=True).split())


def _parse_local_html(html_path: Path) -> dict[str, Any]:
    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})

    headings: dict[str, list[str]] = {}
    for level in ("h1", "h2", "h3", "h4"):
        tags = [t.get_text(strip=True) for t in soup.find_all(level)]
        if tags:
            headings[level.upper()] = tags

    body_text = _visible_text(soup)

    ctas: list[dict[str, str]] = []
    seen_ctas: set[tuple[str, str]] = set()
    for tag in soup.find_all(["a", "button"]):
        text = tag.get_text(strip=True)
        if not text or len(text) > 80:
            continue
        href = tag.get("href") or ""
        key = (tag.name, text[:50])
        if key not in seen_ctas:
            seen_ctas.add(key)
            ctas.append({"tag": tag.name, "text": text, "href": href})

    forms: list[dict[str, Any]] = []
    for form in soup.find_all("form"):
        action = form.get("action") or ""
        fields = [
            {"name": inp.get("name") or "", "type": inp.get("type") or "text"}
            for inp in form.find_all(["input", "textarea", "select"])
        ]
        forms.append({
            "action": action,
            "field_count": len(fields),
            "fields": fields,
            "is_formspree": "formspree.io" in action,
        })

    return {
        "source": str(html_path),
        "title": title_tag.get_text(strip=True) if title_tag else "",
        "meta_description": meta_desc.get("content", "") if meta_desc else "",
        "headings": headings,
        "body_text_excerpt": body_text[:2500],
        "word_count": len(body_text.split()),
        "cta_elements": ctas,
        "forms": forms,
        "formspree_detected": any(f["is_formspree"] for f in forms),
        "raw_html_length": len(html),
        "raw_html_source": html[:6000],
    }


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _load_previous_content_findings(history_path: Path) -> list[dict[str, Any]]:
    """Return proposed_changes from the most recent content run, or []."""
    if not history_path.exists():
        return []
    try:
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return []
        content_runs = [e for e in entries if e.get("mode") == "content"]
        if not content_runs:
            return []
        return content_runs[-1].get("claude_output", {}).get("proposed_changes", [])
    except (json.JSONDecodeError, KeyError):
        return []


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

_PRIORITY_BADGE = {
    "P0": "🔥 P0 — This week",
    "P1": "⚡ P1 — This fortnight",
    "P2": "💡 P2 — Backlog",
}
_EFFORT_BADGE = {
    "low": "🟢 Low",
    "medium": "🟡 Medium",
    "high": "🔴 High",
}
_PRIORITY_SORT = {"P0": 0, "P1": 1, "P2": 2}


def _write_review_doc(
    output_path: Path,
    claude_output: dict[str, Any],
    html_summary: dict[str, Any],
    run_timestamp_utc: str,
) -> None:
    changes = [c for c in claude_output.get("proposed_changes", []) if isinstance(c, dict)]
    score = claude_output.get("conversion_readiness_score", "N/A")
    assessment = claude_output.get("overall_assessment", "")
    change_log = claude_output.get("proposed_html_changes_summary", "")

    p0 = sum(1 for c in changes if c.get("priority") == "P0")
    p1 = sum(1 for c in changes if c.get("priority") == "P1")
    p2 = sum(1 for c in changes if c.get("priority") == "P2")

    lines: list[str] = [
        "# OwnersHub Content Optimiser Review",
        "",
        f"**Run:** {run_timestamp_utc}",
        f"**Source HTML:** `{html_summary.get('source', '')}`",
        f"**Conversion Readiness Score:** {score} / 10",
        f"**Changes:** {len(changes)} total — P0: {p0}  P1: {p1}  P2: {p2}",
        "",
        "## Overall Assessment",
        "",
        assessment,
        "",
        "---",
        "",
        f"## Proposed Changes ({len(changes)} total)",
        "",
        "> Review each change independently. Apply P0 items first, then re-assess.",
        "",
    ]

    sorted_changes = sorted(
        changes,
        key=lambda c: _PRIORITY_SORT.get(c.get("priority", "P2"), 2),
    )

    for change in sorted_changes:
        cid = change.get("id", "CC-???")
        title = change.get("title", "").strip()
        priority = change.get("priority", "P?")
        section = change.get("section", "")
        dimension = change.get("dimension", "")
        effort = change.get("effort", "")
        reasoning = change.get("reasoning", "")
        expected = change.get("expected_value", "")
        current_el = change.get("current_element", "")
        proposed_el = change.get("proposed_element", "")
        impl_note = change.get("implementation_note", "")

        heading = f"### {title}" if title else f"### {cid} — {section.title()} · {dimension.title()}"

        lines += [
            "---",
            "",
            heading,
            "",
            f"**ID:** {cid}  ",
            f"**Section:** {section.title()} · {dimension.title()}  ",
            f"**Priority:** {_PRIORITY_BADGE.get(priority, priority)}  ",
            f"**Effort:** {_EFFORT_BADGE.get(effort, effort)}  ",
            "",
            "#### Reasoning",
            "",
            reasoning,
            "",
            "#### Expected Value",
            "",
            expected,
            "",
            "#### Current HTML",
            "",
            "```html",
            current_el,
            "```",
            "",
            "#### Proposed HTML",
            "",
            "```html",
            proposed_el,
            "```",
            "",
        ]
        if impl_note:
            lines += [
                "#### Implementation Note",
                "",
                impl_note,
                "",
            ]

    if change_log:
        lines += [
            "---",
            "",
            "## Commit Message / Change Log",
            "",
            "```",
            change_log,
            "```",
            "",
        ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Content review written: %s", output_path)


def _apply_changes_to_html(source_html: str, changes: list[dict[str, Any]]) -> str:
    """String-replace each current_element with its proposed_element.
    Skips any change whose current_element string is not found verbatim."""
    result = source_html
    applied = 0
    for change in changes:
        current = change.get("current_element", "")
        proposed = change.get("proposed_element", "")
        if current and proposed and current in result:
            result = result.replace(current, proposed, 1)
            applied += 1
        else:
            logger.debug(
                "Skipping %s: current_element not found verbatim in HTML",
                change.get("id", "?"),
            )
    logger.info("Applied %d / %d changes to proposed HTML", applied, len(changes))
    return result


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ContentOptimizer:
    def __init__(self, config: dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = project_root
        opt = config.get("content_optimizer", {})

        html_source_str = opt.get("html_source", "")
        self.html_source = Path(html_source_str) if html_source_str else Path()

        output_dir_rel = opt.get("output_dir", "content-review")
        self.output_dir = project_root / output_dir_rel
        self.generate_proposed_html: bool = bool(opt.get("generate_proposed_html", True))
        self.max_changes: int = int(opt.get("max_changes_per_run", 10))

    def run(self, *, dry_run: bool = False) -> Path:
        run_ts = utc_now_iso()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Validate + read local HTML
        if not self.html_source.exists():
            raise FileNotFoundError(
                f"content_optimizer.html_source not found: {self.html_source}\n"
                "Update the path in config.yaml → content_optimizer.html_source"
            )
        logger.info("ContentOptimizer: reading HTML from %s", self.html_source)
        html_text = self.html_source.read_text(encoding="utf-8")
        html_summary = _parse_local_html(self.html_source)

        # 2. Screenshots disabled — HTML source is sufficient for CRO analysis.
        screenshot_paths: dict[str, str] = {}

        # 3. Ads performance insights (search terms, keyword quality, ad copy, opt score)
        ads_insights: dict | None = None
        try:
            lookback = int(self.config["lookback_days"]["full"])
            end_date = date.today()
            start_date = end_date - timedelta(days=lookback - 1)
            ads_client = AdsClient(self.config)
            insights_report = ads_client.fetch_content_insights(start_date, end_date)
            ads_insights = content_insights_to_dict(insights_report)
            logger.info(
                "ContentOptimizer: ads insights fetched (%d search terms, %d keywords, opt score=%s)",
                len(insights_report.search_terms),
                len(insights_report.keyword_quality),
                insights_report.optimization_score,
            )
        except Exception as exc:
            logger.warning("ContentOptimizer: ads insights unavailable (%s) — continuing without", exc)

        # 4. Previous findings context
        history_path = self.project_root / self.config["storage"]["history_file"]
        previous_findings = _load_previous_content_findings(history_path)

        # 5. Claude call
        claude_output: dict[str, Any]
        if dry_run:
            logger.info("ContentOptimizer: DRY RUN — skipping Claude call")
            claude_output = {
                "overall_assessment": "[DRY RUN — Claude call skipped]",
                "conversion_readiness_score": 0,
                "proposed_changes": [],
                "proposed_html_changes_summary": "",
            }
        else:
            from .claude_client import ClaudeClient, ClaudeError  # deferred to avoid circular at import
            client = ClaudeClient(self.config)
            prompt_path = self.project_root / "prompts" / "content_optimizer.txt"
            claude_output = client.review_content(
                prompt_path=prompt_path,
                html_summary=html_summary,
                screenshot_paths=screenshot_paths or None,
                previous_findings=previous_findings,
                ads_insights=ads_insights,
                ownershub_context=self.config["ownershub_context"],
                conversion_goal=self.config["website"].get(
                    "conversion_goal", "formspree_register_interest"
                ),
                max_changes=self.max_changes,
            )

        # 5. Write Markdown review
        today = date.today().isoformat()
        review_path = self.output_dir / f"{today}-content-review.md"
        _write_review_doc(review_path, claude_output, html_summary, run_ts)

        # 6. Optionally write proposed HTML (all changes pre-applied)
        valid_changes = [c for c in claude_output.get("proposed_changes", []) if isinstance(c, dict)]
        if self.generate_proposed_html and valid_changes:
            proposed_path = self.output_dir / f"{today}-proposed-index.html"
            proposed_html = _apply_changes_to_html(html_text, valid_changes)
            proposed_path.write_text(proposed_html, encoding="utf-8")
            logger.info("ContentOptimizer: proposed HTML written: %s", proposed_path)

        # 7. Persist to history so next run sees previous findings
        entry: dict[str, Any] = {
            "mode": "content",
            "run_timestamp_utc": run_ts,
            "html_source": str(self.html_source),
            "conversion_readiness_score": claude_output.get("conversion_readiness_score"),
            "changes_count": len(valid_changes),
            "report_path": str(review_path),
            "claude_output": claude_output,
        }
        append_history(entry, history_path)

        # 8. HITL: create review session and email Henry's notification
        hitl_cfg = self.config.get("hitl", {})
        if hitl_cfg.get("enabled", False) and not dry_run and valid_changes:
            try:
                from .hitl.state import ReviewSession
                from .hitl.mailer import send_review_notification

                session_dir = self.project_root / hitl_cfg.get("session_dir", "data/review-sessions")
                session = ReviewSession.create(
                    session_dir=session_dir,
                    changes=valid_changes,
                    html_source=str(self.html_source),
                    review_path=str(review_path),
                    conversion_readiness_score=claude_output.get("conversion_readiness_score", 0),
                    overall_assessment=claude_output.get("overall_assessment", ""),
                )
                send_review_notification(
                    to_email=hitl_cfg["to_email"],
                    session_id=session.session_id,
                    review_base_url=hitl_cfg["review_base_url"],
                    changes=valid_changes,
                    score=claude_output.get("conversion_readiness_score", 0),
                    assessment=claude_output.get("overall_assessment", ""),
                )
                logger.info("ContentOptimizer: HITL session created %s", session.session_id)
            except Exception as exc:
                logger.warning(
                    "ContentOptimizer: HITL notification failed (%s) — review still written", exc
                )

        logger.info("ContentOptimizer: done. Review: %s", review_path)
        return review_path
