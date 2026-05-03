"""Anthropic SDK wrapper for joint ads + landing-page optimisation prompts."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from string import Template
from typing import Any


logger = logging.getLogger(__name__)


REQUIRED_OUTPUT_KEYS = (
    "assumptions",
    "ads_findings",
    "website_findings",
    "recommendations",
    "experiments",
)


class ClaudeError(RuntimeError):
    pass


def _load_template(path: Path) -> Template:
    if not path.exists():
        raise ClaudeError(f"prompt template not found: {path}")
    return Template(path.read_text(encoding="utf-8"))


def _encode_image(path: str) -> dict[str, Any]:
    p = Path(path)
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    media_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _fix_json_strings(text: str) -> str:
    """Escape literal newlines and carriage returns inside JSON string values.

    Claude sometimes emits multi-line HTML verbatim inside JSON strings, which
    breaks json.loads. This walks the text character-by-character so it handles
    escaped quotes correctly without false-positive replacements.
    """
    result: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == "\\":
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == "\n":
            result.append("\\n")
        elif in_string and ch == "\r":
            result.append("\\r")
        else:
            result.append(ch)
    return "".join(result)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a free-form Claude response."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = text[first:last + 1]
    if candidate is None:
        raise ClaudeError("no JSON object found in Claude response")
    candidate = _fix_json_strings(candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"failed to parse JSON: {exc}") from exc


def _validate_output(payload: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_OUTPUT_KEYS if k not in payload]
    if missing:
        raise ClaudeError(f"Claude response missing required keys: {missing}")


class ClaudeClient:
    def __init__(self, config: dict[str, Any]) -> None:
        claude_cfg = config["claude"]
        self.model: str = claude_cfg["model"]
        self.max_tokens: int = int(claude_cfg.get("max_tokens", 4096))
        self.api_key_env: str = claude_cfg["api_key_env"]
        self.json_retry_attempts: int = int(claude_cfg.get("json_retry_attempts", 1))
        self.request_timeout_s: int = int(claude_cfg.get("request_timeout_s", 120))

        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise ClaudeError(
                f"environment variable {self.api_key_env} is empty. "
                "Set it in .env (and load via python-dotenv) or in your shell."
            )
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ClaudeError(f"anthropic package not installed: {exc}") from exc

        self._client = Anthropic(api_key=api_key, timeout=self.request_timeout_s)

    def optimize(
        self,
        *,
        prompt_path: Path,
        ads_data: dict[str, Any],
        website_data: dict[str, Any],
        previous_summary: dict[str, Any] | None,
        ownershub_context: dict[str, Any],
        budget_aud: float,
        screenshot_paths: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        template = _load_template(prompt_path)
        prompt_text = template.safe_substitute(
            OWNERSHUB_CONTEXT=json.dumps(ownershub_context, indent=2),
            DAILY_BUDGET_AUD=str(budget_aud),
            ADS_DATA=json.dumps(ads_data, indent=2, default=str),
            WEBSITE_HTML_SUMMARY=json.dumps(website_data, indent=2),
            PREVIOUS_RUN_SUMMARY=json.dumps(previous_summary or {}, indent=2),
        )

        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        if screenshot_paths:
            for label in ("desktop", "mobile"):
                path = screenshot_paths.get(label)
                if path and Path(path).exists():
                    content_blocks.append({
                        "type": "text",
                        "text": f"[Attached: {label} screenshot of {website_data.get('url', 'site')}]",
                    })
                    content_blocks.append(_encode_image(path))

        payload = self._call_with_retry(content_blocks)

        for attempt in range(self.json_retry_attempts + 1):
            try:
                parsed = _extract_json(payload)
                _validate_output(parsed)
                return parsed
            except ClaudeError as exc:
                if attempt >= self.json_retry_attempts:
                    raise
                logger.warning("Claude output not valid JSON, retrying once: %s", exc)
                fixup_blocks = [{
                    "type": "text",
                    "text": (
                        "Your previous response did not parse as the required JSON schema. "
                        "Re-emit ONLY a single JSON object with these top-level keys: "
                        f"{list(REQUIRED_OUTPUT_KEYS)}. No prose, no code fences."
                    ),
                }]
                payload = self._call_with_retry(fixup_blocks)
        raise ClaudeError("unreachable")

    def review_content(
        self,
        *,
        prompt_path: Path,
        html_summary: dict[str, Any],
        screenshot_paths: dict[str, str] | None = None,
        previous_findings: list[dict[str, Any]] | None = None,
        ads_insights: dict[str, Any] | None = None,
        ownershub_context: dict[str, Any],
        conversion_goal: str,
        max_changes: int = 10,
    ) -> dict[str, Any]:
        """Call Claude with the content-optimiser prompt. Returns the parsed JSON output."""
        template = _load_template(prompt_path)
        ads_insights_text = (
            json.dumps(ads_insights, indent=2) if ads_insights
            else "No ads performance data available for this run."
        )
        prompt_text = template.safe_substitute(
            OWNERSHUB_CONTEXT=json.dumps(ownershub_context, indent=2),
            CONVERSION_GOAL=conversion_goal,
            HTML_SUMMARY=json.dumps(html_summary, indent=2),
            ADS_INSIGHTS=ads_insights_text,
            PREVIOUS_FINDINGS=json.dumps(previous_findings or [], indent=2),
            MAX_CHANGES=str(max_changes),
        )

        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        if screenshot_paths:
            for label in ("desktop", "mobile"):
                path = screenshot_paths.get(label)
                if path and Path(path).exists():
                    content_blocks.append({
                        "type": "text",
                        "text": f"[{label} screenshot of the live landing page]",
                    })
                    content_blocks.append(_encode_image(path))

        required_keys = ("overall_assessment", "conversion_readiness_score", "proposed_changes")
        payload = self._call_with_retry(content_blocks)

        for attempt in range(self.json_retry_attempts + 1):
            try:
                parsed = _extract_json(payload)
                missing = [k for k in required_keys if k not in parsed]
                if missing:
                    raise ClaudeError(f"content review response missing keys: {missing}")
                return parsed
            except ClaudeError as exc:
                if attempt >= self.json_retry_attempts:
                    raise
                logger.warning("Content review output not valid JSON, retrying: %s", exc)
                fixup_text = (
                    "Your previous response did not parse as valid JSON. "
                    "Re-emit your analysis as a SINGLE JSON object with NO prose and NO code fences. "
                    "Required top-level keys and types:\n"
                    '  "overall_assessment": string\n'
                    '  "conversion_readiness_score": integer 1-10\n'
                    '  "proposed_changes": array of objects, each with keys: '
                    "id, priority, section, dimension, effort, current_element, "
                    "proposed_element, reasoning, expected_value, implementation_note\n"
                    '  "proposed_html_changes_summary": string\n'
                    "Ensure all HTML inside JSON strings has double-quotes escaped as \\\" "
                    "and newlines escaped as \\n."
                )
                # Pass full conversation so Claude retains its analysis context.
                conversation = [
                    {"role": "user", "content": content_blocks},
                    {"role": "assistant", "content": [{"type": "text", "text": payload}]},
                    {"role": "user", "content": [{"type": "text", "text": fixup_text}]},
                ]
                payload = self._call_with_messages(conversation)
        raise ClaudeError("unreachable")

    def re_review_change(
        self,
        *,
        prompt_path: Path,
        change: dict[str, Any],
        feedback: str,
    ) -> dict[str, Any]:
        """Call Claude to revise a single change based on reviewer feedback."""
        template = _load_template(prompt_path)
        prompt_text = template.safe_substitute(
            CHANGE_ID=change.get("id", ""),
            SECTION=change.get("section", ""),
            REASONING=change.get("reasoning", ""),
            EXPECTED_VALUE=change.get("expected_value", ""),
            CURRENT_ELEMENT=change.get("current_element", ""),
            PROPOSED_ELEMENT=change.get("proposed_element", ""),
            FEEDBACK=feedback,
        )
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        payload = self._call_with_retry(content_blocks)
        parsed = _extract_json(payload)
        if "proposed_element" not in parsed:
            raise ClaudeError("re-review response missing proposed_element key")
        return parsed

    def _call_with_messages(self, messages: list[dict[str, Any]]) -> str:
        """Call Claude with a full multi-turn messages array."""
        from anthropic import APIError, APITimeoutError, RateLimitError

        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                logger.info("claude_client: calling model=%s (attempt %d/3)", self.model, attempt + 1)
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                )
                text_parts = [
                    block.text for block in response.content
                    if getattr(block, "type", None) == "text"
                ]
                return "\n".join(text_parts)
            except (RateLimitError, APITimeoutError, APIError) as exc:
                last_exc = exc
                logger.warning("claude_client: retryable error (%s), backing off %.1fs", exc, backoff)
                time.sleep(backoff)
                backoff *= 2
        raise ClaudeError(f"Claude call failed after retries: {last_exc}")

    def _call_with_retry(self, content_blocks: list[dict[str, Any]]) -> str:
        return self._call_with_messages([{"role": "user", "content": content_blocks}])
