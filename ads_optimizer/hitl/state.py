"""Review session state — stored as JSON files in data/review-sessions/."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewSession:
    def __init__(self, session_dir: Path, session_id: str | None = None) -> None:
        self.session_dir = session_dir
        self.session_id = session_id or str(uuid.uuid4())
        self._path = session_dir / f"{self.session_id}.json"

    @classmethod
    def create(
        cls,
        session_dir: Path,
        changes: list[dict[str, Any]],
        html_source: str,
        review_path: str,
        conversion_readiness_score: int,
        overall_assessment: str,
        parent_session_id: str | None = None,
    ) -> "ReviewSession":
        session_dir.mkdir(parents=True, exist_ok=True)
        session = cls(session_dir)
        data: dict[str, Any] = {
            "session_id": session.session_id,
            "created_at": _utc_now(),
            "status": "pending",
            "parent_session_id": parent_session_id,
            "is_re_review": parent_session_id is not None,
            "html_source": html_source,
            "review_path": review_path,
            "conversion_readiness_score": conversion_readiness_score,
            "overall_assessment": overall_assessment,
            "changes": [
                {
                    **change,
                    "decision": None,
                    "feedback_text": None,
                    "re_review_result": None,
                }
                for change in changes
            ],
            "deployed": False,
            "commit_url": "",
            "deploy_error": "",
        }
        session._write(data)
        return session

    @classmethod
    def load(cls, session_dir: Path, session_id: str) -> "ReviewSession":
        session = cls(session_dir, session_id)
        if not session._path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return session

    def read(self) -> dict[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def update(self, data: dict[str, Any]) -> None:
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self._path)
