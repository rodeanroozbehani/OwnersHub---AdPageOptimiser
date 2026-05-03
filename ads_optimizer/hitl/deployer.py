"""Apply approved HTML changes, git commit and push to GitHub."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def apply_and_deploy(
    html_path: Path,
    repo_dir: Path,
    approved_changes: list[dict[str, Any]],
    branch: str = "main",
    git_user_name: str = "Henry",
    git_user_email: str = "henry@ownershub.com.au",
) -> str:
    """Apply changes to index.html, commit, push. Returns commit URL or ''."""
    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    source = html_path.read_text(encoding="utf-8")
    result = source
    applied_ids: list[str] = []

    for change in approved_changes:
        rr = change.get("re_review_result") or {}
        current = change.get("current_element", "")
        proposed = rr.get("proposed_element") or change.get("proposed_element", "")
        if current and proposed and current in result:
            result = result.replace(current, proposed, 1)
            applied_ids.append(change.get("id", "?"))
        else:
            logger.warning(
                "Skipping %s: current_element not found verbatim in HTML",
                change.get("id", "?"),
            )

    if not applied_ids:
        logger.warning("No changes applied verbatim — skipping commit")
        return ""

    html_path.write_text(result, encoding="utf-8")
    logger.info("Applied changes %s to %s", applied_ids, html_path)

    def _git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    _git("config", "user.name", git_user_name)
    _git("config", "user.email", git_user_email)

    rel = str(html_path.relative_to(repo_dir))
    _git("add", rel)

    ids_str = ", ".join(applied_ids)
    commit_msg = (
        f"Henry: apply {len(applied_ids)} CRO change(s) — {ids_str}\n\n"
        f"Auto-applied by OwnersHub Ad & Page Optimiser after HITL approval."
    )
    _git("commit", "-m", commit_msg)
    _git("push", "origin", branch)

    commit_hash = _git("rev-parse", "HEAD")[:7]
    repo_slug = "rodeanroozbehani/OwnersHub---Landing-Page"
    commit_url = f"https://github.com/{repo_slug}/commit/{commit_hash}"
    logger.info("Pushed commit %s", commit_url)
    return commit_url
