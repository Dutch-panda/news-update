"""YAML configuration loader and validator."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from src.models import (
    AppConfig,
    CategoryConfig,
    DigestSettings,
    GitHubConfig,
    HackerNewsConfig,
    RSSFeedConfig,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load and validate application configuration from YAML."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    env_tz = os.getenv("TIMEZONE_OFFSET_HOURS")
    tz_offset = int(env_tz) if env_tz is not None else int(raw.get("timezone_offset_hours", 4))

    digest_raw = raw.get("digest", {})
    digest_settings = DigestSettings(
        items_per_category=int(digest_raw.get("items_per_category", 4)),
        max_total_items=int(digest_raw.get("max_total_items", 20)),
        max_per_source_in_category=int(digest_raw.get("max_per_source_in_category", 2)),
        dedup_retention_days=int(digest_raw.get("dedup_retention_days", 14)),
        description_max_chars=int(digest_raw.get("description_max_chars", 160)),
    )

    categories = [
        CategoryConfig(
            key=str(cat["key"]),
            title=str(cat["title"]),
            keywords=[str(kw) for kw in cat.get("keywords", [])],
            max_items=int(cat["max_items"]) if cat.get("max_items") is not None else None,
        )
        for cat in raw.get("categories", [])
    ]
    if not categories:
        raise ValueError("Configuration must define at least one category.")

    sources_raw = raw.get("sources", {})
    hn_raw = sources_raw.get("hackernews", {})
    hn_config = HackerNewsConfig(
        enabled=bool(hn_raw.get("enabled", True)),
        min_points=int(hn_raw.get("min_points", 25)),
        targeted_min_points=int(hn_raw.get("targeted_min_points", 10)),
        lookback_hours=int(hn_raw.get("lookback_hours", 24)),
        targeted_queries=[str(q) for q in hn_raw.get("targeted_queries", [])],
    )

    gh_raw = sources_raw.get("github", {})
    gh_config = GitHubConfig(
        enabled=bool(gh_raw.get("enabled", True)),
        trending_since=str(gh_raw.get("trending_since", "daily")),
        trending_languages=[str(lang) for lang in gh_raw.get("trending_languages", ["", "python", "typescript"])],
        search_lookback_days=int(gh_raw.get("search_lookback_days", 3)),
        search_min_stars=int(gh_raw.get("search_min_stars", 50)),
        search_queries=[str(q) for q in gh_raw.get("search_queries", [])],
    )

    rss_feeds = [
        RSSFeedConfig(
            name=str(feed["name"]),
            url=str(feed["url"]),
            default_category=str(feed.get("default_category", "trending_tech")),
        )
        for feed in sources_raw.get("rss_feeds", [])
    ]

    schedule_slots = {
        str(k): str(v)
        for k, v in (raw.get("schedule_slots") or {
            "morning": "Morning Update (08:00)",
            "afternoon": "Afternoon Update (15:00)",
            "night": "Night Update (23:00)",
        }).items()
    }

    rss_lookback_hours = int(sources_raw.get("rss_lookback_hours", 24))

    return AppConfig(
        timezone_offset_hours=tz_offset,
        schedule_slots=schedule_slots,
        digest=digest_settings,
        categories=categories,
        hackernews=hn_config,
        github=gh_config,
        rss_feeds=rss_feeds,
        rss_lookback_hours=rss_lookback_hours,
    )
