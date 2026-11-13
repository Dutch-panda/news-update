"""RSS and Atom feed fetcher and parser."""

from __future__ import annotations

import logging
from calendar import timegm
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests

from src.fetchers import DEFAULT_USER_AGENT, REQUEST_TIMEOUT_SEC, clean_summary
from src.models import NewsItem, RSSFeedConfig

logger = logging.getLogger(__name__)

DEFAULT_RSS_LOOKBACK_HOURS = 24


def _extract_entry_datetime(entry: Any) -> datetime | None:
    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_struct:
        return None
    try:
        return datetime.fromtimestamp(timegm(parsed_struct), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _extract_entry_summary(entry: Any, max_chars: int) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        first_content = entry["content"][0]
        if isinstance(first_content, dict):
            raw = first_content.get("value", "")
    return clean_summary(raw, max_chars=max_chars)


def fetch_rss_feeds(
    feeds: list[RSSFeedConfig],
    max_summary_chars: int = 160,
    lookback_hours: int = DEFAULT_RSS_LOOKBACK_HOURS,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Fetch and parse all configured RSS/Atom feeds gracefully."""
    if not feeds:
        return []

    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = ref_now - timedelta(hours=lookback_hours)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }
    )

    items: list[NewsItem] = []

    for feed_cfg in feeds:
        try:
            resp = session.get(feed_cfg.url, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:
            logger.warning("RSS feed '%s' failed (%s): %s", feed_cfg.name, feed_cfg.url, exc)
            continue

        entries = getattr(parsed, "entries", None) or []
        feed_count = 0
        for entry in entries[:50]:
            title = clean_summary(entry.get("title") or "", max_chars=240)
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue

            pub_dt = _extract_entry_datetime(entry)
            if pub_dt is not None and pub_dt < cutoff:
                continue

            entry_id = str(entry.get("id") or link)
            summary = _extract_entry_summary(entry, max_chars=max_summary_chars)

            items.append(
                NewsItem(
                    id=f"rss:{feed_cfg.name}:{entry_id}",
                    title=title,
                    url=link,
                    source_name=feed_cfg.name,
                    source_type="rss",
                    summary=summary,
                    published_at=pub_dt,
                    raw_score=0,
                    secondary_metric=0,
                    default_category=feed_cfg.default_category,
                )
            )
            feed_count += 1

        logger.debug("Fetched %d recent items from RSS '%s'", feed_count, feed_cfg.name)

    logger.info("Fetched %d total items across %d RSS feeds", len(items), len(feeds))
    return items
