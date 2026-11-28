"""Hacker News fetcher using the Algolia HN Search API with Firebase fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.fetchers import DEFAULT_USER_AGENT, REQUEST_TIMEOUT_SEC, clean_summary
from src.models import HackerNewsConfig, NewsItem

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
ALGOLIA_SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
FIREBASE_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def _parse_algolia_hit(hit: dict[str, Any], max_summary_chars: int) -> NewsItem | None:
    object_id = str(hit.get("objectID") or "").strip()
    title = (hit.get("title") or "").strip()
    if not object_id or not title:
        return None

    comments_url = f"https://news.ycombinator.com/item?id={object_id}"
    url = (hit.get("url") or "").strip() or comments_url

    created_ts = hit.get("created_at_i")
    published_at = (
        datetime.fromtimestamp(int(created_ts), tz=timezone.utc)
        if created_ts is not None
        else None
    )

    summary = clean_summary(hit.get("story_text") or "", max_chars=max_summary_chars, strip_urls=True)
    points = int(hit.get("points") or 0)
    num_comments = int(hit.get("num_comments") or 0)

    return NewsItem(
        id=f"hn:{object_id}",
        title=title,
        url=url,
        source_name="Hacker News",
        source_type="hn",
        summary=summary,
        published_at=published_at,
        raw_score=points,
        secondary_metric=num_comments,
        comments_url=comments_url,
        default_category="trending_tech",
    )


def _fetch_firebase_fallback(
    session: requests.Session,
    min_points: int,
    max_summary_chars: int,
) -> list[NewsItem]:
    """Fallback to official Firebase HN API if Algolia is unreachable."""
    items: list[NewsItem] = []
    try:
        resp = session.get(FIREBASE_TOP_URL, timeout=REQUEST_TIMEOUT_SEC)
        resp.raise_for_status()
        ids = (resp.json() or [])[:30]
    except Exception as exc:
        logger.warning("HN Firebase fallback failed: %s", exc)
        return items

    for story_id in ids:
        try:
            r = session.get(FIREBASE_ITEM_URL.format(item_id=story_id), timeout=REQUEST_TIMEOUT_SEC)
            r.raise_for_status()
            data = r.json() or {}
            score = int(data.get("score") or 0)
            title = (data.get("title") or "").strip()
            if not title or score < min_points:
                continue
            comments_url = f"https://news.ycombinator.com/item?id={story_id}"
            published_at = (
                datetime.fromtimestamp(int(data["time"]), tz=timezone.utc)
                if data.get("time")
                else None
            )
            items.append(
                NewsItem(
                    id=f"hn:{story_id}",
                    title=title,
                    url=(data.get("url") or "").strip() or comments_url,
                    source_name="Hacker News",
                    source_type="hn",
                    summary=clean_summary(data.get("text") or "", max_chars=max_summary_chars),
                    published_at=published_at,
                    raw_score=score,
                    secondary_metric=int(data.get("descendants") or 0),
                    comments_url=comments_url,
                    default_category="trending_tech",
                )
            )
        except Exception as exc:
            logger.debug("Failed to fetch HN Firebase item %s: %s", story_id, exc)
    return items


def fetch_hackernews(
    config: HackerNewsConfig,
    max_summary_chars: int = 160,
    lookback_hours: int | None = None,
    include_targeted: bool = True,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Fetch front-page, most popular recent stories, and targeted queries in the lookback window."""
    if not config.enabled:
        return []

    hours = lookback_hours if lookback_hours is not None else config.lookback_hours
    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_dt = ref_now - timedelta(hours=hours)
    cutoff_ts = int(cutoff_dt.timestamp())

    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

    seen_ids: set[str] = set()
    results: list[NewsItem] = []

    def _collect(hits: list[dict[str, Any]]) -> None:
        for hit in hits:
            item = _parse_algolia_hit(hit, max_summary_chars=max_summary_chars)
            if not item or item.id in seen_ids:
                continue
            if item.published_at is not None and item.published_at < cutoff_dt:
                continue
            seen_ids.add(item.id)
            results.append(item)

    algolia_ok = False
    # Use Algolia /search (ranked by popularity/points) rather than /search_by_date for top stories
    requests_spec: list[tuple[str, dict[str, Any]]] = [
        (ALGOLIA_SEARCH_URL, {"tags": "front_page", "hitsPerPage": 50}),
        (
            ALGOLIA_SEARCH_URL,
            {
                "tags": "story",
                "numericFilters": f"created_at_i>{cutoff_ts},points>={config.min_points}",
                "hitsPerPage": 100,
            },
        ),
    ]
    if include_targeted:
        for query in config.targeted_queries:
            requests_spec.append(
                (
                    ALGOLIA_SEARCH_URL,
                    {
                        "query": query,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{cutoff_ts},points>={config.targeted_min_points}",
                        "hitsPerPage": 25,
                    },
                )
            )

    for endpoint, params in requests_spec:
        try:
            resp = session.get(endpoint, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            payload = resp.json() or {}
            _collect(payload.get("hits") or [])
            algolia_ok = True
        except Exception as exc:
            logger.warning("HN Algolia request failed (%s): %s", params.get("query", params.get("tags")), exc)

    if not algolia_ok:
        results.extend(
            _fetch_firebase_fallback(
                session=session,
                min_points=config.min_points,
                max_summary_chars=max_summary_chars,
            )
        )

    logger.info("Fetched %d unique stories from Hacker News", len(results))
    return results
