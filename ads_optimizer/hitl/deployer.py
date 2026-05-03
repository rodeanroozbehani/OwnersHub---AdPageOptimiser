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
    applied: list[dict[str, Any]] = []

    for change in approved_changes:
        rr = change.get("re_review_result") or {}
        current = change.get("current_element", "")
        proposed = rr.get("proposed_element") or change.get("proposed_element", "")
        title = (rr.get("title") or change.get("title") or "").strip()
        if current and proposed and current in result:
            result = result.replace(current, proposed, 1)
            applied.append({"id": change.get("id", "?"), "title": title})
        else:
            logger.warning(
                "Skipping %s: current_element not found verbatim in HTML",
                change.get("id", "?"),
            )

    if not applied:
        logger.warning("No changes applied verbatim — skipping commit")
        return ""

    html_path.write_text(result, encoding="utf-8")
    logger.info("Applied changes %s to %s", [a["id"] for a in applied], html_path)

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

    # Build a spartan commit message from the change titles.
    if len(applied) == 1:
        commit_msg = applied[0]["title"] or applied[0]["id"]
    else:
        subject = f"{len(applied)} content updates"
        body_lines = [f"- {a['title'] or a['id']}" for a in applied]
        commit_msg = subject + "\n\n" + "\n".join(body_lines)
    _git("commit", "-m", commit_msg)
    _git("push", "origin", branch)

    commit_hash = _git("rev-parse", "HEAD")[:7]
    repo_slug = "rodeanroozbehani/OwnersHub---Landing-Page"
    commit_url = f"https://github.com/{repo_slug}/commit/{commit_hash}"
    logger.info("Pushed commit %s", commit_url)
    return commit_url
