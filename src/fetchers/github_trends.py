"""GitHub Trending scraper and GitHub Search API fetcher for rising repositories."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from src.fetchers import DEFAULT_USER_AGENT, REQUEST_TIMEOUT_SEC, clean_summary
from src.models import GitHubConfig, NewsItem

logger = logging.getLogger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending"
GITHUB_SEARCH_API_URL = "https://api.github.com/search/repositories"
NUMBER_RE = re.compile(r"[\d,]+")


def _parse_int(text: str) -> int:
    match = NUMBER_RE.search(text or "")
    if not match:
        return 0
    return int(match.group(0).replace(",", ""))


def _fetch_trending_page(
    session: requests.Session,
    since: str,
    max_summary_chars: int,
    now: datetime,
    language: str = "",
) -> list[NewsItem]:
    """Scrape https://github.com/trending (all or language-specific) for trending repositories."""
    items: list[NewsItem] = []
    url = f"{GITHUB_TRENDING_URL}/{language.strip('/')}" if language else GITHUB_TRENDING_URL
    try:
        resp = session.get(
            url,
            params={"since": since},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("GitHub Trending page fetch failed (%s): %s", url, exc)
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    for row in soup.select("article.Box-row"):
        link_el = row.select_one("h2 a")
        if not link_el or not link_el.get("href"):
            continue
        repo_path = link_el["href"].strip().lstrip("/")
        if "/" not in repo_path:
            continue

        desc_el = row.select_one("p")
        description = clean_summary(desc_el.get_text(" ", strip=True) if desc_el else "", max_summary_chars)

        lang_el = row.select_one('[itemprop="programmingLanguage"]')
        language = lang_el.get_text(strip=True) if lang_el else ""

        star_link = row.select_one(f'a[href="/{repo_path}/stargazers"]')
        total_stars = _parse_int(star_link.get_text(strip=True)) if star_link else 0

        today_el = row.select_one("span.d-inline-block.float-sm-right")
        stars_today = _parse_int(today_el.get_text(strip=True)) if today_el else 0

        title = f"{repo_path}"
        if language:
            summary = f"[{language}] {description}" if description else f"[{language}] Trending on GitHub"
        else:
            summary = description or "Trending on GitHub"

        items.append(
            NewsItem(
                id=f"gh:{repo_path}",
                title=title,
                url=f"https://github.com/{repo_path}",
                source_name="GitHub Trending",
                source_type="github",
                summary=clean_summary(summary, max_chars=max_summary_chars),
                published_at=now,
                raw_score=total_stars,
                secondary_metric=stars_today,
                default_category="new_tools",
                language=language,
            )
        )

    return items


def _fetch_search_repos(
    session: requests.Session,
    config: GitHubConfig,
    max_summary_chars: int,
    now: datetime,
) -> list[NewsItem]:
    """Query GitHub Search API for newly created fast-rising repositories."""
    items: list[NewsItem] = []
    cutoff_dt = now - timedelta(days=config.search_lookback_days)
    since_date = cutoff_dt.strftime("%Y-%m-%d")

    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for query in config.search_queries:
        full_query = f"{query} created:>={since_date} stars:>={config.search_min_stars}"
        try:
            resp = session.get(
                GITHUB_SEARCH_API_URL,
                params={"q": full_query, "sort": "stars", "order": "desc", "per_page": 15},
                headers=headers,
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json() or {}
        except Exception as exc:
            logger.warning("GitHub Search API failed for query '%s': %s", query, exc)
            continue

        for repo in payload.get("items") or []:
            full_name = (repo.get("full_name") or "").strip()
            html_url = (repo.get("html_url") or "").strip()
            if not full_name or not html_url:
                continue

            created_str = repo.get("created_at")
            published_at = None
            if created_str:
                try:
                    published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                except ValueError:
                    published_at = None

            if published_at is not None and published_at < cutoff_dt:
                continue

            language = (repo.get("language") or "").strip()
            desc = (repo.get("description") or "").strip()
            topics = repo.get("topics") or []
            topic_suffix = f" ({', '.join(topics[:3])})" if topics else ""
            raw_summary = f"[{language}] {desc}{topic_suffix}" if language else f"{desc}{topic_suffix}"

            stars = int(repo.get("stargazers_count") or 0)
            age_days = (
                max(1.0, (now - published_at).total_seconds() / 86400.0)
                if published_at is not None
                else 1.0
            )
            stars_per_day = round(stars / age_days)

            items.append(
                NewsItem(
                    id=f"gh:{full_name}",
                    title=full_name,
                    url=html_url,
                    source_name="GitHub Search",
                    source_type="github",
                    summary=clean_summary(raw_summary, max_chars=max_summary_chars),
                    published_at=published_at,
                    raw_score=stars,
                    secondary_metric=stars_per_day,
                    default_category="new_tools",
                    language=language,
                )
            )

    return items


def fetch_github(
    config: GitHubConfig,
    max_summary_chars: int = 160,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Fetch trending and newly created fast-rising repositories from GitHub."""
    if not config.enabled:
        return []

    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

    by_id: dict[str, NewsItem] = {}

    for lang in config.trending_languages or [""]:
        for item in _fetch_trending_page(session, config.trending_since, max_summary_chars, ref_now, language=lang):
            if item.id not in by_id:
                by_id[item.id] = item

    if config.search_enabled:
        for item in _fetch_search_repos(session, config, max_summary_chars, ref_now):
            if item.id not in by_id:
                by_id[item.id] = item
            elif len(item.summary) > len(by_id[item.id].summary):
                # Enrich trending summary if Search API has richer topic/description
                by_id[item.id].summary = item.summary

    results = list(by_id.values())
    logger.info("Fetched %d unique repositories from GitHub", len(results))
    return results
