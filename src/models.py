"""Data models for news items, configuration, and digest editions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NewsItem:
    """Normalized representation of a fetched news story or repository."""

    id: str
    title: str
    url: str
    source_name: str
    source_type: str  # "hn", "github", "rss"
    summary: str = ""
    published_at: datetime | None = None
    raw_score: int = 0
    secondary_metric: int = 0
    comments_url: str = ""
    default_category: str = "trending_tech"
    language: str = ""

    # Populated during classification and ranking
    assigned_category: str = ""
    relevance_score: float = 0.0
    popularity_score: float = 0.0
    final_score: float = 0.0
    cross_source_hits: int = 1
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize item to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "summary": self.summary,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "raw_score": self.raw_score,
            "secondary_metric": self.secondary_metric,
            "comments_url": self.comments_url,
            "default_category": self.default_category,
            "language": self.language,
            "assigned_category": self.assigned_category,
            "relevance_score": round(self.relevance_score, 3),
            "popularity_score": round(self.popularity_score, 3),
            "final_score": round(self.final_score, 3),
            "matched_keywords": self.matched_keywords,
        }


@dataclass
class CategoryConfig:
    """Configuration for one digest topic category."""

    key: str
    title: str
    keywords: list[str] = field(default_factory=list)
    max_items: int | None = None


@dataclass
class RSSFeedConfig:
    """Configuration for an RSS/Atom feed source."""

    name: str
    url: str
    default_category: str


@dataclass
class HackerNewsConfig:
    """Configuration for Hacker News fetching."""

    enabled: bool = True
    min_points: int = 25
    targeted_min_points: int = 10
    lookback_hours: int = 24
    targeted_queries: list[str] = field(default_factory=list)


@dataclass
class GitHubConfig:
    """Configuration for GitHub Trending and Search fetching."""

    enabled: bool = True
    trending_since: str = "daily"
    search_lookback_days: int = 7
    search_min_stars: int = 15
    search_queries: list[str] = field(default_factory=list)


@dataclass
class DigestSettings:
    """Formatting and selection limits for a digest edition."""

    items_per_category: int = 4
    max_total_items: int = 20
    max_per_source_in_category: int = 2
    dedup_retention_days: int = 14
    description_max_chars: int = 160


@dataclass
class AppConfig:
    """Top-level application configuration loaded from YAML."""

    timezone_offset_hours: int
    schedule_slots: dict[str, str]
    digest: DigestSettings
    categories: list[CategoryConfig]
    hackernews: HackerNewsConfig
    github: GitHubConfig
    rss_feeds: list[RSSFeedConfig]
    rss_lookback_hours: int = 24


@dataclass
class DigestEdition:
    """Generated digest edition ready for archival, site building, and messaging."""

    edition_id: str
    edition_key: str
    edition_label: str
    generated_at: datetime
    categories: list[CategoryConfig]
    items_by_category: dict[str, list[NewsItem]]
    total_fetched: int = 0
    total_after_dedup: int = 0

    @property
    def total_selected(self) -> int:
        return sum(len(items) for items in self.items_by_category.values())

    def to_index_entry(self, markdown_rel_path: str, html_rel_path: str) -> dict[str, Any]:
        """Serialize digest metadata and items for data/digests_index.json."""
        return {
            "edition_id": self.edition_id,
            "edition_key": self.edition_key,
            "edition_label": self.edition_label,
            "generated_at": self.generated_at.isoformat(),
            "markdown_path": markdown_rel_path,
            "html_path": html_rel_path,
            "total_fetched": self.total_fetched,
            "total_after_dedup": self.total_after_dedup,
            "total_selected": self.total_selected,
            "categories": [
                {
                    "key": cat.key,
                    "title": cat.title,
                    "items": [item.to_dict() for item in self.items_by_category.get(cat.key, [])],
                }
                for cat in self.categories
                if self.items_by_category.get(cat.key)
            ],
        }
