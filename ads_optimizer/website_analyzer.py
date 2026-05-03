"""Fetch + parse + screenshot a target website for joint ads/landing-page review."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


@dataclass
class WebsiteReport:
    url: str
    fetched_at_utc: str
    status_code: int
    title: str
    meta_description: str
    headings: dict[str, list[str]]
    body_text_excerpt: str
    cta_links: list[dict[str, str]]
    forms: list[dict[str, Any]]
    formspree_detected: bool
    word_count: int
    snapshot_dir: str
    html_path: str
    screenshot_paths: dict[str, str] = field(default_factory=dict)
    screenshot_error: str | None = None


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def _extract_ctas(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    ctas: list[dict[str, str]] = []
    for tag in soup.find_all(["a", "button"]):
        text = tag.get_text(strip=True)
        if not text or len(text) > 80:
            continue
        href = tag.get("href") or ""
        absolute = urljoin(base_url, href) if href else ""
        ctas.append({
            "tag": tag.name,
            "text": text,
            "href": absolute,
        })
    # Deduplicate while preserving order
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for cta in ctas:
        key = (cta["tag"], cta["text"], cta["href"])
        if key not in seen:
            seen.add(key)
            unique.append(cta)
    return unique[:50]


def _extract_forms(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], bool]:
    forms: list[dict[str, Any]] = []
    formspree = False
    for form in soup.find_all("form"):
        action = (form.get("action") or "").strip()
        method = (form.get("method") or "GET").upper()
        if "formspree" in action.lower() or "formspree" in (form.get("data-form") or "").lower():
            formspree = True
        inputs = []
        for inp in form.find_all(["input", "textarea", "select"]):
            inputs.append({
                "name": inp.get("name") or "",
                "type": inp.get("type") or inp.name,
                "placeholder": inp.get("placeholder") or "",
                "required": inp.has_attr("required"),
            })
        forms.append({
            "action": action,
            "method": method,
            "input_count": len(inputs),
            "inputs": inputs,
        })
    return forms, formspree


def _take_screenshots(
    url: str,
    snapshot_dir: Path,
    viewports: dict[str, tuple[int, int]],
    navigation_timeout_ms: int,
) -> tuple[dict[str, str], str | None]:
    """Capture desktop + mobile screenshots via Playwright. Best-effort."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {}, f"playwright not installed: {exc}"

    try:
        from PIL import Image
    except ImportError as exc:
        return {}, f"pillow not installed: {exc}"

    paths: dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for label, (width, height) in viewports.items():
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    page.set_default_navigation_timeout(navigation_timeout_ms)
                    page.goto(url, wait_until="networkidle")
                    raw_path = snapshot_dir / f"{label}.raw.png"
                    page.screenshot(path=str(raw_path), full_page=True)
                    context.close()

                    # Downscale to fit within 1280×5000 (Anthropic max is 8000px per dimension).
                    final_path = snapshot_dir / f"{label}.png"
                    with Image.open(raw_path) as img:
                        scale = min(1280 / img.width, 5000 / img.height, 1.0)
                        if scale < 1.0:
                            new_size = (int(img.width * scale), int(img.height * scale))
                            img = img.resize(new_size, Image.LANCZOS)
                        img.save(final_path, "PNG", optimize=True)
                    raw_path.unlink(missing_ok=True)
                    paths[label] = str(final_path)
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Playwright screenshot failure: %s", exc)
        return paths, str(exc)

    return paths, None


class WebsiteAnalyzer:
    def __init__(self, config: dict[str, Any]) -> None:
        web = config["website"]
        self.url: str = web["url"]
        self.user_agent: str = web.get("user_agent", "OwnersHubAdsOptimizer/1.0")
        self.request_timeout_s: int = int(web.get("request_timeout_s", 15))
        self.navigation_timeout_ms: int = int(web.get("navigation_timeout_ms", 20000))
        viewports_cfg = web.get("viewports", {}) or {}
        self.viewports: dict[str, tuple[int, int]] = {
            label: (int(dims[0]), int(dims[1]))
            for label, dims in viewports_cfg.items()
            if isinstance(dims, (list, tuple)) and len(dims) == 2
        }

    def analyze(self, snapshot_dir: Path, fetched_at_utc: str) -> WebsiteReport:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        logger.info("website_analyzer: fetching %s", self.url)
        response = requests.get(
            self.url,
            timeout=self.request_timeout_s,
            headers={"User-Agent": self.user_agent},
        )
        html = response.text
        html_path = snapshot_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "")
        meta_desc_tag = soup.find("meta", attrs={"name": "description"})
        meta_description = (meta_desc_tag.get("content") or "").strip() if meta_desc_tag else ""
        headings = {
            tag: [el.get_text(strip=True) for el in soup.find_all(tag) if el.get_text(strip=True)]
            for tag in ("h1", "h2", "h3")
        }
        body_text = _visible_text(soup)
        word_count = len(body_text.split())
        excerpt = body_text[:4000]

        ctas = _extract_ctas(soup, base_url=self.url)
        forms, formspree = _extract_forms(soup)

        screenshot_paths, screenshot_error = _take_screenshots(
            self.url, snapshot_dir, self.viewports, self.navigation_timeout_ms
        )

        return WebsiteReport(
            url=self.url,
            fetched_at_utc=fetched_at_utc,
            status_code=response.status_code,
            title=title,
            meta_description=meta_description,
            headings=headings,
            body_text_excerpt=excerpt,
            cta_links=ctas,
            forms=forms,
            formspree_detected=formspree,
            word_count=word_count,
            snapshot_dir=str(snapshot_dir),
            html_path=str(html_path),
            screenshot_paths=screenshot_paths,
            screenshot_error=screenshot_error,
        )


def report_to_dict(report: WebsiteReport) -> dict[str, Any]:
    return {
        "url": report.url,
        "fetched_at_utc": report.fetched_at_utc,
        "status_code": report.status_code,
        "title": report.title,
        "meta_description": report.meta_description,
        "headings": report.headings,
        "body_text_excerpt": report.body_text_excerpt,
        "cta_links": report.cta_links,
        "forms": report.forms,
        "formspree_detected": report.formspree_detected,
        "word_count": report.word_count,
        "snapshot_dir": report.snapshot_dir,
        "html_path": report.html_path,
        "screenshot_paths": report.screenshot_paths,
        "screenshot_error": report.screenshot_error,
    }
