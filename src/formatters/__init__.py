"""Output formatters for Markdown archive, Telegram Bot API, and GitHub Pages site."""

from __future__ import annotations

from src.models import NewsItem


def format_item_metrics(item: NewsItem) -> str:
    """Return a concise human-readable metric string for an item."""
    if item.source_type == "github":
        stars = f"⭐ {item.raw_score:,}"
        if item.secondary_metric > 0 and item.source_name == "GitHub Trending":
            return f"{stars} (+{item.secondary_metric:,} today)"
        return stars
    if item.source_type == "hn":
        return f"▲ {item.raw_score:,} pts · 💬 {item.secondary_metric:,}"
    return ""
