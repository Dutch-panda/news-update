"""News and trends fetchers for Hacker News, GitHub, and RSS feeds."""

from __future__ import annotations

import html
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.models import NewsItem

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"\s+")
BARE_URL_RE = re.compile(r"https?://\S+")
DEFAULT_USER_AGENT = "NewsUpdateDigestBot/1.0 (+https://github.com/personal-news-update)"
REQUEST_TIMEOUT_SEC = 15
ENRICH_TIMEOUT_SEC = 4


def clean_summary(raw_html: str | None, max_chars: int = 160, strip_urls: bool = False) -> str:
    """Strip HTML tags (including double-escaped tags), unescape entities, and truncate cleanly."""
    if not raw_html:
        return ""
    unescaped = html.unescape(raw_html)
    text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    text = html.unescape(text)
    if strip_urls:
        text = BARE_URL_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if max_chars > 0 and len(text) > max_chars:
        truncated = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:")
        return f"{truncated}…" if truncated else f"{text[: max_chars - 1]}…"
    return text


def enrich_missing_summaries(items: list[NewsItem], max_chars: int = 160) -> None:
    """Populate empty summaries for selected items from page og:description / meta description."""
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

    for item in items:
        if item.summary and len(item.summary.strip()) >= 20:
            continue
        if not item.url or item.url.startswith("https://news.ycombinator.com/item?"):
            continue
        try:
            resp = session.get(item.url, timeout=ENRICH_TIMEOUT_SEC, allow_redirects=True)
            content_type = resp.headers.get("Content-Type", "").lower()
            if not resp.ok or ("html" not in content_type and content_type):
                continue
            # Decode HTML cleanly as UTF-8 unless charset is explicitly declared, avoiding mojibake
            raw_bytes = resp.content[:65536]
            encoding = resp.encoding if "charset=" in content_type else "utf-8"
            html_snippet = raw_bytes.decode(encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(html_snippet, "html.parser")
            desc = ""
            for selector in (
                'meta[property="og:description"]',
                'meta[name="description"]',
                'meta[name="twitter:description"]',
            ):
                tag = soup.select_one(selector)
                if tag and tag.get("content"):
                    desc = str(tag["content"])
                    break
            cleaned = clean_summary(desc, max_chars=max_chars, strip_urls=True)
            if len(cleaned) >= 20:
                item.summary = cleaned
        except Exception as exc:
            logger.debug("Meta description enrichment skipped for %s: %s", item.url, exc)
